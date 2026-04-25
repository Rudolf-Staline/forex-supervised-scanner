"""Trend-structure helpers built from swing highs and lows."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.types import DirectionBias


def detect_structure(df: pd.DataFrame, swing_count: int = 4) -> tuple[DirectionBias, float, str]:
    """Classify recent swing structure as bullish, bearish, or neutral."""

    highs = df["swing_high"].dropna().tail(swing_count).to_numpy()
    lows = df["swing_low"].dropna().tail(swing_count).to_numpy()
    if len(highs) < 2 or len(lows) < 2:
        return DirectionBias.NO_TRADE, 35.0, "not enough confirmed swing points"

    high_slope = _monotonic_score(highs)
    low_slope = _monotonic_score(lows)

    if high_slope > 0 and low_slope > 0:
        quality = min(100.0, 50.0 + 25.0 * high_slope + 25.0 * low_slope)
        return DirectionBias.LONG, quality, "higher highs and higher lows"
    if high_slope < 0 and low_slope < 0:
        quality = min(100.0, 50.0 + 25.0 * abs(high_slope) + 25.0 * abs(low_slope))
        return DirectionBias.SHORT, quality, "lower highs and lower lows"

    mixed_quality = max(25.0, 55.0 - 20.0 * abs(high_slope - low_slope))
    return DirectionBias.NO_TRADE, mixed_quality, "mixed swing structure"


def _monotonic_score(values: np.ndarray) -> float:
    diffs = np.diff(values)
    if len(diffs) == 0:
        return 0.0
    positive = np.count_nonzero(diffs > 0)
    negative = np.count_nonzero(diffs < 0)
    total = len(diffs)
    return (positive - negative) / total

