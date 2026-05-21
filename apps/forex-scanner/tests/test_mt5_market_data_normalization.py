"""MT5 market-data normalization tests."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.data.providers import mt5_rates_to_ohlcv, to_mt5_symbol
from app.data.validation import validate_ohlcv


def test_mt5_rates_normalize_to_valid_ohlcv_frame() -> None:
    rates = _fake_mt5_rates(150)

    normalized = mt5_rates_to_ohlcv("EUR/USD", rates)
    cleaned = validate_ohlcv(normalized, min_rows=120)

    assert to_mt5_symbol("EUR/USD") == "EURUSD"
    assert list(cleaned.columns) == ["open", "high", "low", "close", "volume", "spread"]
    assert isinstance(cleaned.index, pd.DatetimeIndex)
    assert cleaned.index.tz is not None
    assert len(cleaned) == 150
    assert cleaned[["open", "high", "low", "close", "volume"]].isna().sum().sum() == 0


def _fake_mt5_rates(rows: int) -> np.ndarray:
    start = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    dtype = [
        ("time", "i8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("tick_volume", "i8"),
        ("spread", "i4"),
        ("real_volume", "i8"),
    ]
    rates = np.zeros(rows, dtype=dtype)
    steps = np.arange(rows)
    close = 1.08 + steps * 0.00001
    rates["time"] = start + steps * 300
    rates["open"] = close - 0.00002
    rates["high"] = close + 0.00008
    rates["low"] = close - 0.00008
    rates["close"] = close
    rates["tick_volume"] = 100 + steps
    rates["spread"] = 8
    rates["real_volume"] = 0
    return rates
