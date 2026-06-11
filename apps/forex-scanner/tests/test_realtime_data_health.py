from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.core.types import Timeframe
from app.data.providers import MarketDataProvider
from app.execution.realtime_data_health import (
    RealtimeDataHealthConfig,
    RealtimeDataHealthService,
    RealtimeDataHealthStatus,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FrameProvider(MarketDataProvider):
    name = "mt5"

    def __init__(self, frame: pd.DataFrame | None = None, *, provider_name: str = "mt5", error: Exception | None = None) -> None:
        self.frame = frame
        self.name = provider_name
        self.error = error

    def get_ohlcv(self, symbol: str, timeframe: Timeframe, start=None, end=None) -> pd.DataFrame:
        if self.error is not None:
            raise self.error
        df = self.frame.copy()
        df.attrs.update(self.frame.attrs)
        df.attrs.setdefault("provider", self.name)
        return df


def candles(*, periods: int = 220, end: datetime = NOW, freq: str = "1min", spread: float = 0.00001) -> pd.DataFrame:
    index = pd.date_range(end=end, periods=periods, freq=freq, tz=timezone.utc)
    close = pd.Series([1.10 + i * 0.00001 for i in range(periods)], index=index)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 0.0001,
            "low": close - 0.0001,
            "close": close,
            "volume": 100.0,
            "spread": spread,
        },
        index=index,
    )


def check(provider: MarketDataProvider, tmp_path: Path):
    return RealtimeDataHealthService(provider, now_fn=lambda: NOW).check(
        RealtimeDataHealthConfig(provider=provider.name, symbols=["EUR/USD"], timeframe=Timeframe.M1, reports_dir=tmp_path)
    )


def test_fresh_mt5_like_mocked_candles_pass(tmp_path: Path):
    report = check(FrameProvider(candles()), tmp_path)
    assert report.status == RealtimeDataHealthStatus.REALTIME_DATA_READY
    assert report.safe_for_realtime_paper is True
    assert report.mt5_used is True


def test_stale_candles_block(tmp_path: Path):
    report = check(FrameProvider(candles(end=NOW - timedelta(hours=2))), tmp_path)
    assert report.status == RealtimeDataHealthStatus.BLOCKED_STALE_DATA
    assert report.safe_for_realtime_paper is False


def test_synthetic_fallback_blocks_realtime_paper_mode(tmp_path: Path):
    df = candles()
    df.attrs["provider"] = "synthetic"
    df.attrs["warning"] = "MT5 failed; using synthetic fallback"
    report = check(FrameProvider(df, provider_name="synthetic"), tmp_path)
    assert report.status == RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK
    assert report.synthetic_fallback_used is True


def test_poor_data_quality_blocks(tmp_path: Path):
    df = candles(periods=220, freq="3min")
    report = check(FrameProvider(df), tmp_path)
    assert report.status == RealtimeDataHealthStatus.BLOCKED_POOR_DATA_QUALITY
    assert report.checks[0].missing_bars > 0


def test_high_spread_atr_blocks(tmp_path: Path):
    report = check(FrameProvider(candles(spread=0.01)), tmp_path)
    assert report.status == RealtimeDataHealthStatus.BLOCKED_SPREAD_TOO_WIDE
    assert report.checks[0].spread_atr_ratio is not None


def test_provider_error_blocks(tmp_path: Path):
    report = check(FrameProvider(error=RuntimeError("boom")), tmp_path)
    assert report.status == RealtimeDataHealthStatus.BLOCKED_PROVIDER_FAILURE
    assert "boom" in report.checks[0].error


def test_exports_json_and_txt(tmp_path: Path):
    report = RealtimeDataHealthService(FrameProvider(candles()), now_fn=lambda: NOW).check(
        RealtimeDataHealthConfig(
            provider="mt5",
            symbols=["EUR/USD"],
            timeframe=Timeframe.M1,
            reports_dir=tmp_path,
            export_json=True,
            export_txt=True,
        )
    )
    assert len(report.output_paths) == 2
    assert (tmp_path / "realtime_data_health.json").exists()
    assert (tmp_path / "realtime_data_health.txt").exists()
