"""Shared test fixtures for technical-analysis modules."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.config.settings import AppSettings, load_settings
from app.indicators.calculations import add_indicators


@pytest.fixture
def settings() -> AppSettings:
    return load_settings()


def make_ohlcv(rows: int = 260, trend: float = 0.00035, start: float = 1.0) -> pd.DataFrame:
    index = pd.date_range(datetime(2025, 1, 1, tzinfo=timezone.utc), periods=rows, freq="15min")
    steps = np.arange(rows, dtype=float)
    close = start + trend * steps + np.sin(steps / 9.0) * 0.0015
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + 0.0012
    low = np.minimum(open_, close) - 0.0012
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(rows, 100.0),
            "spread": np.full(rows, 0.00012),
        },
        index=index,
    )


def enriched_trend_frame(rows: int = 260, trend: float = 0.00035) -> pd.DataFrame:
    return add_indicators(make_ohlcv(rows=rows, trend=trend))

