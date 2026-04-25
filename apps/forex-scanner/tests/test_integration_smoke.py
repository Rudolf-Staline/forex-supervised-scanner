"""Deterministic integration smoke tests for delivery hardening."""

from __future__ import annotations

from datetime import datetime, timezone

from app.backtest.engine import Backtester
from app.config.settings import load_settings
from app.core.pipeline import ScannerService
from app.core.types import OpportunityStatus, TradingStyle
from app.data.providers import AutoFallbackProvider, SyntheticForexDataProvider, build_provider
from app.storage.database import Database


def test_default_settings_db_provider_scan_and_backtest_smoke(tmp_path) -> None:
    settings = load_settings().model_copy(deep=True)
    configured_provider = build_provider(settings)
    assert isinstance(configured_provider, AutoFallbackProvider)

    settings.provider.name = "synthetic"
    settings.provider.max_bars = 300
    settings.styles[TradingStyle.DAY_TRADING].lookback_bars = 220
    settings.styles[TradingStyle.DAY_TRADING].max_hold_bars = 4
    database = Database(tmp_path / "smoke.sqlite")
    provider = SyntheticForexDataProvider(settings.provider)

    scan = ScannerService(settings, provider, database).scan(
        TradingStyle.DAY_TRADING,
        ["EUR/USD", "GBP/USD", "USD/CHF"],
        timestamp=datetime(2025, 1, 15, 14, tzinfo=timezone.utc),
    )
    assert len(scan.opportunities) >= 1
    assert not scan.errors
    tradable = [opportunity for opportunity in scan.opportunities if opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}]
    diagnosed = [opportunity for opportunity in scan.opportunities if opportunity.raw_setup_family is not None]
    assert tradable or diagnosed
    if not tradable:
        assert diagnosed[0].rejection_reason
        assert diagnosed[0].pre_gate_score is not None

    backtest = Backtester(settings, provider, database).run(
        symbols=[(tradable or diagnosed)[0].symbol],
        style=TradingStyle.DAY_TRADING,
        setup_filter="all",
        start=datetime(2025, 1, 10, tzinfo=timezone.utc),
        end=datetime(2025, 1, 11, tzinfo=timezone.utc),
    )
    assert backtest.metrics.number_of_trades >= 0
    assert len(backtest.equity_curve) >= 1
