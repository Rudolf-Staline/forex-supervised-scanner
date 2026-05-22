"""Shared terminal helpers for paper-only demo bot scripts."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.env import load_dotenv
from app.config.safety import ensure_mt5_demo_safe_mode
from app.config.settings import AppSettings, load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.config.instruments import filter_symbols_by_asset_class
from app.core.types import TradingStyle
from app.data.mt5_symbols_health import split_healthy_symbols, summarize_symbol_health
from app.data.providers import DEBUG_MARKET_DATA_ENV, DataProviderError, MarketDataProvider, build_provider
from app.execution.demo_bot import DemoBotCycleResult
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
        return symbols
    if provider_name != "mt5":
        print(f"symbol_health=skipped reason=provider_is_{provider_name}")
        return symbols
    try:
        healthy, results = split_healthy_symbols(symbols)
    except DataProviderError as exc:
        raise SystemExit(f"symbol_health=error reason={exc}") from exc
    for result in results:
        action = "use" if result.healthy else "skip"
        spread_atr = "n/a" if result.spread_atr is None else f"{result.spread_atr:.4f}"
        print(
            "symbol_health "
            f"symbol={result.symbol} mt5_symbol={result.mt5_symbol} action={action} status={result.status} "
            f"reason={result.reason} spread_atr={spread_atr}"
        )
    _print_symbol_health_summary(results)
    if not healthy:
        raise SystemExit("no healthy MT5 symbols remain after --skip-unhealthy-symbols")
    return healthy


def _print_symbol_health_summary(results) -> None:
    summary = summarize_symbol_health(results)
    print("Symbol health summary:")
    print(f"- healthy_symbols: {','.join(summary['healthy_symbols']) or '-'}")
    print(f"- unhealthy_symbols: {','.join(summary['unhealthy_symbols']) or '-'}")
    print(f"- highest_spread_atr_symbols: {','.join(summary['highest_spread_atr_symbols']) or '-'}")
    print(f"- lowest_spread_atr_symbols: {','.join(summary['lowest_spread_atr_symbols']) or '-'}")
    print(f"- recommended_watchlist_for_demo: {','.join(summary['recommended_watchlist_for_demo']) or '-'}")


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def created_order_ids(result: DemoBotCycleResult) -> list[str]:
    """Return order ids created during accepted decisions."""

    return [order_id for decision in result.decisions for order_id in decision.order_ids]


def print_broker_result(order_id: str, broker_order_id: str | None) -> None:
    """Print one MT5 demo submission result."""

    print(f"broker_submit=ok mode=mt5_demo paper_order_id={order_id} broker_order_id={broker_order_id or '-'}")


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
