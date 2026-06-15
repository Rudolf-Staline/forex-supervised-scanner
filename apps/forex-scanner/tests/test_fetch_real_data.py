"""Tests for the local real-data acquisition utility (no network access)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.config.settings import ProviderSettings
from app.core.types import Timeframe
from app.data.providers import CsvHistoricalProvider
from fetch_real_data import (  # noqa: E402
    aggregate_m1,
    combine_bid_ask,
    parse_histdata_m1,
    write_output_csv,
)


def _ohlc(index: pd.DatetimeIndex, base: float, spread_offset: float = 0.0) -> pd.DataFrame:
    steps = np.arange(len(index), dtype=float)
    close = base + 0.0004 * np.sin(steps / 4.0) + spread_offset
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + 0.0002
    low = np.minimum(open_, close) - 0.0002
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": np.full(len(index), 100.0)}, index=index)


def test_combine_bid_ask_computes_real_spread_and_uses_bid_ohlc() -> None:
    idx = pd.date_range("2026-01-01", periods=30, freq="1h", tz="UTC")
    bid = _ohlc(idx, 1.1000)
    ask = _ohlc(idx, 1.1000, spread_offset=0.00015)  # ask ~1.5 pip above bid
    out = combine_bid_ask(bid, ask)

    assert list(out.columns) == ["open", "high", "low", "close", "volume", "spread"]
    # OHLC come from the bid side.
    np.testing.assert_allclose(out["close"].to_numpy(), bid["close"].to_numpy())
    # Spread = ask_close - bid_close, ~0.00015, never negative.
    np.testing.assert_allclose(out["spread"].to_numpy(), 0.00015, atol=1e-9)
    assert (out["spread"] >= 0).all()


def test_combine_bid_ask_aligns_on_shared_timestamps() -> None:
    idx = pd.date_range("2026-01-01", periods=30, freq="1h", tz="UTC")
    bid = _ohlc(idx, 1.10)
    ask = _ohlc(idx[5:], 1.10, spread_offset=0.0001)  # ask missing first 5 bars
    out = combine_bid_ask(bid, ask)
    assert len(out) == 25
    assert out.index.min() == idx[5]


def test_aggregate_m1_to_m5_and_m15() -> None:
    idx = pd.date_range("2026-01-01 00:00", periods=30, freq="1min", tz="UTC")
    m1 = _ohlc(idx, 1.10)
    m1["spread"] = 0.0001

    m5 = aggregate_m1(m1, Timeframe.M5)
    assert len(m5) == 6
    first = m5.iloc[0]
    # open=first, high=max, low=min, close=last, volume=sum over the 5 source bars.
    assert first["open"] == pytest.approx(m1["open"].iloc[0])
    assert first["high"] == pytest.approx(m1["high"].iloc[:5].max())
    assert first["low"] == pytest.approx(m1["low"].iloc[:5].min())
    assert first["close"] == pytest.approx(m1["close"].iloc[4])
    assert first["volume"] == pytest.approx(m1["volume"].iloc[:5].sum())

    m15 = aggregate_m1(m1, Timeframe.M15)
    assert len(m15) == 2


def test_write_output_csv_roundtrips_through_provider(tmp_path: Path) -> None:
    idx = pd.date_range("2026-01-01", periods=40, freq="1h", tz="UTC")
    frame = combine_bid_ask(_ohlc(idx, 1.10), _ohlc(idx, 1.10, spread_offset=0.0001))
    result = write_output_csv(frame, "EUR/USD", Timeframe.H1, tmp_path)

    assert result.path.name == "EURUSD_H1.csv"
    assert result.rows == 40
    # The written file must load cleanly through the real-data provider.
    provider = CsvHistoricalProvider(ProviderSettings(name="csv", csv_data_dir=str(tmp_path)), min_rows=10)
    loaded = provider.get_ohlcv("EUR/USD", Timeframe.H1)
    assert len(loaded) == 40
    assert loaded.attrs["spread_available"] is True
    np.testing.assert_allclose(loaded["spread"].to_numpy(), 0.0001, atol=1e-9)


def test_parse_histdata_m1_converts_to_utc(tmp_path: Path) -> None:
    path = tmp_path / "EURUSD_M1.csv"
    # HistData generic ASCII: 'YYYYMMDD HHMMSS;O;H;L;C;V', EST (Etc/GMT+5) by default.
    lines = [
        "20260102 080000;1.10000;1.10050;1.09980;1.10030;0",
        "20260102 080100;1.10030;1.10070;1.10010;1.10060;0",
    ]
    path.write_text("\n".join(lines) + "\n")
    df = parse_histdata_m1(path)
    assert str(df.index.tz) == "UTC"
    # 08:00 EST (Etc/GMT+5) == 13:00 UTC.
    assert df.index[0] == pd.Timestamp("2026-01-02 13:00:00", tz="UTC")
    assert df["close"].iloc[0] == pytest.approx(1.10030)


def test_histdata_aggregation_leaves_spread_empty(tmp_path: Path) -> None:
    idx = pd.date_range("2026-01-01 00:00", periods=60, freq="1min", tz="UTC")
    m1 = _ohlc(idx, 1.10)  # no spread column (HistData has none)
    m5 = aggregate_m1(m1, Timeframe.M5)
    # validate_ohlcv adds a spread column filled with NaN when absent.
    assert "spread" in m5.columns
    assert m5["spread"].isna().all()


def test_combine_bid_ask_rejects_disjoint_frames() -> None:
    a = _ohlc(pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC"), 1.10)
    b = _ohlc(pd.date_range("2027-01-01", periods=10, freq="1h", tz="UTC"), 1.10)
    with pytest.raises(ValueError, match="share no timestamps"):
        combine_bid_ask(a, b)
