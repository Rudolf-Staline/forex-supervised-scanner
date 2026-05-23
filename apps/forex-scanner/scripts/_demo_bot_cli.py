"""Shared terminal helpers for paper-only demo bot scripts."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.env import load_dotenv
from app.config.safety import ensure_mt5_demo_safe_mode
from app.config.settings import AppSettings, load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.config.instruments import AssetClass, filter_symbols_by_asset_class, instrument_for_symbol
from app.core.types import TradingStyle
from app.data.mt5_symbols_health import split_healthy_symbols, summarize_symbol_health
from app.data.mt5_symbol_resolver import set_mt5_symbol_overrides
from app.data.providers import DEBUG_MARKET_DATA_ENV, DataProviderError, MarketDataProvider, build_provider
from app.execution.demo_bot import DemoBotCycleResult
from app.execution.models import ExecutionOrder
from app.market.sessions import get_market_session
from app.notifications.notifier import notify_session_transition, update_session_notification_state
from app.safety.demo_execution_gate import (
    DemoExecutionGateContext,
    evaluate_demo_execution_gate,
    format_demo_execution_gate_result,
)
from app.storage.database import Database
from app.utils.logging import configure_logging

DEFAULT_DEMO_SYMBOLS = ["EUR/USD", "GBP/USD", "USD/CHF"]


def add_cycle_arguments(parser: argparse.ArgumentParser) -> None:
    """Add common demo bot cycle CLI arguments."""

    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument(
        "--provider",
        default="synthetic",
        choices=["synthetic", "auto", "mt5"],
        help="Data provider for terminal demos. Default: synthetic. Use mt5 for explicit MT5 market data.",
    )
    parser.add_argument(
        "--broker",
        default="paper",
        choices=["paper", "mt5_demo"],
        help="Execution target. Default: paper. mt5_demo requires BROKER_MODE=mt5_demo and MT5_DEMO_ONLY=true.",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Symbols to scan. Overrides --watchlist. Default: EUR/USD GBP/USD USD/CHF.",
    )
    parser.add_argument(
        "--watchlist",
        default=None,
        choices=watchlist_names(),
        help="Named watchlist profile to scan, for example major_forex.",
    )
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument(
        "--debug-market-data",
        action="store_true",
        help="Enable verbose MT5 market-data diagnostics (raw columns, dtypes, head/tail, NaN counts).",
    )
    parser.add_argument(
        "--skip-unhealthy-symbols",
        action="store_true",
        help="For MT5 data runs, diagnose watchlist symbols first and skip symbols with bars=0 or non-tradable status.",
    )
    parser.add_argument(
        "--only-tradable-session",
        action="store_true",
        help="Skip symbols that are currently outside their configured asset-class demo session before scanning.",
    )
    parser.add_argument(
        "--show-next-windows",
        action="store_true",
        help="Print current UTC time and next configured demo-session windows.",
    )
    parser.add_argument(
        "--explain-execution-gate",
        action="store_true",
        help="Explain why created paper orders would or would not pass the strict MT5 demo execution gate.",
    )
    parser.add_argument(
        "--demo-execution-confirmed",
        action="store_true",
        help="Required explicit operator confirmation before any ultra-limited MT5 demo order can be submitted.",
    )


def load_demo_runtime(
    context: str,
    *,
    provider_name: str = "synthetic",
    broker_mode: str = "paper",
    debug_market_data: bool = False,
) -> tuple[AppSettings, Database, MarketDataProvider]:
    """Load settings, enforce paper/demo safety, and build runtime services."""

    load_dotenv()
    configure_logging()
    if debug_market_data:
        os.environ[DEBUG_MARKET_DATA_ENV] = "true"
    else:
        os.environ.pop(DEBUG_MARKET_DATA_ENV, None)
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = provider_name
    try:
        if broker_mode == "mt5_demo":
            ensure_mt5_demo_safe_mode(settings, context=context)
        else:
            ensure_demo_safe_mode(settings, context=context)
    except DemoSafetyError as exc:
        raise SystemExit(str(exc)) from exc
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)
    return settings, database, provider


def normalize_symbols(symbols: list[str] | None, watchlist: str | None = None, asset_class: str | None = None) -> list[str]:
    """Accept explicit symbols first, then a named watchlist, then defaults."""

    normalized: list[str] = []
    for raw in symbols or []:
        normalized.extend(_normalize_symbol(symbol) for symbol in raw.split(",") if symbol.strip())
    if normalized:
        return filter_symbols_by_asset_class(normalized, asset_class)
    if watchlist:
        return filter_symbols_by_asset_class([_normalize_symbol(symbol) for symbol in get_watchlist(watchlist)], asset_class)
    return filter_symbols_by_asset_class(normalized or list(DEFAULT_DEMO_SYMBOLS), asset_class)


def filter_unhealthy_symbols_if_requested(symbols: list[str], enabled: bool, provider_name: str) -> list[str]:
    """Optionally remove MT5 symbols that cannot provide usable demo data."""

    if not enabled:
        set_mt5_symbol_overrides({})
        return symbols
    if provider_name != "mt5":
        set_mt5_symbol_overrides({})
        print(f"symbol_health=skipped reason=provider_is_{provider_name}")
        return symbols
    try:
        healthy, results = split_healthy_symbols(symbols)
    except DataProviderError as exc:
        raise SystemExit(f"symbol_health=error reason={exc}") from exc
    for result in results:
        action = "use" if result.healthy else "skip"
        spread_atr = "n/a" if result.spread_atr is None else f"{result.spread_atr:.4f}"
        if result.healthy:
            print(f"symbol_resolved logical={result.symbol} mt5_symbol=\"{result.mt5_symbol}\"")
        else:
            print(f"symbol_skipped logical={result.symbol} reason={result.reason}")
        print(
            "symbol_health "
            f"symbol={result.symbol} mt5_symbol={result.mt5_symbol} action={action} status={result.status} "
            f"reason={result.reason} spread_atr={spread_atr}"
        )
    _print_symbol_health_summary(results)
    if not healthy:
        raise SystemExit("no healthy MT5 symbols remain after --skip-unhealthy-symbols")
    set_mt5_symbol_overrides({result.symbol: result.mt5_symbol for result in results if result.healthy})
    return healthy


def filter_tradable_session_symbols_if_requested(
    symbols: list[str],
    enabled: bool,
    *,
    now_utc: datetime | None = None,
    broker_mode: str = "paper",
) -> list[str]:
    """Optionally skip symbols outside configured asset-class demo sessions."""

    if not enabled:
        return symbols
    now = now_utc or datetime.now(timezone.utc)
    tradable: list[str] = []
    next_windows: list[str] = []
    for symbol in symbols:
        instrument = instrument_for_symbol(symbol)
        session = get_market_session(now, instrument.asset_class, symbol)
        if session.is_tradable_session:
            notify_session_transition(session, broker_mode=broker_mode)
            tradable.append(symbol)
            continue
        update_session_notification_state(session)
        next_windows.append(session.next_tradable_window)
        print(
            "skipped_off_hours "
            f"symbol={symbol} asset_class={session.asset_class} session_name={session.session_name} "
            f"next_tradable_window=\"{session.next_tradable_window}\""
        )
    if not tradable:
        print("no_tradable_symbols_now=true")
        print(f"recommended_next_run_time={recommended_next_run_time(next_windows)}")
    return tradable


def print_next_session_windows(symbols: list[str] | None = None, *, now_utc: datetime | None = None) -> None:
    """Print the next configured demo-session windows for each asset class."""

    now = now_utc or datetime.now(timezone.utc)
    print(f"current_utc_time={now.isoformat(timespec='seconds')}")
    windows: dict[AssetClass, str] = {}
    for asset_class in AssetClass:
        sample_symbol = _sample_symbol_for_asset_class(asset_class, symbols)
        windows[asset_class] = get_market_session(now, asset_class, sample_symbol).next_tradable_window
    print(f"next_forex_window={windows[AssetClass.FOREX]}")
    print(f"next_commodities_window={windows[AssetClass.COMMODITIES]}")
    print(f"next_indices_window={windows[AssetClass.INDICES]}")
    print(f"recommended_next_run_time={recommended_next_run_time(list(windows.values()))}")


def next_session_windows_for_symbols(symbols: list[str], *, now_utc: datetime | None = None) -> list[str]:
    """Return next tradable windows for the provided symbols."""

    now = now_utc or datetime.now(timezone.utc)
    windows: list[str] = []
    for symbol in symbols:
        instrument = instrument_for_symbol(symbol)
        windows.append(get_market_session(now, instrument.asset_class, symbol).next_tradable_window)
    return windows


def recommended_next_run_time(next_windows: list[str]) -> str:
    """Return the earliest readable next-run timestamp found in session-window strings."""

    candidates: list[str] = []
    for window in next_windows:
        if not window or window.startswith("no configured"):
            continue
        if "now until " in window:
            return "now"
        parts = window.split()
        for part in parts:
            if "T" in part and "+" in part:
                candidates.append(part)
                break
    return min(candidates) if candidates else "no configured tradable window found"


def calculate_session_wait_seconds(
    recommended_time: str,
    *,
    now_utc: datetime | None = None,
    max_wait_seconds: int = 86400,
) -> tuple[int, bool]:
    """Calculate capped wait seconds until the next tradable session."""

    if recommended_time == "now":
        return 0, False
    now = now_utc or datetime.now(timezone.utc)
    try:
        target = datetime.fromisoformat(recommended_time)
    except ValueError:
        return max(0, max_wait_seconds), True
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    wait_seconds = max(0, int((target.astimezone(timezone.utc) - now).total_seconds()))
    capped = wait_seconds > max_wait_seconds
    if capped:
        return max_wait_seconds, True
    return wait_seconds, False


def _print_symbol_health_summary(results) -> None:
    summary = summarize_symbol_health(results)
    print("Symbol health summary:")
    print(f"- healthy_symbols: {','.join(summary['healthy_symbols']) or '-'}")
    print(f"- unhealthy_symbols: {','.join(summary['unhealthy_symbols']) or '-'}")
    print(f"- highest_spread_atr_symbols: {','.join(summary['highest_spread_atr_symbols']) or '-'}")
    print(f"- lowest_spread_atr_symbols: {','.join(summary['lowest_spread_atr_symbols']) or '-'}")
    print(f"- recommended_watchlist_for_demo: {','.join(summary['recommended_watchlist_for_demo']) or '-'}")


def _sample_symbol_for_asset_class(asset_class: AssetClass, symbols: list[str] | None = None) -> str:
    for symbol in symbols or []:
        if instrument_for_symbol(symbol).asset_class == asset_class:
            return symbol
    defaults = {
        AssetClass.FOREX: "EUR/USD",
        AssetClass.COMMODITIES: "XAU/USD",
        AssetClass.INDICES: "US500",
    }
    return defaults[asset_class]


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def created_order_ids(result: DemoBotCycleResult) -> list[str]:
    """Return order ids created during accepted decisions."""

    return [order_id for decision in result.decisions for order_id in decision.order_ids]


def print_broker_result(order_id: str, broker_order_id: str | None, broker_order: ExecutionOrder | None = None) -> None:
    """Print one MT5 demo submission result."""

    if broker_order is None:
        print(f"broker_submit=ok mode=mt5_demo paper_order_id={order_id} broker_order_id={broker_order_id or '-'}")
        return
    acknowledgement = broker_order.broker_acknowledgement
    print(
        "broker_submit=ok mode=mt5_demo "
        f"paper_order_id={order_id} "
        f"order_id={broker_order.order_id} "
        f"broker_order_id={broker_order_id or '-'} "
        f"retcode={acknowledgement.get('retcode', '-')} "
        f"broker_comment=\"{acknowledgement.get('comment', '')}\" "
        f"filled_price={broker_order.average_fill_price or broker_order.simulated_entry or '-'} "
        f"volume={broker_order.request.quantity_units} "
        f"timestamp={broker_order.created_at.isoformat()}"
    )


def print_execution_gate_explanations(
    result: DemoBotCycleResult,
    database: Database,
    settings: AppSettings,
    broker_mode: str,
    *,
    account=None,
    mt5=None,
) -> None:
    """Print strict MT5 demo gate diagnostics for this cycle."""

    paper_orders = {order.order_id: order for order in database.load_paper_orders()}
    for decision in result.decisions:
        if not decision.order_ids:
            reasons = "; ".join(decision.reasons) if decision.reasons else "no paper order was created"
            print(
                "demo_execution_gate=blocked "
                f"symbol={decision.symbol} status={decision.status} setup={decision.setup_subtype} "
                f"reason=signal did not create a paper order; {reasons}"
            )
            continue
        for order_id in decision.order_ids:
            paper_order = paper_orders.get(order_id)
            if paper_order is None:
                print(f"demo_execution_gate=blocked paper_order_id={order_id} reason=paper order was not found")
                continue
            gate = evaluate_order_execution_gate(
                settings,
                database,
                paper_order,
                broker_mode=broker_mode,
                account=account,
                mt5=mt5,
            )
            print(format_demo_execution_gate_result(order_id, gate))


def evaluate_order_execution_gate(
    settings: AppSettings,
    database: Database,
    paper_order: ExecutionOrder,
    *,
    broker_mode: str,
    account=None,
    mt5=None,
    mt5_symbol: str | None = None,
    symbol_info=None,
    demo_execution_confirmed: bool = False,
):
    """Evaluate the strict MT5 demo gate for one created paper order."""

    return evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=paper_order,
            broker_mode=broker_mode,
            existing_orders=database.load_paper_orders(),
            account=account,
            mt5=mt5,
            mt5_symbol=mt5_symbol,
            symbol_info=symbol_info,
            demo_execution_confirmed=demo_execution_confirmed,
        )
    )


def print_cycle_result(result: DemoBotCycleResult) -> None:
    """Render one demo bot cycle in a compact terminal format."""

    print(
        "cycle "
        f"id={result.cycle_id} style={result.style.value} symbols={','.join(result.symbols)} "
        f"opportunities={result.opportunities} orders_created={result.orders_created}"
    )
    for decision in result.decisions:
        verdict = "ACCEPT" if decision.accepted else "REJECT"
        score = "n/a" if decision.final_score is None else f"{decision.final_score:.2f}"
        rr = "n/a" if decision.risk_reward is None else f"{decision.risk_reward:.2f}"
        order_ids = ",".join(decision.order_ids) if decision.order_ids else "-"
        reasons = "; ".join(decision.reasons) if decision.reasons else "paper trade accepted"
        patterns = ",".join(decision.detected_patterns) if decision.detected_patterns else "-"
        print(
            "decision "
            f"{verdict} symbol={decision.symbol} status={decision.status} setup={decision.setup_subtype} "
            f"score={score} rr={rr} pattern_score={decision.pattern_score:.2f} "
            f"detected_patterns={patterns} order_ids={order_ids} reasons={reasons}"
        )
    if result.logs:
        print("logs:")
        for line in result.logs:
            print(f"- {line}")
