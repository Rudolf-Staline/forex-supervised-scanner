"""Shared terminal helpers for paper-only demo bot scripts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.env import load_dotenv
from app.config.safety import ensure_mt5_demo_safe_mode
from app.config.settings import AppSettings, load_settings
from app.core.types import TradingStyle
from app.data.providers import MarketDataProvider, build_provider
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
        choices=["synthetic", "auto"],
        help="Data provider for terminal demos. Default: synthetic. Use auto to try MT5/Yahoo before fallback.",
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
        default=DEFAULT_DEMO_SYMBOLS,
        help="Symbols to scan. Default: EUR/USD GBP/USD USD/CHF.",
    )


def load_demo_runtime(
    context: str,
    *,
    provider_name: str = "synthetic",
    broker_mode: str = "paper",
) -> tuple[AppSettings, Database, MarketDataProvider]:
    """Load settings, enforce paper/demo safety, and build runtime services."""

    load_dotenv()
    configure_logging()
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


def normalize_symbols(symbols: list[str]) -> list[str]:
    """Accept whitespace-separated or comma-separated symbols."""

    normalized: list[str] = []
    for raw in symbols:
        normalized.extend(symbol.strip().upper() for symbol in raw.split(",") if symbol.strip())
    return normalized or list(DEFAULT_DEMO_SYMBOLS)


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
        print(
            "decision "
            f"{verdict} symbol={decision.symbol} status={decision.status} setup={decision.setup_subtype} "
            f"score={score} rr={rr} order_ids={order_ids} reasons={reasons}"
        )
    if result.logs:
        print("logs:")
        for line in result.logs:
            print(f"- {line}")
