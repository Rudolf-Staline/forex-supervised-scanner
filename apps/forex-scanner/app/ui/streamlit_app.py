"""Streamlit UI for the Forex technical-analysis scanner."""

from __future__ import annotations

import copy
from datetime import date, datetime, time, timezone
from typing import Literal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.backtest.engine import Backtester
from app.config.settings import AppSettings, LOCAL_SETTINGS_PATH, load_settings, save_settings
from app.core.pipeline import ScannerService
from app.core.types import DirectionBias, Opportunity, OpportunityStatus, SetupFamily, TradingStyle
from app.data.providers import MarketDataProvider, build_provider
from app.data.validation import window_for_bars
from app.indicators.calculations import add_indicators
from app.indicators.levels import LevelSet, find_key_levels
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run the Streamlit app."""

    configure_logging()
    st.set_page_config(page_title="Forex TA Scanner", layout="wide")
    settings = _load_settings()
    database = _database(settings)
    provider = _provider(settings)

    st.title("Forex Technical Scanner")
    st.caption("Rules-based technical analysis only. This tool ranks current conditions; it does not place trades.")
    _provider_notice(settings)
    page = st.sidebar.radio("Workspace", ["Scanner", "Backtest", "Settings"], horizontal=False)
    if page == "Scanner":
        _scanner_page(settings, provider, database)
    elif page == "Backtest":
        _backtest_page(settings, provider, database)
    else:
        _settings_page(settings, database)


def _database(settings: AppSettings) -> Database:
    return Database(settings.database_absolute_path)


def _provider(settings: AppSettings) -> MarketDataProvider:
    return build_provider(settings)


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


def _scanner_page(settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
    st.subheader("Scanner")
    default_symbols = database.load_selected_symbols() or settings.symbols
    style = TradingStyle(
        st.selectbox(
            "Trading style",
            [style.value for style in TradingStyle],
            format_func=lambda value: value.replace("_", " ").title(),
        )
    )
    symbols = st.multiselect("Symbol universe", settings.symbols, default=[symbol for symbol in default_symbols if symbol in settings.symbols])
    if st.button("Scan", type="primary", disabled=not symbols):
        try:
            database.save_selected_symbols(symbols)
            with st.spinner("Scanning current technical conditions..."):
                report = ScannerService(settings, provider, database).scan(style, symbols)
            st.session_state["last_scan_report"] = report
        except Exception as exc:
            st.error(f"Scan failed before symbol-level analysis could complete: {exc}")

    report = st.session_state.get("last_scan_report")
    if report is None:
        st.info("Choose a style and symbol universe, then run a scan.")
        return

    st.caption(f"Last scan: {report.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if report.errors:
        st.warning("Some symbols could not be analyzed.")
        st.dataframe(pd.DataFrame([error.model_dump() for error in report.errors]), width="stretch")

    opportunities = report.opportunities
    if not opportunities:
        st.info("No opportunities returned.")
        return

    filtered = _filter_opportunities(opportunities)
    table = _opportunity_table(filtered)
    st.dataframe(table, hide_index=True, width="stretch")
    if all(opportunity.status not in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM} for opportunity in opportunities):
        st.info(
            "No current setup is approved. Detected and watchlist rows remain visible so you can see what is missing for activation."
        )
    selected_index = st.selectbox(
        "Inspect setup",
        list(range(len(filtered))),
        format_func=lambda idx: _opportunity_label(filtered[idx]),
    )
    selected = filtered[selected_index]
    _opportunity_details(selected)
    _chart_panel(settings, provider, selected)


def _backtest_page(settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
    st.subheader("Backtest")
    st.caption("Use this for technical research only; it is not a forward-performance guarantee.")
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
    symbols = st.multiselect("Symbols", settings.symbols, default=settings.symbols[:2], key="backtest_symbols")

    today = date.today()
    default_start = today.replace(year=today.year - 1)
    col_start, col_end = st.columns(2)
    with col_start:
        start_day = st.date_input("Start date", value=default_start)
    with col_end:
        end_day = st.date_input("End date", value=today)

    if st.button("Run backtest", type="primary", disabled=not symbols):
        start_dt = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_day, time.max, tzinfo=timezone.utc)
        try:
            with st.spinner("Running historical test..."):
                result = Backtester(settings, provider, database).run(symbols, style, setup_filter, start_dt, end_dt)
            st.session_state["last_backtest_result"] = result
        except Exception as exc:
            st.error(f"Backtest failed: {exc}")

    result = st.session_state.get("last_backtest_result")
    if result is None:
        return

    metric_cols = st.columns(4)
    metric_cols[0].metric("Trades", result.metrics.number_of_trades)
    metric_cols[1].metric("Win rate", f"{result.metrics.win_rate:.2f}%")
    metric_cols[2].metric("Profit factor", f"{result.metrics.profit_factor:.2f}")
    metric_cols[3].metric("Expectancy", f"{result.metrics.expectancy:.3f} R")

    metric_cols = st.columns(4)
    metric_cols[0].metric("Avg win", f"{result.metrics.average_win:.3f} R")
    metric_cols[1].metric("Avg loss", f"{result.metrics.average_loss:.3f} R")
    metric_cols[2].metric("Max drawdown", f"{result.metrics.max_drawdown:.3f} R")
    metric_cols[3].metric("Sharpe-like", f"{result.metrics.sharpe_like:.3f}")

    if result.equity_curve:
        equity = pd.DataFrame(result.equity_curve, columns=["time", "equity_r"])
        st.plotly_chart(
            go.Figure(data=[go.Scatter(x=equity["time"], y=equity["equity_r"], mode="lines", name="Equity R")]).update_layout(
                height=360,
                margin={"l": 20, "r": 20, "t": 20, "b": 20},
            ),
            width="stretch",
        )
    if result.trades:
        st.dataframe(pd.DataFrame([trade.model_dump(mode="json") for trade in result.trades]), width="stretch")
    else:
        st.info(
            "Zero trades means no historical signal passed setup, score, and risk gates for this filter. "
            "That is normal for short date ranges or strict setup filters; broaden the date range or use EUR/USD in synthetic demo mode for a livelier smoke test."
        )
    if result.limitations:
        st.info("Backtest limitations: " + " ".join(result.limitations))


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


def _filter_opportunities(opportunities: list[Opportunity]) -> list[Opportunity]:
    status_values = [status.value for status in OpportunityStatus]
    selected_statuses = st.multiselect("Status filter", status_values, default=status_values)
    col_a, col_b, col_c = st.columns(3)
    min_final_score = col_a.slider("Minimum final score", 0.0, 100.0, 0.0, 1.0)
    min_technical_score = col_b.slider("Minimum technical score", 0.0, 100.0, 0.0, 1.0)
    min_execution_score = col_c.slider("Minimum execution score", 0.0, 100.0, 0.0, 1.0)
    col_d, col_e, col_f = st.columns(3)
    min_context_score = col_d.slider("Minimum context score", 0.0, 100.0, 0.0, 1.0)
    min_empirical_score = col_e.slider("Minimum empirical score", 0.0, 100.0, 0.0, 1.0)
    min_activation_quality = col_f.slider("Minimum activation quality", 0.0, 100.0, 0.0, 1.0)
    min_rr = st.slider("Minimum displayed RR", 0.0, 5.0, 0.0, 0.1)
    min_data_quality = st.slider("Minimum data quality", 0.0, 100.0, 0.0, 1.0)
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
