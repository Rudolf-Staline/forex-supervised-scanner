# -*- coding: utf-8 -*-
"""Run one controlled approved fixture through the existing demo bot service."""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.execution.demo_bot as demo_bot_module
from _demo_bot_cli import print_cycle_result
from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.settings import load_settings
from app.core.types import (
    ConfidenceBucket,
    DataQualityDiagnostic,
    DirectionBias,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    ScanReport,
    SessionName,
    SetupFamily,
    SetupSubtype,
    Timeframe,
    TradingStyle,
)
from app.data.providers import build_provider
from app.execution.demo_bot import DemoBotService
from app.storage.database import Database
from app.utils.logging import configure_logging


class FixtureScannerService:
    """Return one controlled approved opportunity without changing scanner rules."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def scan(self, style: TradingStyle, symbols: list[str], timestamp: datetime | None = None) -> ScanReport:
        scan_time = timestamp or datetime.now(timezone.utc)
        symbol = symbols[0] if symbols else "EUR/USD"
        return ScanReport(
            timestamp=scan_time,
            style=style,
            opportunities=[_approved_fixture_opportunity(symbol=symbol, style=style, timestamp=scan_time)],
        )


def main() -> None:
    """Create one paper order from a controlled fixture signal."""

    parser = argparse.ArgumentParser(description="Run a controlled approved demo fixture through DemoBotService.")
    parser.add_argument("--symbol", default="EUR/USD")
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument(
        "--database",
        default=None,
        help="Optional SQLite path. Defaults to an isolated temporary fixture database.",
    )
    args = parser.parse_args()

    print("TEST FIXTURE — données synthétiques — aucun marché réel")
    configure_logging()
    settings = load_settings()
    try:
        ensure_demo_safe_mode(settings, context="run_approved_fixture_cycle.py")
    except DemoSafetyError as exc:
        raise SystemExit(str(exc)) from exc

    provider = build_provider(settings)
    style = TradingStyle(args.style)
    if args.database:
        database = Database(Path(args.database))
        _run_fixture(settings, provider, database, style, args.symbol)
        return

    with tempfile.TemporaryDirectory(prefix="forex-approved-fixture-") as temp_dir:
        database = Database(Path(temp_dir) / "fixture.sqlite")
        _run_fixture(settings, provider, database, style, args.symbol)


def _run_fixture(settings, provider, database: Database, style: TradingStyle, symbol: str) -> None:
    original_scanner = demo_bot_module.ScannerService
    demo_bot_module.ScannerService = FixtureScannerService
    try:
        result = DemoBotService(settings, provider, database).run_cycle(style, [symbol.upper()])
    finally:
        demo_bot_module.ScannerService = original_scanner

    print_cycle_result(result)
    if result.orders_created != 1:
        raise SystemExit(f"fixture failed: expected 1 paper order, got {result.orders_created}")
    orders = database.load_paper_orders()
    created_ids = [order_id for decision in result.decisions for order_id in decision.order_ids]
    print(f"fixture_result=ok orders_created={result.orders_created} order_ids={','.join(created_ids)} stored_orders={len(orders)}")


def _approved_fixture_opportunity(symbol: str, style: TradingStyle, timestamp: datetime) -> Opportunity:
    return Opportunity(
        timestamp=timestamp,
        symbol=symbol,
        style=style,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        regime=MarketRegime.TRENDING_UP,
        direction=DirectionBias.LONG,
        score=86.0,
        confidence=ConfidenceBucket.HIGH,
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        risk_reward=2.0,
        explanation="Controlled approved fixture for paper/demo bot validation.",
        timeframe_higher=Timeframe.H1,
        timeframe_entry=Timeframe.M15,
        timeframe_trigger=Timeframe.M5,
        score_components={"fixture": 86.0},
        provider="synthetic_fixture",
        approved=True,
        status=OpportunityStatus.APPROVED,
        raw_setup_family=SetupFamily.TREND_CONTINUATION,
        pre_gate_score=86.0,
        technical_score=86.0,
        execution_score=86.0,
        context_score=86.0,
        empirical_score=86.0,
        final_score=86.0,
        required_min_rr=1.5,
        tp1=1.1050,
        tp2=1.1100,
        tp3=1.1150,
        activation_quality=90.0,
        invalidation_quality=90.0,
        spread=0.00005,
        atr=0.001,
        session=SessionName.LONDON,
        htf_regime=MarketRegime.TRENDING_UP,
        entry_regime=MarketRegime.TRENDING_UP,
        trigger_regime=MarketRegime.TRENDING_UP,
        data_quality=DataQualityDiagnostic(
            score=98.0,
            missing_bars=0,
            stale_minutes=0.0,
            spread_available=True,
            resampled=False,
        ),
    )


if __name__ == "__main__":
    main()
