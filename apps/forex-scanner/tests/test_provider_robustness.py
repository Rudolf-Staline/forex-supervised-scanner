"""Provider robustness tests for missing optional dependencies."""

from __future__ import annotations

import builtins
from datetime import datetime, timezone

import pandas as pd
import pytest

from app.config.settings import ProviderSettings
from app.core.types import Timeframe
from app.data.providers import AutoFallbackProvider, DataProviderError, MarketDataProvider, MetaTrader5Provider, SyntheticForexDataProvider, YahooFinanceProvider
from app.data.validation import assess_data_quality
from tests.conftest import make_ohlcv


class _FailingProvider(MarketDataProvider):
    name = "failing"

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        raise DataProviderError("forced failure")


class _StaticProvider(MarketDataProvider):
    name = "static"

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        return make_ohlcv(rows=180)


def test_yahoo_provider_reports_missing_yfinance_cleanly(monkeypatch, settings) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("missing test dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    provider = YahooFinanceProvider(settings.provider)
    with pytest.raises(DataProviderError, match="yfinance is not installed"):
        provider.get_ohlcv("EUR/USD", Timeframe.M15)


def test_mt5_provider_reports_missing_package_cleanly(monkeypatch, settings) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "MetaTrader5":
            raise ImportError("missing test dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    provider = MetaTrader5Provider(settings.provider)
    with pytest.raises(DataProviderError, match="MetaTrader5 Python package is not installed"):
        provider.get_ohlcv("EUR/USD", Timeframe.M15)


def test_auto_provider_tries_mt5_then_yahoo_before_synthetic() -> None:
    provider = AutoFallbackProvider(
        primary=_FailingProvider(),
        secondary=_StaticProvider(),
        fallback=SyntheticForexDataProvider(ProviderSettings(name="synthetic")),
    )
    df = provider.get_ohlcv("EUR/USD", Timeframe.M15)
    assert df.attrs["provider"] == "static"
    assert "Yahoo fallback" in df.attrs["warning"]


def test_synthetic_provider_is_blocked_by_production_config() -> None:
    with pytest.raises(ValueError, match="synthetic provider is disabled in production"):
        ProviderSettings(name="synthetic", environment="production", fallback_to_synthetic=False)


def test_data_quality_diagnostic_flags_missing_spread_and_stale_data() -> None:
    df = make_ohlcv(rows=140).drop(columns=["spread"])
    quality = assess_data_quality(df, Timeframe.M15, end=datetime(2025, 1, 4, tzinfo=timezone.utc), duplicate_bars=2)
    assert quality.score < 100.0
    assert not quality.spread_available
    assert quality.duplicate_bars == 2
    assert quality.warnings
