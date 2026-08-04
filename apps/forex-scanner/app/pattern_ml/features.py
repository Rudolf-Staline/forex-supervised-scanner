"""Deterministic, scale-invariant feature extraction for chart-shape models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

FEATURE_SCHEMA = "ohlcv-shape-v1"
FEATURE_COLUMNS = (
    "open_scaled",
    "high_scaled",
    "low_scaled",
    "close_scaled",
    "body_to_range",
    "upper_wick_to_range",
    "lower_wick_to_range",
    "log_return",
    "range_to_close",
    "volume_zscore",
)
_REQUIRED_COLUMNS = ("open", "high", "low", "close")


class PatternFeatureError(ValueError):
    """Raised when a market window cannot safely be transformed."""


@dataclass(frozen=True, slots=True)
class PatternFeatureWindow:
    matrix: np.ndarray
    columns: tuple[str, ...]
    start_time: datetime
    end_time: datetime
    latest_close: float

    @property
    def flattened(self) -> np.ndarray:
        return self.matrix.reshape(-1)


def build_shape_features(frame: pd.DataFrame, *, window_size: int) -> PatternFeatureWindow:
    """Build one fixed-size, causal feature window from completed OHLC(V) bars."""

    if window_size < 8:
        raise PatternFeatureError("window_size must be at least 8")
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise PatternFeatureError(f"missing required OHLC columns: {', '.join(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise PatternFeatureError("market frame index must be a DatetimeIndex")
    if not frame.index.is_monotonic_increasing:
        raise PatternFeatureError("market frame index must be monotonic increasing")
    if frame.index.has_duplicates:
        raise PatternFeatureError("market frame index must not contain duplicates")

    columns = [*_REQUIRED_COLUMNS, *(["volume"] if "volume" in frame.columns else [])]
    window = frame.loc[:, columns].tail(window_size).copy()
    if len(window) < window_size:
        raise PatternFeatureError(f"need {window_size} complete bars, received {len(window)}")
    if window.loc[:, _REQUIRED_COLUMNS].isna().any().any():
        raise PatternFeatureError("OHLC window contains missing values")

    ohlc = window.loc[:, _REQUIRED_COLUMNS].astype(float).to_numpy()
    if not np.isfinite(ohlc).all():
        raise PatternFeatureError("OHLC window contains non-finite values")
    open_price, high, low, close = (ohlc[:, index] for index in range(4))
    if np.any(close <= 0.0) or np.any(open_price <= 0.0) or np.any(high <= 0.0) or np.any(low <= 0.0):
        raise PatternFeatureError("OHLC prices must be strictly positive")
    if np.any(high < np.maximum.reduce([open_price, close, low])):
        raise PatternFeatureError("high must be greater than or equal to open, close, and low")
    if np.any(low > np.minimum.reduce([open_price, close, high])):
        raise PatternFeatureError("low must be less than or equal to open, close, and high")

    candle_range = high - low
    positive_ranges = candle_range[candle_range > 0.0]
    scale = float(np.median(positive_ranges)) if positive_ranges.size else float(abs(close[0]) * 1e-6)
    scale = max(scale, float(abs(close[0]) * 1e-9), 1e-12)
    anchor = float(close[0])
    safe_range = np.maximum(candle_range, scale * 1e-6)

    body = close - open_price
    upper_wick = high - np.maximum(open_price, close)
    lower_wick = np.minimum(open_price, close) - low
    log_close = np.log(close)
    log_return = np.diff(log_close, prepend=log_close[0])
    range_to_close = candle_range / close

    if "volume" in window:
        volume = window["volume"].astype(float).to_numpy()
        if not np.isfinite(volume).all() or np.any(volume < 0.0):
            raise PatternFeatureError("volume must contain finite non-negative values")
        volume_std = float(volume.std())
        volume_zscore = (volume - float(volume.mean())) / volume_std if volume_std > 1e-12 else np.zeros_like(volume)
    else:
        volume_zscore = np.zeros_like(close)

    matrix = np.column_stack(
        [
            (open_price - anchor) / scale,
            (high - anchor) / scale,
            (low - anchor) / scale,
            (close - anchor) / scale,
            body / safe_range,
            upper_wick / safe_range,
            lower_wick / safe_range,
            log_return,
            range_to_close,
            volume_zscore,
        ]
    ).astype(np.float64, copy=False)
    if not np.isfinite(matrix).all():
        raise PatternFeatureError("feature matrix contains non-finite values")

    return PatternFeatureWindow(
        matrix=matrix,
        columns=FEATURE_COLUMNS,
        start_time=window.index[0].to_pydatetime(),
        end_time=window.index[-1].to_pydatetime(),
        latest_close=float(close[-1]),
    )
