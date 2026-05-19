"""Streamlit UI for the Forex technical-analysis scanner."""

from __future__ import annotations

import copy
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Literal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.backtest.engine import Backtester
from app.backtest.metrics import calculate_metrics
from app.config.safety import DemoSafetyError, demo_safety_status, ensure_demo_safe_mode
from app.config.settings import AppSettings, LOCAL_SETTINGS_PATH, load_settings, save_settings
from app.core.pipeline import ScannerService
from app.core.types import BacktestResult, DirectionBias, Opportunity, OpportunityStatus, SetupFamily, TradeRecord, TradingStyle
from app.data.providers import MarketDataProvider, build_provider
from app.data.validation import window_for_bars
from app.execution.demo_bot import DemoBotCycleResult, DemoBotService, demo_bot_control_event
from app.execution.demo_bot_config import DemoBotConfig
from app.execution.demo_bot_state import DemoBotLogEntry, DemoBotRuntimeState
from app.execution.models import ExecutionOrder, TradeEvent, TradeEventType
from app.indicators.calculations import add_indicators
from app.indicators.levels import LevelSet, find_key_levels
from app.paper.journal import LEARNING_TAGS, TradeJournalEntry, apply_learning_review, export_trading_journal, journal_learning_summary
from app.paper.reporting import generate_paper_portfolio_report
from app.paper.trading import PaperTradingResult, close_paper_order_manually, submit_signal_to_paper
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run the Streamlit app."""

    configure_logging()
    st.set_page_config(page_title="Forex TA Scanner", layout="wide")
    settings = _load_settings()
    _ensure_safe_mode(settings)
    database = _database(settings)
    provider = _provider(settings)

    st.title("Forex Supervisor")
    st.warning("Outil educatif en mode paper/demo uniquement. Aucun ordre reel n'est envoye. Ceci n'est pas un conseil financier.")
    st.caption("Scanner local, opportunites classees, paper trading, bot demo, journal, backtest et audit pour une demo Forex supervisee.")
    _provider_notice(settings)
    _system_status_panel(settings, database, provider)

    tabs = st.tabs(["Scanner", "Opportunités", "Paper Trading", "Bot Demo", "Backtest", "Journal", "Rapports / Audit"])
    with tabs[0]:
        _scanner_page(settings, provider, database)
    with tabs[1]:
        _opportunities_page(settings, provider, database)
    with tabs[2]:
        _paper_trading_page(settings, database)
    with tabs[3]:
        _bot_demo_page(settings, provider, database)
    with tabs[4]:
        _backtest_page(settings, provider, database)
    with tabs[5]:
        _journal_page(database)
    with tabs[6]:
        _reports_audit_page(settings, database)


def _database(settings: AppSettings) -> Database:
    return Database(settings.database_absolute_path)


def _provider(settings: AppSettings) -> MarketDataProvider:
    return build_provider(settings)


def _ensure_safe_mode(settings: AppSettings) -> None:
    try:
        ensure_demo_safe_mode(settings, context="Streamlit startup")
    except DemoSafetyError as exc:
        st.error(str(exc))
        st.json(demo_safety_status(settings))
        st.stop()


def _load_settings() -> AppSettings:
    try:
        return load_settings()
    except Exception as exc:
        st.error(f"Settings could not be loaded: {exc}")
        st.stop()


def _provider_notice(settings: AppSettings) -> None:
    """Display concise provider and fallback behavior in the sidebar."""

    st.sidebar.caption(f"Configured provider: {settings.provider.name}")
    if settings.provider.name == "synthetic":
        st.sidebar.warning(
            "Demo data mode: deterministic synthetic candles are not broker data. "
            "Demo scenarios include EUR/USD trend pullback, GBP/USD breakout pressure, and USD/CHF range behavior."
        )
    elif settings.provider.name == "auto" and settings.provider.fallback_to_synthetic:
        st.sidebar.info("Auto mode tries MT5 first, Yahoo second, and synthetic demo candles only when development fallback is allowed.")
    elif settings.provider.name == "mt5":
        st.sidebar.info("MT5 mode requires a configured local terminal and MetaTrader5 Python package.")


def _system_status_panel(settings: AppSettings, database: Database, provider: MarketDataProvider) -> None:
    """Show a compact readiness snapshot for local demos."""

    safety = demo_safety_status(settings)
    database_ok = _database_status(database)
    provider_status = _provider_status(settings, provider)
    bot_status = _demo_bot_state().status
    paper_mode = safety.get("EXECUTION_MODE") == "paper" and safety.get("BROKER_MODE") == "paper"
    live_disabled = (
        safety.get("ALLOW_LIVE_TRADING") == "false"
        and safety.get("settings.execution.mode") != "broker_live"
        and not settings.execution_capabilities.broker_live_enabled
        and not settings.broker.live_enabled
    )

    st.markdown("**Etat du systeme**")
    cols = st.columns(5)
    cols[0].metric("Database", database_ok)
    cols[1].metric("Data provider", provider_status)
    cols[2].metric("Paper mode", "actif" if paper_mode else "bloque")
    cols[3].metric("Bot demo", bot_status)
    cols[4].metric("Live trading", "disabled" if live_disabled else "blocked")
    with st.expander("Details demo", expanded=False):
        st.write("Flux de demo: Scanner -> Opportunites -> Ajouter en paper trading -> Bot Demo -> Journal -> Backtest -> Rapports / Audit.")
        st.json(
            {
                "database_path": str(database.path),
                "provider": settings.provider.name,
                "fallback_to_synthetic": settings.provider.fallback_to_synthetic,
                "execution_mode": safety.get("EXECUTION_MODE"),
                "broker_mode": safety.get("BROKER_MODE"),
                "allow_live_trading": safety.get("ALLOW_LIVE_TRADING"),
                "auto_bot_enabled": safety.get("AUTO_BOT_ENABLED"),
            }
        )


def _database_status(database: Database) -> str:
    try:
        database.path.parent.mkdir(parents=True, exist_ok=True)
        database.load_selected_symbols()
    except Exception:
        return "erreur"
    return "OK"


def _provider_status(settings: AppSettings, provider: MarketDataProvider) -> str:
    if settings.provider.name == "auto" and settings.provider.fallback_to_synthetic:
        return "OK/fallback"
    provider_name = provider.__class__.__name__.replace("DataProvider", "")
    return f"OK/{provider_name}"


def _scanner_page(settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
    st.subheader("Scanner")
    st.caption("Selectionnez un style et quelques paires, puis lancez un scan local. Les signaux restent informatifs et paper/demo.")
    default_symbols = database.load_selected_symbols() or settings.symbols
    style = TradingStyle(
        st.selectbox(
            "Style",
            [style.value for style in TradingStyle],
            key="scanner_style",
            format_func=lambda value: value.replace("_", " ").title(),
        )
    )
    symbols = st.multiselect(
        "Paires Forex",
        settings.symbols,
        default=[symbol for symbol in default_symbols if symbol in settings.symbols],
        key="scanner_symbols",
    )
    if st.button("Lancer le scan", type="primary", disabled=not symbols, key="scanner_run"):
        try:
            database.save_selected_symbols(symbols)
            with st.spinner("Analyse des conditions techniques..."):
                report = ScannerService(settings, provider, database).scan(style, symbols)
            st.session_state["last_scan_report"] = report
            st.session_state["last_scan_symbols"] = symbols
            st.session_state["last_scan_style"] = style.value
        except Exception as exc:
            st.error(f"Le scan a échoué avant l'analyse par symbole: {exc}")

    report = st.session_state.get("last_scan_report")
    if report is None:
        st.info("Choisissez un style, des paires Forex, puis lancez le scan.")
        return

    st.caption(f"Dernier scan: {report.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if report.errors:
        st.warning("Certaines paires n'ont pas pu être analysées.")
        st.dataframe(pd.DataFrame([error.model_dump() for error in report.errors]), width="stretch")

    opportunities = report.opportunities
    if not opportunities:
        st.info("Aucune opportunité retournée.")
        return

    _status_summary(opportunities)
    filtered = _filter_opportunities(opportunities, key_prefix="scanner")
    table = _dashboard_opportunity_table(filtered)
    st.dataframe(table, hide_index=True, width="stretch")
    if all(opportunity.status not in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM} for opportunity in opportunities):
        st.info(
            "Aucun setup approuvé pour l'instant. Les lignes detected/watchlist/rejected restent visibles pour expliquer ce qui manque."
        )


def _opportunities_page(settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
    st.subheader("Opportunités")
    st.caption("Lisez le score, les niveaux et la raison du statut avant tout ajout en paper trading.")
    report = st.session_state.get("last_scan_report")
    if report is None or not report.opportunities:
        st.info("Lancez un scan pour afficher les setups.")
        return

    opportunities = _ranked_opportunities(report.opportunities)
    st.dataframe(_dashboard_opportunity_table(opportunities[:12]), hide_index=True, width="stretch")
    selected_index = st.selectbox(
        "Setup à examiner",
        list(range(len(opportunities))),
        format_func=lambda idx: _opportunity_label(opportunities[idx]),
        key="opportunities_selected",
    )
    selected = opportunities[selected_index]
    _opportunity_details(selected)
    _paper_submit_panel(settings, database, selected)
    _chart_panel(settings, provider, selected)


def _paper_submit_panel(settings: AppSettings, database: Database, opportunity: Opportunity) -> None:
    executable = opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}
    if executable:
        notes = st.text_area("Notes paper", key=f"paper_notes_{opportunity.symbol}_{opportunity.setup_subtype.value}", height=80)
        if st.button("Ajouter en paper trading", type="primary", key=f"paper_send_{opportunity.symbol}_{opportunity.setup_subtype.value}"):
            try:
                submission = submit_signal_to_paper(opportunity, settings=settings, database=database, source="manual", notes=notes)
                if submission.order:
                    st.success(f"Trade paper cree: {submission.order.order_id}")
                elif submission.reasons:
                    st.warning("Setup bloque par les garde-fous paper: " + "; ".join(submission.reasons))
                else:
                    st.info("Aucun ordre cree pour ce setup.")
            except Exception as exc:
                st.error(f"Envoi paper impossible: {exc}")
    else:
        st.button("Ajouter en paper trading", disabled=True, key=f"paper_send_disabled_{opportunity.symbol}_{opportunity.setup_subtype.value}")
        st.caption("Seuls les setups approved ou premium peuvent être envoyés en paper trading.")


def _paper_trading_page(settings: AppSettings, database: Database) -> None:
    st.subheader("Paper Trading")
    st.caption("Suivez les trades simules, ajoutez des notes et fermez manuellement sans aucun broker externe.")
    orders = database.load_paper_orders()
    blocks = database.load_paper_blocks()
    events = database.load_trade_events()
    open_orders = [order for order in orders if order.is_open]
    closed_orders = [order for order in orders if not order.is_open]

    metric_cols = st.columns(4)
    metric_cols[0].metric("Ouvertes", len(open_orders))
    metric_cols[1].metric("Fermées", len(closed_orders))
    metric_cols[2].metric("Bloquées", len(blocks))
    metric_cols[3].metric("PnL simulé", f"{sum(order.realized_pnl or 0.0 for order in closed_orders):.2f}")

    st.markdown("**Tous les trades paper**")
    _orders_dataframe(orders, empty_message="Aucun trade paper cree.")
    st.markdown("**Positions ouvertes**")
    _orders_dataframe(open_orders, empty_message="Aucune position ouverte ou opportunité pending.")
    if open_orders:
        selected_id = st.selectbox(
            "Trade a gerer",
            [order.order_id for order in open_orders],
            format_func=lambda order_id: _paper_order_label(next(order for order in open_orders if order.order_id == order_id)),
            key="paper_manage_order",
        )
        selected_order = next(order for order in open_orders if order.order_id == selected_id)
        col_price, col_button = st.columns([2, 1])
        close_price = col_price.number_input(
            "Prix de fermeture manuel",
            min_value=0.00001,
            value=float(selected_order.simulated_entry or selected_order.request.entry_price),
            step=0.0001,
            format="%.5f",
            key=f"paper_close_price_{selected_order.order_id}",
        )
        close_notes = st.text_area("Notes", key=f"paper_close_notes_{selected_order.order_id}", height=80)
        if col_button.button("Fermer manuellement", type="primary", key=f"paper_close_{selected_order.order_id}"):
            try:
                closed = close_paper_order_manually(selected_order, settings=settings, database=database, exit_price=close_price, notes=close_notes)
                st.success(f"Trade paper ferme manuellement: {closed.order_id}")
                st.rerun()
            except Exception as exc:
                st.error(f"Fermeture manuelle impossible: {exc}")
        st.markdown("**Evenements du trade selectionne**")
        _events_dataframe([event for event in events if event.trade_id == selected_order.order_id])
    st.markdown("**Positions fermées**")
    _orders_dataframe(closed_orders, empty_message="Aucune position fermée.")
    st.markdown("**Événements récents**")
    _events_dataframe(events)


def _bot_demo_page(settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
    st.subheader("Bot Demo")
    st.warning("Mode demo uniquement. Aucun ordre reel n'est envoye.")
    st.caption("AUTO_BOT_ENABLED=false par defaut: le bot ne demarre jamais au lancement de Streamlit.")
    config = DemoBotConfig.from_settings(settings)
    state = _demo_bot_state()
    default_symbols = [symbol for symbol in ["EUR/USD", "GBP/USD", "USD/CHF"] if symbol in settings.symbols] or settings.symbols[:3]
    style = TradingStyle(
        st.selectbox(
            "Style demo",
            [style.value for style in TradingStyle],
            index=[style.value for style in TradingStyle].index(TradingStyle.DAY_TRADING.value),
            format_func=lambda value: value.replace("_", " ").title(),
            key="bot_demo_style",
        )
    )
    symbols = st.multiselect("Paires demo", settings.symbols, default=default_symbols, key="bot_demo_symbols")

    metric_cols = st.columns(5)
    metric_cols[0].metric("Statut", state.status)
    metric_cols[1].metric("Intervalle", f"{config.interval_seconds}s")
    metric_cols[2].metric("Score min", f"{config.min_score:.0f}")
    metric_cols[3].metric("RR min", f"{config.min_rr:.2f}")
    metric_cols[4].metric("Max ouverts", config.max_open_trades)

    st.markdown("**Configuration actuelle**")
    st.dataframe(_demo_bot_config_table(config), hide_index=True, width="stretch")

    control_cols = st.columns(3)
    if control_cols[0].button("Start Demo Bot", type="primary", disabled=state.running or not symbols, key="bot_demo_start"):
        state = state.start()
        _set_demo_bot_state(state)
        _save_demo_bot_control_event(database, TradeEventType.DEMO_BOT_STARTED, "running", "operator clicked Start Demo Bot")
        try:
            with st.spinner("Premier cycle demo paper en cours..."):
                result = _run_demo_bot_cycle(settings, provider, database, style, symbols)
            st.success(f"Demo bot RUNNING. Premier cycle termine: {result.orders_created} trades paper crees.")
        except Exception as exc:
            state = _state_with_logs(_demo_bot_state(), [f"Premier cycle impossible: {exc}"], level="error")
            _set_demo_bot_state(state)
            st.error(f"Demo bot demarre, mais le premier cycle est impossible: {exc}")
    if control_cols[1].button("Stop Demo Bot", disabled=not state.running, key="bot_demo_stop"):
        state = state.stop()
        _set_demo_bot_state(state)
        _save_demo_bot_control_event(database, TradeEventType.DEMO_BOT_STOPPED, "stopped", "operator clicked Stop Demo Bot")
        st.info("Demo bot STOPPED.")
    if control_cols[2].button("Run one cycle", disabled=not symbols, key="bot_demo_run_one_cycle"):
        try:
            with st.spinner("Cycle demo: scan, filtres, garde-fous, paper trading..."):
                result = _run_demo_bot_cycle(settings, provider, database, style, symbols)
            st.success(f"Cycle termine: {result.opportunities} setups, {result.orders_created} trades paper crees.")
        except Exception as exc:
            st.error(f"Cycle demo impossible: {exc}")

    state = _demo_bot_state()
    if state.running and state.due_for_cycle(config.interval_seconds) and symbols:
        try:
            with st.spinner("Cycle automatique demo en cours..."):
                result = _run_demo_bot_cycle(settings, provider, database, style, symbols)
            st.success(f"Cycle auto termine: {result.orders_created} trades paper crees.")
        except Exception as exc:
            state = _state_with_logs(_demo_bot_state(), [f"Cycle auto impossible: {exc}"], level="error")
            _set_demo_bot_state(state)
            st.error(f"Cycle auto impossible: {exc}")

    last_result = st.session_state.get("last_demo_bot_cycle")
    if isinstance(last_result, DemoBotCycleResult):
        st.markdown("**Dernieres decisions**")
        st.dataframe(_demo_bot_decisions_table(last_result), hide_index=True, width="stretch")

    bot_events = _demo_bot_events(database.load_trade_events())
    st.markdown("**Derniers logs**")
    _demo_bot_logs_dataframe(_demo_bot_state().logs)
    st.markdown("**Evenements audit bot**")
    _events_dataframe(bot_events)
    st.markdown("**Trades paper crees par le bot**")
    order_ids = _demo_bot_created_order_ids(bot_events)
    bot_orders = [order for order in database.load_paper_orders() if order.order_id in order_ids]
    _orders_dataframe(bot_orders, empty_message="Aucun trade paper cree par le bot.")


def _demo_bot_state() -> DemoBotRuntimeState:
    raw = st.session_state.get("demo_bot_state")
    if isinstance(raw, DemoBotRuntimeState):
        return raw
    if isinstance(raw, dict):
        return DemoBotRuntimeState.model_validate(raw)
    state = DemoBotRuntimeState()
    st.session_state["demo_bot_state"] = state
    return state


def _set_demo_bot_state(state: DemoBotRuntimeState) -> None:
    st.session_state["demo_bot_state"] = state


def _run_demo_bot_cycle(
    settings: AppSettings,
    provider: MarketDataProvider,
    database: Database,
    style: TradingStyle,
    symbols: list[str],
) -> DemoBotCycleResult:
    result = DemoBotService(settings, provider, database).run_cycle(style, symbols)
    state = _demo_bot_state().mark_cycle(f"Cycle {result.cycle_id} termine: {result.orders_created} trades paper.")
    state = _state_with_logs(state, result.logs)
    _set_demo_bot_state(state)
    st.session_state["last_demo_bot_cycle"] = result
    return result


def _state_with_logs(state: DemoBotRuntimeState, messages: list[str], *, level: str = "info") -> DemoBotRuntimeState:
    entries = [DemoBotLogEntry(level=level, message=message) for message in reversed(messages)]
    return state.model_copy(update={"logs": [*entries, *state.logs][:100]})


def _save_demo_bot_control_event(database: Database, event_type: TradeEventType, status: str, reason: str) -> None:
    database.save_trade_events([demo_bot_control_event(event_type, status, reason)])


def _demo_bot_config_table(config: DemoBotConfig) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"setting": "AUTO_BOT_ENABLED", "value": str(config.auto_bot_enabled).lower()},
            {"setting": "AUTO_BOT_INTERVAL_SECONDS", "value": str(config.interval_seconds)},
            {"setting": "AUTO_BOT_MIN_SCORE", "value": f"{config.min_score:.1f}"},
            {"setting": "AUTO_BOT_ALLOWED_STATUSES", "value": ", ".join(config.allowed_statuses)},
            {"setting": "AUTO_BOT_MAX_OPEN_TRADES", "value": str(config.max_open_trades)},
            {"setting": "AUTO_BOT_MAX_TRADES_PER_DAY", "value": str(config.max_trades_per_day)},
            {"setting": "AUTO_BOT_COOLDOWN_MINUTES", "value": f"{config.cooldown_minutes:.1f}"},
            {"setting": "AUTO_BOT_MIN_RR", "value": f"{config.min_rr:.2f}"},
        ]
    )


def _demo_bot_decisions_table(result: DemoBotCycleResult) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": decision.symbol,
                "status": decision.status,
                "setup": decision.setup_subtype,
                "decision": "accepted" if decision.accepted else "rejected",
                "score": decision.final_score,
                "rr": decision.risk_reward,
                "orders": ", ".join(decision.order_ids),
                "reason": "; ".join(decision.reasons),
            }
            for decision in result.decisions
        ]
    )


def _demo_bot_logs_dataframe(logs: list[DemoBotLogEntry]) -> None:
    if not logs:
        st.info("Aucun log bot pour cette session.")
        return
    rows = [
        {
            "time": entry.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "level": entry.level,
            "message": entry.message,
        }
        for entry in logs[:50]
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _demo_bot_events(events: list[TradeEvent]) -> list[TradeEvent]:
    return [event for event in events if event.event_type.value.startswith("demo_bot_")]


def _demo_bot_created_order_ids(events: list[TradeEvent]) -> set[str]:
    order_ids: set[str] = set()
    for event in events:
        if event.event_type != TradeEventType.DEMO_BOT_DECISION_ACCEPTED:
            continue
        if event.trade_id:
            order_ids.add(event.trade_id)
        raw_order_ids = event.payload.get("order_ids")
        if isinstance(raw_order_ids, str):
            order_ids.update(order_id.strip() for order_id in raw_order_ids.split(",") if order_id.strip())
    return order_ids


def _journal_page(database: Database) -> None:
    st.subheader("Journal")
    st.caption("Ajoutez tags, emotion, lecon et notes pour apprendre des trades paper/demo.")
    orders = database.load_paper_orders()
    blocks = database.load_paper_blocks()
    entries = database.rebuild_trading_journal() if (orders or blocks) else database.load_journal_entries()
    summary = journal_learning_summary(entries)
    summary_cols = st.columns(4)
    summary_cols[0].metric("Trades paper", int(summary["paper_trades"]))
    summary_cols[1].metric("Win rate paper", f"{float(summary['win_rate']):.1f}%")
    summary_cols[2].metric("Expectancy approx.", f"{float(summary['expectancy_r']):.2f} R")
    frequent_errors = summary["frequent_errors"]
    summary_cols[3].metric("Erreurs taguees", len(frequent_errors))

    st.markdown("**Entrées journal**")
    _journal_dataframe(entries)
    if frequent_errors:
        st.markdown("**Erreurs frequentes**")
        st.dataframe(pd.DataFrame([{"tag": tag, "count": count} for tag, count in frequent_errors]), hide_index=True, width="stretch")

    if orders:
        st.markdown("**Edition des notes**")
        selected_id = st.selectbox(
            "Trade a annoter",
            [order.order_id for order in orders],
            format_func=lambda order_id: _paper_order_label(next(order for order in orders if order.order_id == order_id)),
            key="journal_edit_trade",
        )
        selected_order = next(order for order in orders if order.order_id == selected_id)
        selected_entry = next((entry for entry in entries if entry.trade_id == selected_id), None)
        selected_tags = st.multiselect(
            "Tags",
            LEARNING_TAGS,
            default=selected_entry.mistake_tags if selected_entry else [],
            key=f"journal_tags_{selected_id}",
        )
        lesson = st.text_area("Lesson", value=selected_entry.lesson or "" if selected_entry else "", key=f"journal_lesson_{selected_id}", height=80)
        emotion = st.text_input("Emotion", value=selected_entry.emotion or "" if selected_entry else "", key=f"journal_emotion_{selected_id}")
        notes = st.text_area("Notes", value=selected_entry.notes or "" if selected_entry else "", key=f"journal_notes_{selected_id}", height=100)
        if st.button("Enregistrer le journal", type="primary", key=f"journal_save_{selected_id}"):
            updated = apply_learning_review(selected_order, mistake_tags=selected_tags, lesson=lesson, emotion=emotion, notes=notes)
            database.save_paper_orders([updated])
            database.rebuild_trading_journal()
            st.success("Journal mis a jour.")
            st.rerun()

    if st.button("Exporter CSV journal", key="journal_export_csv"):
        outputs = export_trading_journal(orders, blocks, Path("reports/journal"))
        st.success("Journal exporte.")
        st.dataframe(_outputs_table(outputs), hide_index=True, width="stretch")

    st.markdown("**Événements d'audit paper**")
    _events_dataframe(database.load_trade_events())


def _reports_audit_page(settings: AppSettings, database: Database) -> None:
    st.subheader("Rapports / Audit")
    st.caption("Consultez le verrou de securite, les exports paper et le journal d'audit local.")
    orders = database.load_paper_orders()
    blocks = database.load_paper_blocks()
    events = database.load_trade_events()
    journals = database.load_journal_entries()

    metric_cols = st.columns(4)
    metric_cols[0].metric("Ordres paper", len(orders))
    metric_cols[1].metric("Blocages paper", len(blocks))
    metric_cols[2].metric("Événements", len(events))
    metric_cols[3].metric("Journal", len(journals))

    st.markdown("**Verrou paper/demo**")
    st.dataframe(pd.DataFrame([demo_safety_status(settings)]), hide_index=True, width="stretch")

    col_report, col_journal = st.columns(2)
    with col_report:
        if st.button("Générer rapport paper", key="generate_paper_report"):
            outputs = generate_paper_portfolio_report(orders, blocks, Path("reports/paper"))
            st.success("Rapport paper généré.")
            st.dataframe(_outputs_table(outputs), hide_index=True, width="stretch")
    with col_journal:
        if st.button("Exporter journal", key="export_journal_report"):
            outputs = export_trading_journal(orders, blocks, Path("reports/journal"))
            st.success("Journal exporté.")
            st.dataframe(_outputs_table(outputs), hide_index=True, width="stretch")


def _backtest_page(settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
    st.subheader("Backtest")
    st.warning("Backtest simplifié. Les résultats passés ne garantissent aucune performance future.")
    with st.form("backtest_form"):
        symbol = st.selectbox("Paire", settings.symbols, index=0, key="backtest_symbol")
        style = TradingStyle(
            st.selectbox(
                "Style",
                [style.value for style in TradingStyle],
                key="backtest_style",
                format_func=lambda value: value.replace("_", " ").title(),
            )
        )
        setup_options = ["all", *[family.value for family in SetupFamily if family != SetupFamily.NO_TRADE]]
        setup_value = st.selectbox("Setup family", setup_options, format_func=lambda value: value.replace("_", " ").title())
        setup_filter: SetupFamily | Literal["all"] = "all" if setup_value == "all" else SetupFamily(setup_value)

        today = date.today()
        default_start = today.replace(year=today.year - 1)
        col_start, col_end = st.columns(2)
        with col_start:
            start_day = st.date_input("Début", value=default_start)
        with col_end:
            end_day = st.date_input("Fin", value=today)
        col_score, col_capital, col_risk = st.columns(3)
        min_score = col_score.slider("Score minimum", 0.0, 100.0, 0.0, 1.0)
        initial_capital = col_capital.number_input("Capital initial fictif", min_value=100.0, value=10_000.0, step=500.0)
        risk_pct = col_risk.number_input("Risque par trade fictif (%)", min_value=0.1, max_value=20.0, value=1.0, step=0.1)
        submitted = st.form_submit_button("Run backtest", type="primary")

    if submitted:
        start_dt = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_day, time.max, tzinfo=timezone.utc)
        if start_dt >= end_dt:
            st.error("La période de backtest doit avoir une date de fin après la date de début.")
            return
        try:
            with st.spinner("Running historical test..."):
                result = Backtester(settings, provider, database).run([symbol], style, setup_filter, start_dt, end_dt)
            st.session_state["last_backtest_result"] = result
            st.session_state["last_backtest_min_score"] = float(min_score)
            st.session_state["last_backtest_initial_capital"] = float(initial_capital)
            st.session_state["last_backtest_risk_pct"] = float(risk_pct)
        except Exception as exc:
            st.error(f"Provider indisponible ou données incomplètes: {exc}")

    result = st.session_state.get("last_backtest_result")
    if result is None:
        return

    active_min_score = float(st.session_state.get("last_backtest_min_score", 0.0))
    active_initial_capital = float(st.session_state.get("last_backtest_initial_capital", 10_000.0))
    active_risk_pct = float(st.session_state.get("last_backtest_risk_pct", 1.0))
    filtered_trades = _filter_backtest_trades(result.trades, active_min_score)
    metrics = calculate_metrics(filtered_trades)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Trades", metrics.number_of_trades)
    metric_cols[1].metric("Win rate", f"{metrics.win_rate:.2f}%")
    metric_cols[2].metric("Profit factor", f"{metrics.profit_factor:.2f}")
    metric_cols[3].metric("Expectancy", f"{metrics.expectancy:.3f} R")

    metric_cols = st.columns(4)
    metric_cols[0].metric("Average R", f"{metrics.expectancy:.3f} R")
    metric_cols[1].metric("Avg win/loss", f"{metrics.average_win:.2f} / {metrics.average_loss:.2f} R")
    metric_cols[2].metric("Max drawdown", f"{metrics.max_drawdown:.3f} R")
    metric_cols[3].metric("Capital fictif", f"{active_initial_capital + sum(trade.net_r for trade in filtered_trades) * active_initial_capital * active_risk_pct / 100.0:.2f}")

    equity = _backtest_equity_frame(filtered_trades, result.start, active_initial_capital, active_risk_pct)
    if not equity.empty:
        st.plotly_chart(
            go.Figure(
                data=[
                    go.Scatter(x=equity["time"], y=equity["equity_r"], mode="lines", name="Equity R"),
                    go.Scatter(x=equity["time"], y=equity["equity"], mode="lines", name="Capital fictif"),
                ]
            ).update_layout(
                height=360,
                margin={"l": 20, "r": 20, "t": 20, "b": 20},
            ),
            width="stretch",
        )

    if filtered_trades:
        st.markdown("**Setup families les plus performantes**")
        st.dataframe(_backtest_family_performance(filtered_trades), hide_index=True, width="stretch")
        col_best, col_worst = st.columns(2)
        with col_best:
            st.markdown("**Meilleurs trades simulés**")
            st.dataframe(_backtest_trade_table(sorted(filtered_trades, key=lambda trade: trade.net_r, reverse=True)[:5]), hide_index=True, width="stretch")
        with col_worst:
            st.markdown("**Pires trades simulés**")
            st.dataframe(_backtest_trade_table(sorted(filtered_trades, key=lambda trade: trade.net_r)[:5]), hide_index=True, width="stretch")
        st.markdown("**Trades simulés**")
        st.dataframe(_backtest_trade_table(filtered_trades), hide_index=True, width="stretch")
    else:
        st.info(
            "Aucun trade: pas assez de données exploitables, aucun signal n'a passé les filtres, ou le score minimum est trop strict. "
            "Essayez une période plus longue, EUR/USD en données synthétiques, ou un score minimum plus bas."
        )
    if result.limitations:
        provider_warnings = [item for item in result.limitations if "skipped because" in item]
        if provider_warnings:
            st.warning("Données/provider: " + " ".join(provider_warnings))
        st.info("Backtest limitations: " + " ".join(result.limitations))


def _filter_backtest_trades(trades: list[TradeRecord], min_score: float) -> list[TradeRecord]:
    return [trade for trade in trades if (trade.final_score or 0.0) >= min_score]


def _backtest_equity_frame(trades: list[TradeRecord], start: datetime, initial_capital: float, risk_pct: float) -> pd.DataFrame:
    risk_amount = initial_capital * risk_pct / 100.0
    rows: list[dict[str, float | datetime]] = [{"time": start, "equity_r": 0.0, "equity": initial_capital}]
    cumulative_r = 0.0
    for trade in sorted(trades, key=lambda item: item.exit_time):
        cumulative_r += trade.net_r
        rows.append(
            {
                "time": trade.exit_time,
                "equity_r": round(cumulative_r, 4),
                "equity": round(initial_capital + cumulative_r * risk_amount, 2),
            }
        )
    return pd.DataFrame(rows)


def _backtest_family_performance(trades: list[TradeRecord]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for family in sorted({trade.setup_family for trade in trades}, key=lambda item: item.value):
        family_trades = [trade for trade in trades if trade.setup_family == family]
        metrics = calculate_metrics(family_trades)
        rows.append(
            {
                "setup_family": family.value,
                "trades": metrics.number_of_trades,
                "win_rate": metrics.win_rate,
                "expectancy_r": metrics.expectancy,
                "total_r": round(sum(trade.net_r for trade in family_trades), 4),
                "profit_factor": metrics.profit_factor,
            }
        )
    return pd.DataFrame(rows).sort_values(["expectancy_r", "total_r"], ascending=False)


def _backtest_trade_table(trades: list[TradeRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": trade.symbol,
                "family": trade.setup_family.value,
                "subtype": trade.setup_subtype.value,
                "direction": trade.direction.value,
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "net_r": trade.net_r,
                "gross_r": trade.gross_r,
                "final_score": trade.final_score,
                "exit_reason": trade.exit_reason,
                "outcome": trade.outcome.value if trade.outcome else "",
                "mae": trade.mae,
                "mfe": trade.mfe,
            }
            for trade in trades
        ]
    )


def _settings_page(settings: AppSettings, database: Database) -> None:
    st.subheader("Settings")
    st.caption(f"Validated local config path: {LOCAL_SETTINGS_PATH}")
    payload = copy.deepcopy(settings.model_dump(mode="json"))
    provider_name = st.selectbox("Provider", ["auto", "yahoo", "synthetic", "mt5"], index=["auto", "yahoo", "synthetic", "mt5"].index(settings.provider.name))
    payload["provider"]["name"] = provider_name
    payload["provider"]["environment"] = st.selectbox(
        "Environment",
        ["development", "test", "production"],
        index=["development", "test", "production"].index(settings.provider.environment),
    )
    payload["provider"]["fallback_to_synthetic"] = st.checkbox("Fallback to deterministic development data", value=settings.provider.fallback_to_synthetic)
    payload["provider"]["allow_synthetic_in_production"] = st.checkbox(
        "Allow synthetic provider in production",
        value=settings.provider.allow_synthetic_in_production,
    )

    st.markdown("**Scoring Weights**")
    weight_cols = st.columns(4)
    for idx, key in enumerate(settings.weights.as_dict()):
        with weight_cols[idx % 4]:
            payload["weights"][key] = st.number_input(key.replace("_", " ").title(), min_value=0.0, value=float(payload["weights"][key]), step=1.0)

    st.markdown("**Score Layer Weights**")
    layer_cols = st.columns(4)
    for idx, key in enumerate(settings.layer_weights.as_dict()):
        with layer_cols[idx % 4]:
            payload["layer_weights"][key] = st.number_input(
                key.replace("_", " ").title(),
                min_value=0.0,
                value=float(payload["layer_weights"][key]),
                step=0.01,
                key=f"layer_{key}",
            )

    st.markdown("**Context And Empirical Calibration**")
    context_cols = st.columns(4)
    payload["context"]["minimum_data_quality"] = context_cols[0].number_input("Minimum data quality", min_value=0.0, max_value=100.0, value=float(payload["context"]["minimum_data_quality"]), step=1.0)
    payload["context"]["dead_session_penalty"] = context_cols[1].number_input("Dead session penalty", min_value=0.0, max_value=50.0, value=float(payload["context"]["dead_session_penalty"]), step=1.0)
    payload["empirical"]["neutral_score"] = context_cols[2].number_input("Neutral empirical score", min_value=0.0, max_value=100.0, value=float(payload["empirical"]["neutral_score"]), step=1.0)
    payload["empirical"]["minimum_samples"] = int(context_cols[3].number_input("Minimum empirical samples", min_value=1, max_value=10000, value=int(payload["empirical"]["minimum_samples"]), step=1))
    empirical_cols = st.columns(3)
    payload["empirical"]["min_condition_samples"] = int(empirical_cols[0].number_input("Minimum condition samples", min_value=1, max_value=10000, value=int(payload["empirical"]["min_condition_samples"]), step=1))
    payload["empirical"]["shrinkage_samples"] = int(empirical_cols[1].number_input("Shrinkage samples", min_value=1, max_value=10000, value=int(payload["empirical"]["shrinkage_samples"]), step=1))
    payload["empirical"]["max_adjustment"] = empirical_cols[2].number_input("Maximum empirical adjustment", min_value=0.0, max_value=50.0, value=float(payload["empirical"]["max_adjustment"]), step=1.0)

    st.markdown("**Approval Gates**")
    approval_cols = st.columns(4)
    payload["approval"]["minimum_execution_score"] = approval_cols[0].number_input("Minimum execution score", min_value=0.0, max_value=100.0, value=float(payload["approval"]["minimum_execution_score"]), step=1.0)
    payload["approval"]["minimum_context_score"] = approval_cols[1].number_input("Minimum context score", min_value=0.0, max_value=100.0, value=float(payload["approval"]["minimum_context_score"]), step=1.0)
    payload["approval"]["minimum_empirical_score"] = approval_cols[2].number_input("Minimum empirical score", min_value=0.0, max_value=100.0, value=float(payload["approval"]["minimum_empirical_score"]), step=1.0)
    payload["approval"]["premium_final_score"] = approval_cols[3].number_input("Premium final score", min_value=0.0, max_value=100.0, value=float(payload["approval"]["premium_final_score"]), step=1.0)
    approval_quality_cols = st.columns(4)
    payload["approval"]["minimum_data_quality_score"] = approval_quality_cols[0].number_input("Minimum data quality score", min_value=0.0, max_value=100.0, value=float(payload["approval"]["minimum_data_quality_score"]), step=1.0)
    payload["approval"]["minimum_activation_quality"] = approval_quality_cols[1].number_input("Minimum activation quality", min_value=0.0, max_value=100.0, value=float(payload["approval"]["minimum_activation_quality"]), step=1.0)
    payload["approval"]["minimum_invalidation_quality"] = approval_quality_cols[2].number_input("Minimum invalidation quality", min_value=0.0, max_value=100.0, value=float(payload["approval"]["minimum_invalidation_quality"]), step=1.0)
    payload["approval"]["premium_data_quality_score"] = approval_quality_cols[3].number_input("Premium data quality score", min_value=0.0, max_value=100.0, value=float(payload["approval"]["premium_data_quality_score"]), step=1.0)
    premium_quality_cols = st.columns(2)
    payload["approval"]["premium_activation_quality"] = premium_quality_cols[0].number_input("Premium activation quality", min_value=0.0, max_value=100.0, value=float(payload["approval"]["premium_activation_quality"]), step=1.0)
    payload["approval"]["premium_invalidation_quality"] = premium_quality_cols[1].number_input("Premium invalidation quality", min_value=0.0, max_value=100.0, value=float(payload["approval"]["premium_invalidation_quality"]), step=1.0)

    st.markdown("**Paper Execution And Portfolio Guardrails**")
    execution_cols = st.columns(4)
    payload["execution"]["mode"] = execution_cols[0].selectbox("Execution mode", ["disabled", "paper"], index=["disabled", "paper"].index(payload["execution"]["mode"]))
    payload["execution"]["default_quantity_units"] = execution_cols[1].number_input("Paper quantity units", min_value=0.01, max_value=1_000_000.0, value=float(payload["execution"]["default_quantity_units"]), step=1.0)
    payload["execution"]["estimated_slippage_pips"] = execution_cols[2].number_input("Estimated slippage pips", min_value=0.0, max_value=20.0, value=float(payload["execution"]["estimated_slippage_pips"]), step=0.1)
    payload["execution"]["activation_timeout_bars"] = int(execution_cols[3].number_input("Activation timeout bars", min_value=1, max_value=500, value=int(payload["execution"]["activation_timeout_bars"]), step=1))
    execution_more_cols = st.columns(4)
    payload["execution"]["spread_aware_fills"] = execution_more_cols[0].checkbox("Spread-aware paper fills", value=bool(payload["execution"]["spread_aware_fills"]))
    payload["execution"]["move_stop_to_breakeven_after_tp1"] = execution_more_cols[1].checkbox("Move stop after TP1", value=bool(payload["execution"]["move_stop_to_breakeven_after_tp1"]))
    payload["execution"]["cancel_on_invalidation_before_activation"] = execution_more_cols[2].checkbox("Cancel on pre-entry invalidation", value=bool(payload["execution"]["cancel_on_invalidation_before_activation"]))
    payload["execution"]["gap_through_entry_policy"] = execution_more_cols[3].selectbox("Gap-through-entry policy", ["miss", "fill_at_open"], index=["miss", "fill_at_open"].index(payload["execution"]["gap_through_entry_policy"]))
    partial_cols = st.columns(3)
    payload["execution"]["partial_exit_fractions"]["tp1"] = partial_cols[0].number_input("TP1 exit fraction", min_value=0.0, max_value=1.0, value=float(payload["execution"]["partial_exit_fractions"]["tp1"]), step=0.01)
    payload["execution"]["partial_exit_fractions"]["tp2"] = partial_cols[1].number_input("TP2 exit fraction", min_value=0.0, max_value=1.0, value=float(payload["execution"]["partial_exit_fractions"]["tp2"]), step=0.01)
    payload["execution"]["partial_exit_fractions"]["tp3"] = partial_cols[2].number_input("TP3 exit fraction", min_value=0.0, max_value=1.0, value=float(payload["execution"]["partial_exit_fractions"]["tp3"]), step=0.01)
    guardrail_cols = st.columns(4)
    payload["portfolio_risk"]["enabled"] = guardrail_cols[0].checkbox("Enable portfolio guardrails", value=bool(payload["portfolio_risk"]["enabled"]))
    payload["portfolio_risk"]["max_simultaneous_trades"] = int(guardrail_cols[1].number_input("Max simultaneous trades", min_value=1, max_value=100, value=int(payload["portfolio_risk"]["max_simultaneous_trades"]), step=1))
    payload["portfolio_risk"]["max_exposure_per_currency"] = int(guardrail_cols[2].number_input("Max exposure per currency", min_value=1, max_value=100, value=int(payload["portfolio_risk"]["max_exposure_per_currency"]), step=1))
    payload["portfolio_risk"]["max_daily_loss_r"] = guardrail_cols[3].number_input("Max daily loss R", min_value=0.1, max_value=100.0, value=float(payload["portfolio_risk"]["max_daily_loss_r"]), step=0.1)
    guardrail_more_cols = st.columns(4)
    payload["portfolio_risk"]["cooldown_after_consecutive_losses"] = int(guardrail_more_cols[0].number_input("Cooldown loss streak", min_value=1, max_value=20, value=int(payload["portfolio_risk"]["cooldown_after_consecutive_losses"]), step=1))
    payload["portfolio_risk"]["cooldown_bars"] = int(guardrail_more_cols[1].number_input("Cooldown bars", min_value=1, max_value=500, value=int(payload["portfolio_risk"]["cooldown_bars"]), step=1))
    payload["portfolio_risk"]["min_data_quality_for_entry"] = guardrail_more_cols[2].number_input("Min paper data quality", min_value=0.0, max_value=100.0, value=float(payload["portfolio_risk"]["min_data_quality_for_entry"]), step=1.0)
    payload["portfolio_risk"]["max_spread_to_atr_ratio"] = guardrail_more_cols[3].number_input("Max paper spread/ATR", min_value=0.0, max_value=2.0, value=float(payload["portfolio_risk"]["max_spread_to_atr_ratio"]), step=0.01)
    guardrail_limit_cols = st.columns(4)
    payload["portfolio_risk"]["max_exposure_per_symbol"] = int(guardrail_limit_cols[0].number_input("Max exposure per symbol", min_value=1, max_value=100, value=int(payload["portfolio_risk"]["max_exposure_per_symbol"]), step=1))
    payload["portfolio_risk"]["max_exposure_per_setup_family"] = int(guardrail_limit_cols[1].number_input("Max exposure per setup family", min_value=1, max_value=100, value=int(payload["portfolio_risk"]["max_exposure_per_setup_family"]), step=1))
    payload["portfolio_risk"]["max_exposure_per_setup_subtype"] = int(guardrail_limit_cols[2].number_input("Max exposure per setup subtype", min_value=1, max_value=100, value=int(payload["portfolio_risk"]["max_exposure_per_setup_subtype"]), step=1))
    payload["portfolio_risk"]["block_off_hours"] = guardrail_limit_cols[3].checkbox("Block off-hours paper entries", value=bool(payload["portfolio_risk"]["block_off_hours"]))

    st.markdown("**Style Parameters**")
    timeframe_options = ["M1", "M5", "M15", "H1", "H4", "D1"]
    for style in TradingStyle:
        with st.expander(style.value.replace("_", " ").title(), expanded=False):
            style_payload = payload["styles"][style.value]
            style_payload["higher_timeframe"] = st.selectbox("Higher timeframe", timeframe_options, index=timeframe_options.index(style_payload["higher_timeframe"]), key=f"{style.value}_higher")
            style_payload["entry_timeframe"] = st.selectbox("Entry timeframe", timeframe_options, index=timeframe_options.index(style_payload["entry_timeframe"]), key=f"{style.value}_entry")
            style_payload["trigger_timeframe"] = st.selectbox("Trigger timeframe", timeframe_options, index=timeframe_options.index(style_payload["trigger_timeframe"]), key=f"{style.value}_trigger")
            style_payload["min_rr"] = st.number_input("Minimum RR", min_value=0.1, max_value=10.0, value=float(style_payload["min_rr"]), step=0.1, key=f"{style.value}_rr")
            style_payload["atr_stop_multiplier"] = st.number_input("ATR stop multiplier", min_value=0.1, max_value=10.0, value=float(style_payload["atr_stop_multiplier"]), step=0.1, key=f"{style.value}_atr_stop")
            style_payload["atr_target_multiplier"] = st.number_input("ATR target multiplier", min_value=0.1, max_value=20.0, value=float(style_payload["atr_target_multiplier"]), step=0.1, key=f"{style.value}_atr_target")
            style_payload["swing_buffer_atr"] = st.number_input("Swing buffer ATR", min_value=0.0, max_value=3.0, value=float(style_payload["swing_buffer_atr"]), step=0.05, key=f"{style.value}_swing_buffer")
            style_payload["lookback_bars"] = st.number_input("Lookback bars", min_value=120, max_value=5000, value=int(style_payload["lookback_bars"]), step=10, key=f"{style.value}_lookback")
            style_payload["max_hold_bars"] = st.number_input("Max hold bars", min_value=1, max_value=500, value=int(style_payload["max_hold_bars"]), step=1, key=f"{style.value}_max_hold")
            style_payload["transaction_cost_pips"] = st.number_input("Round-trip cost pips", min_value=0.0, max_value=20.0, value=float(style_payload["transaction_cost_pips"]), step=0.1, key=f"{style.value}_cost")

    st.markdown("**Enabled Setups**")
    for family in [SetupFamily.TREND_CONTINUATION, SetupFamily.BREAKOUT_CONFIRMATION, SetupFamily.MEAN_REVERSION]:
        payload["setups"]["enabled"][family.value] = st.checkbox(
            family.value.replace("_", " ").title(),
            value=bool(payload["setups"]["enabled"][family.value]),
        )
        payload["setups"]["minimum_scores"][family.value] = st.number_input(
            f"{family.value.replace('_', ' ').title()} minimum score",
            min_value=0.0,
            max_value=100.0,
            value=float(payload["setups"]["minimum_scores"][family.value]),
            step=1.0,
        )

    st.markdown("**Setup Thresholds**")
    payload["setups"]["pullback_ema_tolerance_atr"] = st.number_input(
        "Pullback EMA tolerance ATR",
        min_value=0.01,
        max_value=5.0,
        value=float(payload["setups"]["pullback_ema_tolerance_atr"]),
        step=0.05,
    )
    payload["setups"]["breakout_buffer_atr"] = st.number_input(
        "Breakout buffer ATR",
        min_value=0.0,
        max_value=5.0,
        value=float(payload["setups"]["breakout_buffer_atr"]),
        step=0.05,
    )
    payload["setups"]["range_rsi_low"] = st.number_input(
        "Range RSI low",
        min_value=1.0,
        max_value=49.0,
        value=float(payload["setups"]["range_rsi_low"]),
        step=1.0,
    )
    payload["setups"]["range_rsi_high"] = st.number_input(
        "Range RSI high",
        min_value=51.0,
        max_value=99.0,
        value=float(payload["setups"]["range_rsi_high"]),
        step=1.0,
    )
    payload["setups"]["level_tolerance_atr"] = st.number_input(
        "Level tolerance ATR",
        min_value=0.01,
        max_value=5.0,
        value=float(payload["setups"]["level_tolerance_atr"]),
        step=0.05,
    )

    if st.button("Save settings", type="primary"):
        try:
            updated = AppSettings.model_validate(payload)
            written = save_settings(updated)
            database.save_settings_snapshot(updated)
            st.success(f"Settings saved to {written}")
        except Exception as exc:
            st.error(f"Settings were not saved: {exc}")


def _opportunity_table(opportunities: list[Opportunity]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for opportunity in opportunities:
        rows.append(
            {
                "symbol": opportunity.symbol,
                "status_badge": _status_badge(opportunity.status),
                "status": opportunity.status.value,
                "regime": opportunity.regime.value,
                "direction": opportunity.direction.value,
                "approved": opportunity.approved,
                "setup": opportunity.setup_family.value,
                "subtype": opportunity.setup_subtype.value,
                "raw_setup": opportunity.raw_setup_family.value if opportunity.raw_setup_family else "",
                "session": opportunity.session.value if opportunity.session else "",
                "score": opportunity.score,
                "technical_score": opportunity.technical_score,
                "execution_score": opportunity.execution_score,
                "context_score": opportunity.context_score,
                "empirical_score": opportunity.empirical_score,
                "final_score": opportunity.final_score,
                "grade": opportunity.grade.value if opportunity.grade else "",
                "pre_gate_score": opportunity.pre_gate_score,
                "confidence": opportunity.confidence.value,
                "entry": opportunity.entry,
                "stop_loss": opportunity.stop_loss,
                "take_profit": opportunity.take_profit,
                "tp1": opportunity.tp1,
                "tp2": opportunity.tp2,
                "tp3": opportunity.tp3,
                "rr": opportunity.risk_reward,
                "required_rr": opportunity.required_min_rr,
                "activation_quality": opportunity.activation_quality,
                "invalidation_quality": opportunity.invalidation_quality,
                "spread": opportunity.spread,
                "atr": opportunity.atr,
                "key_level_distances": opportunity.key_level_distances,
                "htf_regime": opportunity.htf_regime.value if opportunity.htf_regime else "",
                "entry_regime": opportunity.entry_regime.value if opportunity.entry_regime else "",
                "trigger_regime": opportunity.trigger_regime.value if opportunity.trigger_regime else "",
                "failed_gates": ", ".join(opportunity.failed_gates),
                "missing_condition": "; ".join(opportunity.missing_conditions),
                "invalidation": opportunity.invalidation or "",
                "rejection_category": opportunity.rejection_category.value if opportunity.rejection_category else "",
                "provider": opportunity.provider,
                "data_quality": opportunity.data_quality.score if opportunity.data_quality else None,
                "data_quality_warning": "; ".join(opportunity.data_quality.warnings) if opportunity.data_quality else "",
                "outcome": opportunity.outcome.value if opportunity.outcome else "",
                "tp1_hit": opportunity.tp1_hit,
                "tp2_hit": opportunity.tp2_hit,
                "tp3_hit": opportunity.tp3_hit,
                "mae": opportunity.mae,
                "mfe": opportunity.mfe,
                "no_trade_reason": opportunity.rejection_reason or "",
            }
        )
    return pd.DataFrame(rows)


def _dashboard_opportunity_table(opportunities: list[Opportunity]) -> pd.DataFrame:
    table = _opportunity_table(opportunities)
    columns = [
        "symbol",
        "status_badge",
        "final_score",
        "regime",
        "setup",
        "subtype",
        "rr",
        "data_quality",
        "direction",
        "rejection_category",
        "failed_gates",
        "no_trade_reason",
    ]
    return table[[column for column in columns if column in table.columns]]


def _status_badge(status: OpportunityStatus) -> str:
    return f"[{status.value.upper()}]"


def _status_summary(opportunities: list[Opportunity]) -> None:
    statuses = [
        OpportunityStatus.REJECTED,
        OpportunityStatus.DETECTED,
        OpportunityStatus.WATCHLIST,
        OpportunityStatus.APPROVED,
        OpportunityStatus.PREMIUM,
    ]
    counts = {status: sum(1 for opportunity in opportunities if opportunity.status == status) for status in statuses}
    cols = st.columns(len(statuses))
    for col, status in zip(cols, statuses, strict=True):
        col.metric(_status_badge(status), counts[status])


def _ranked_opportunities(opportunities: list[Opportunity]) -> list[Opportunity]:
    status_rank = {
        OpportunityStatus.PREMIUM: 5,
        OpportunityStatus.APPROVED: 4,
        OpportunityStatus.WATCHLIST: 3,
        OpportunityStatus.DETECTED: 2,
        OpportunityStatus.REJECTED: 1,
    }
    return sorted(opportunities, key=lambda item: (status_rank[item.status], item.final_score or item.score), reverse=True)


def _submit_to_paper(settings: AppSettings, database: Database, opportunities: list[Opportunity]) -> PaperTradingResult:
    submissions = [
        submit_signal_to_paper(opportunity, settings=settings, database=database, source="manual")
        for opportunity in opportunities
    ]
    orders = [submission.order for submission in submissions if submission.order is not None]
    block_records = [submission.block_record for submission in submissions if submission.block_record is not None]
    blocked = {
        f"{block.symbol}:{block.setup_subtype}:{block.status}": block.reasons
        for block in block_records
    }
    return PaperTradingResult(orders=orders, blocked=blocked, block_records=block_records)


def _paper_order_label(order: ExecutionOrder) -> str:
    return f"{order.request.symbol} {order.status.value} {order.request.direction.value} {order.request.entry_price:.5f}"


def _orders_dataframe(orders: list[ExecutionOrder], *, empty_message: str) -> None:
    if not orders:
        st.info(empty_message)
        return
    rows = [
        {
            "id": order.order_id,
            "symbol": order.request.symbol,
            "status": order.status.value,
            "source": order.execution_assumptions.get("source", ""),
            "direction": order.request.direction.value,
            "setup": order.request.setup_family.value,
            "subtype": order.request.setup_subtype.value,
            "entry": order.request.entry_price,
            "simulated_entry": order.simulated_entry,
            "stop_loss": order.request.stop_loss,
            "tp1": order.request.tp1,
            "tp2": order.request.tp2,
            "tp3": order.request.tp3,
            "remaining": order.remaining_fraction,
            "realized_r": order.realized_r,
            "simulated_pnl": order.realized_pnl,
            "close_reason": order.close_reason.value if order.close_reason else "",
            "notes": order.execution_assumptions.get("notes", order.execution_assumptions.get("manual_close_notes", "")),
            "events": len(order.events),
            "created_at": order.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        }
        for order in orders
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _events_dataframe(events: list[TradeEvent]) -> None:
    if not events:
        st.info("Aucun événement enregistré.")
        return
    recent = sorted(events, key=lambda event: event.occurred_at, reverse=True)[:50]
    rows = [
        {
            "time": event.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": event.symbol,
            "event": event.event_type.value,
            "status": event.status,
            "reason": event.reason or "",
            "trade_id": event.trade_id,
        }
        for event in recent
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _journal_dataframe(entries: list[TradeJournalEntry]) -> None:
    if not entries:
        st.info("Aucune entrée journal.")
        return
    rows = [
        {
            "signal": entry.signal_id,
            "trade": entry.trade_id,
            "symbol": entry.symbol,
            "direction": entry.direction,
            "source": entry.source,
            "status": entry.status,
            "result": entry.result,
            "pnl_r": entry.pnl_r,
            "setup": entry.setup_family,
            "subtype": entry.setup_subtype,
            "session": entry.session,
            "realized_r": entry.realized_r_multiple,
            "simulated_pnl": entry.realized_pnl,
            "mistake_tags": ", ".join(entry.mistake_tags),
            "lesson": entry.lesson or "",
            "emotion": entry.emotion or "",
            "notes": entry.notes or "",
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "entry_time": entry.entry_timestamp,
            "exit_time": entry.exit_timestamp,
            "block_reasons": "; ".join(entry.block_reasons),
        }
        for entry in entries
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _outputs_table(outputs: dict[str, Path]) -> pd.DataFrame:
    return pd.DataFrame([{"name": name, "path": str(path)} for name, path in outputs.items()])


def _opportunity_label(opportunity: Opportunity) -> str:
    if opportunity.raw_setup_family is None:
        return f"{opportunity.symbol}: {opportunity.status.value}"
    if opportunity.status != OpportunityStatus.APPROVED:
        if opportunity.raw_setup_family is None:
            return f"{opportunity.symbol}: no-trade"
        setup = opportunity.setup_subtype.value if opportunity.setup_subtype.value != "none" else opportunity.raw_setup_family.value
        score = "" if opportunity.final_score is None else f" ({opportunity.final_score:.1f})"
        return f"{opportunity.symbol}: {opportunity.status.value} {setup}{score}"
    return f"{opportunity.symbol}: {opportunity.status.value} {opportunity.direction.value} {opportunity.setup_subtype.value} ({opportunity.score:.1f})"


def _opportunity_details(opportunity: Opportunity) -> None:
    cols = st.columns(4)
    cols[0].metric("Symbol", opportunity.symbol)
    cols[1].metric("Status", opportunity.status.value)
    cols[2].metric("Direction", opportunity.direction.value)
    cols[3].metric("Final score", f"{(opportunity.final_score or opportunity.score):.1f}")

    cols = st.columns(4)
    cols[0].metric("Entry", _fmt_price(opportunity.entry))
    cols[1].metric("Stop loss", _fmt_price(opportunity.stop_loss))
    cols[2].metric("Primary TP", _fmt_price(opportunity.take_profit))
    cols[3].metric("Risk/reward", "n/a" if opportunity.risk_reward is None else f"{opportunity.risk_reward:.2f}")
    target_cols = st.columns(3)
    target_cols[0].metric("TP1", _fmt_price(opportunity.tp1))
    target_cols[1].metric("TP2", _fmt_price(opportunity.tp2))
    target_cols[2].metric("TP3", _fmt_price(opportunity.tp3))
    if opportunity.pre_gate_score is not None or opportunity.raw_setup_family is not None:
        diag_cols = st.columns(4)
        diag_cols[0].metric("Technical score", "n/a" if opportunity.technical_score is None else f"{opportunity.technical_score:.1f}")
        diag_cols[1].metric("Subtype", opportunity.setup_subtype.value)
        diag_cols[2].metric("Execution score", "n/a" if opportunity.execution_score is None else f"{opportunity.execution_score:.1f}")
        diag_cols[3].metric("Required RR", "n/a" if opportunity.required_min_rr is None else f"{opportunity.required_min_rr:.2f}")
        layer_cols = st.columns(4)
        layer_cols[0].metric("Context score", "n/a" if opportunity.context_score is None else f"{opportunity.context_score:.1f}")
        layer_cols[1].metric("Empirical score", "n/a" if opportunity.empirical_score is None else f"{opportunity.empirical_score:.1f}")
        layer_cols[2].metric("Activation", "n/a" if opportunity.activation_quality is None else f"{opportunity.activation_quality:.1f}")
        layer_cols[3].metric("Invalidation", "n/a" if opportunity.invalidation_quality is None else f"{opportunity.invalidation_quality:.1f}")
    regime_cols = st.columns(3)
    regime_cols[0].metric("HTF regime", opportunity.htf_regime.value if opportunity.htf_regime else opportunity.regime.value)
    regime_cols[1].metric("Entry regime", opportunity.entry_regime.value if opportunity.entry_regime else "n/a")
    regime_cols[2].metric("Trigger regime", opportunity.trigger_regime.value if opportunity.trigger_regime else "n/a")
    st.write(opportunity.explanation)
    if opportunity.missing_conditions:
        st.info("Missing for activation: " + "; ".join(opportunity.missing_conditions))
    if opportunity.invalidation:
        st.caption(f"Invalidation: {opportunity.invalidation}")
    if opportunity.rejection_reason:
        st.info(f"Main reason: {opportunity.rejection_reason}")
    if opportunity.rejection_category:
        st.warning(f"Main rejection driver: {opportunity.rejection_category.value}")
    execution_context = []
    if opportunity.session:
        execution_context.append(f"session={opportunity.session.value}")
    if opportunity.spread is not None:
        execution_context.append(f"spread={opportunity.spread:.6f}")
    if opportunity.atr is not None:
        execution_context.append(f"ATR={opportunity.atr:.6f}")
    if execution_context:
        st.caption("Execution context: " + " | ".join(execution_context))
    if opportunity.key_level_distances:
        st.caption("Key-level distances in ATR: " + ", ".join(f"{key}={value}" for key, value in opportunity.key_level_distances.items()))
    if opportunity.outcome:
        st.caption(
            "Realized outcome: "
            f"{opportunity.outcome.value}; TP hits={bool(opportunity.tp1_hit)}/{bool(opportunity.tp2_hit)}/{bool(opportunity.tp3_hit)}; "
            f"MAE={opportunity.mae}; MFE={opportunity.mfe}"
        )
    if opportunity.gate_breakdown:
        st.dataframe(_gate_table(opportunity), hide_index=True, width="stretch")
    if opportunity.data_warning:
        st.warning(opportunity.data_warning)
    if opportunity.data_quality:
        st.caption(f"Data quality score: {opportunity.data_quality.score:.1f}/100")
        if opportunity.data_quality.warnings:
            st.warning("Data quality: " + "; ".join(opportunity.data_quality.warnings))
    if opportunity.score_components:
        st.dataframe(pd.DataFrame([opportunity.score_components]), hide_index=True, width="stretch")


def _chart_panel(settings: AppSettings, provider: MarketDataProvider, opportunity: Opportunity) -> None:
    try:
        with st.spinner("Loading chart data..."):
            style_settings = settings.styles[opportunity.style]
            window = window_for_bars(style_settings.entry_timeframe, style_settings.lookback_bars)
            df = add_indicators(provider.get_ohlcv(opportunity.symbol, style_settings.entry_timeframe, window.start, window.end))
            levels = find_key_levels(df, tolerance_atr=settings.setups.level_tolerance_atr)
        st.plotly_chart(_build_chart(df.tail(160), opportunity, levels), width="stretch")
    except Exception as exc:
        st.warning(f"Chart could not be loaded for {opportunity.symbol}: {exc}")


def _build_chart(df: pd.DataFrame, opportunity: Opportunity, levels: LevelSet | None = None) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="Price",
        )
    )
    for column, color in [("ema_20", "#1f77b4"), ("ema_50", "#2ca02c"), ("ema_200", "#d62728")]:
        fig.add_trace(go.Scatter(x=df.index, y=df[column], mode="lines", name=column.upper(), line={"color": color, "width": 1.5}))
    fig.add_trace(go.Scatter(x=df.index, y=df["bb_upper"], mode="lines", name="BB Upper", line={"color": "#7f7f7f", "width": 1, "dash": "dot"}))
    fig.add_trace(go.Scatter(x=df.index, y=df["bb_lower"], mode="lines", name="BB Lower", line={"color": "#7f7f7f", "width": 1, "dash": "dot"}))

    if levels is not None:
        for level in levels.supports[:3]:
            fig.add_hline(
                y=level.price,
                line_color="#00897b",
                line_dash="dot",
                annotation_text=f"Support {level.touches}x",
                annotation_position="bottom right",
            )
        for level in levels.resistances[:3]:
            fig.add_hline(
                y=level.price,
                line_color="#6a1b9a",
                line_dash="dot",
                annotation_text=f"Resistance {level.touches}x",
                annotation_position="top right",
            )

    for label, value, color in [
        ("Entry", opportunity.entry, "#111111"),
        ("SL", opportunity.stop_loss, "#d62728"),
        ("TP", opportunity.take_profit, "#2ca02c"),
    ]:
        if value is not None:
            fig.add_hline(y=value, line_color=color, line_dash="dash", annotation_text=label)
    fig.update_layout(
        height=620,
        margin={"l": 20, "r": 20, "t": 25, "b": 20},
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
    )
    return fig


def _fmt_price(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.5f}"


def _filter_opportunities(opportunities: list[Opportunity], *, key_prefix: str = "opportunities") -> list[Opportunity]:
    status_values = [status.value for status in OpportunityStatus]
    selected_statuses = st.multiselect("Status filter", status_values, default=status_values, key=f"{key_prefix}_status_filter")
    col_a, col_b, col_c = st.columns(3)
    min_final_score = col_a.slider("Minimum final score", 0.0, 100.0, 0.0, 1.0, key=f"{key_prefix}_min_final")
    min_technical_score = col_b.slider("Minimum technical score", 0.0, 100.0, 0.0, 1.0, key=f"{key_prefix}_min_technical")
    min_execution_score = col_c.slider("Minimum execution score", 0.0, 100.0, 0.0, 1.0, key=f"{key_prefix}_min_execution")
    col_d, col_e, col_f = st.columns(3)
    min_context_score = col_d.slider("Minimum context score", 0.0, 100.0, 0.0, 1.0, key=f"{key_prefix}_min_context")
    min_empirical_score = col_e.slider("Minimum empirical score", 0.0, 100.0, 0.0, 1.0, key=f"{key_prefix}_min_empirical")
    min_activation_quality = col_f.slider("Minimum activation quality", 0.0, 100.0, 0.0, 1.0, key=f"{key_prefix}_min_activation")
    min_rr = st.slider("Minimum displayed RR", 0.0, 5.0, 0.0, 0.1, key=f"{key_prefix}_min_rr")
    min_data_quality = st.slider("Minimum data quality", 0.0, 100.0, 0.0, 1.0, key=f"{key_prefix}_min_data_quality")
    filtered = [
        opportunity
        for opportunity in opportunities
        if opportunity.status.value in selected_statuses
        and (opportunity.final_score or opportunity.score) >= min_final_score
        and (opportunity.technical_score or 0.0) >= min_technical_score
        and (opportunity.execution_score or 0.0) >= min_execution_score
        and (opportunity.context_score or 0.0) >= min_context_score
        and (opportunity.empirical_score or 0.0) >= min_empirical_score
        and (opportunity.activation_quality or 0.0) >= min_activation_quality
        and (opportunity.risk_reward or 0.0) >= min_rr
        and (opportunity.data_quality.score if opportunity.data_quality else 100.0) >= min_data_quality
    ]
    return filtered or opportunities


def _gate_table(opportunity: Opportunity) -> pd.DataFrame:
    if opportunity.gate_breakdown is None:
        return pd.DataFrame()
    labels = {
        "trend": "Trend",
        "structure": "Structure",
        "momentum": "Momentum",
        "volatility": "Volatility",
        "multi_timeframe_alignment": "Multi-timeframe alignment",
        "minimum_rr": "Minimum RR",
        "score_threshold": "Score threshold",
    }
    rows = [
        {"gate": labels[key], "status": "pass" if passed else "fail"}
        for key, passed in opportunity.gate_breakdown.model_dump().items()
    ]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    main()
