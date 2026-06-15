"""Ingestion tests for the real-data CsvHistoricalProvider."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.config.settings import ProviderSettings
from app.core.types import Timeframe
from app.data.providers import CsvHistoricalProvider, DataProviderError, build_provider


def _valid_frame(rows: int = 60, start: str = "2026-01-01T00:00:00Z", freq: str = "1h", with_spread: bool = True) -> pd.DataFrame:
    index = pd.date_range(start, periods=rows, freq=freq, tz="UTC")
    steps = np.arange(rows, dtype=float)
    close = 1.10 + 0.0005 * np.sin(steps / 5.0)
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + 0.0003
    low = np.minimum(open_, close) - 0.0003
    data = {
        "timestamp": index.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": np.full(rows, 1000.0),
    }
    if with_spread:
        data["spread"] = np.full(rows, 0.00012)
    return pd.DataFrame(data)


def _write(tmp_path: Path, symbol: str, timeframe: Timeframe, frame: pd.DataFrame) -> Path:
    path = tmp_path / f"{symbol.replace('/', '')}_{timeframe.value}.csv"
    frame.to_csv(path, index=False)
    return path


def _provider(tmp_path: Path, min_rows: int = 20) -> CsvHistoricalProvider:
    return CsvHistoricalProvider(ProviderSettings(name="csv", csv_data_dir=str(tmp_path)), min_rows=min_rows)


def test_valid_csv_ingests_with_spread_and_attrs(tmp_path: Path) -> None:
    _write(tmp_path, "EUR/USD", Timeframe.H1, _valid_frame(rows=60))
    df = _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)

    assert list(df.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert "spread" in df.columns and df["spread"].notna().all()
    assert isinstance(df.index, pd.DatetimeIndex) and str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
    assert df.attrs["provider"] == "csv"
    assert df.attrs["spread_available"] is True
    assert df.attrs["source_file"].endswith("EURUSD_H1.csv")


def test_missing_directory_fails_loudly(tmp_path: Path) -> None:
    provider = CsvHistoricalProvider(ProviderSettings(name="csv", csv_data_dir=str(tmp_path / "nope")))
    with pytest.raises(DataProviderError, match="data directory not found"):
        provider.get_ohlcv("EUR/USD", Timeframe.H1)


def test_missing_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(DataProviderError, match="No real CSV history"):
        _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)


def test_empty_file_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "EURUSD_H1.csv"
    path.write_text("")
    with pytest.raises(DataProviderError):
        _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)


def test_broken_schema_missing_column_fails_loudly(tmp_path: Path) -> None:
    frame = _valid_frame(rows=60).drop(columns=["close"])
    _write(tmp_path, "EUR/USD", Timeframe.H1, frame)
    with pytest.raises(DataProviderError, match="missing required column"):
        _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)


def test_too_few_rows_fails_loudly(tmp_path: Path) -> None:
    _write(tmp_path, "EUR/USD", Timeframe.H1, _valid_frame(rows=10))
    with pytest.raises(DataProviderError, match="failed OHLCV validation"):
        _provider(tmp_path, min_rows=50).get_ohlcv("EUR/USD", Timeframe.H1)


def test_unparseable_timestamps_fail_loudly(tmp_path: Path) -> None:
    frame = _valid_frame(rows=60)
    frame["timestamp"] = "not-a-date"
    _write(tmp_path, "EUR/USD", Timeframe.H1, frame)
    with pytest.raises(DataProviderError, match="no parseable UTC timestamps"):
        _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)


def test_spread_optional_absent_marks_unavailable(tmp_path: Path) -> None:
    _write(tmp_path, "EUR/USD", Timeframe.H1, _valid_frame(rows=60, with_spread=False))
    df = _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)
    assert "spread" in df.columns and df["spread"].isna().all()
    assert df.attrs["spread_available"] is False


def test_unsorted_rows_are_sorted(tmp_path: Path) -> None:
    frame = _valid_frame(rows=60).iloc[::-1].reset_index(drop=True)  # reversed
    _write(tmp_path, "EUR/USD", Timeframe.H1, frame)
    df = _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)
    assert df.index.is_monotonic_increasing


def test_gaps_are_ingested_and_flagged(tmp_path: Path) -> None:
    # Drop a contiguous block of bars to create a gap; ingestion must succeed and
    # the data-quality diagnostic must record missing bars (gaps are not fabricated).
    frame = _valid_frame(rows=120)
    frame = pd.concat([frame.iloc[:40], frame.iloc[70:]], ignore_index=True)
    _write(tmp_path, "EUR/USD", Timeframe.H1, frame)
    df = _provider(tmp_path).get_ohlcv("EUR/USD", Timeframe.H1)
    assert df.attrs["data_quality"].missing_bars > 0
    assert df.index.is_monotonic_increasing


def test_date_range_filter(tmp_path: Path) -> None:
    _write(tmp_path, "EUR/USD", Timeframe.H1, _valid_frame(rows=200))
    provider = _provider(tmp_path, min_rows=10)
    df = provider.get_ohlcv("EUR/USD", Timeframe.H1,
                            start=datetime(2026, 1, 2, tzinfo=timezone.utc),
                            end=datetime(2026, 1, 5, tzinfo=timezone.utc))
    assert df.index.min() >= pd.Timestamp("2026-01-02", tz="UTC")
    assert df.index.max() <= pd.Timestamp("2026-01-05", tz="UTC")


def test_build_provider_csv_never_falls_back_to_synthetic(tmp_path: Path) -> None:
    from app.config.settings import load_settings
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = "csv"
    settings.provider.csv_data_dir = str(tmp_path)  # empty -> must raise, not synthesize
    provider = build_provider(settings)
    assert isinstance(provider, CsvHistoricalProvider)
    with pytest.raises(DataProviderError):
        provider.get_ohlcv("EUR/USD", Timeframe.H1)
