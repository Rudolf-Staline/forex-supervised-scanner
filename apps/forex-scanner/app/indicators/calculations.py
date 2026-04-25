"""Technical indicator calculations used by scanner and backtester."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.data.validation import validate_ohlcv


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA, RSI, MACD, ATR, Bollinger Bands, and swing markers."""

    enriched = validate_ohlcv(df, min_rows=min(120, len(df))).copy()
    close = enriched["close"]
    high = enriched["high"]
    low = enriched["low"]

    enriched["ema_20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    enriched["ema_50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    enriched["ema_200"] = close.ewm(span=200, adjust=False, min_periods=120).mean()
    enriched["rsi_14"] = _rsi(close, period=14)

    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    enriched["macd"] = ema_12 - ema_26
    enriched["macd_signal"] = enriched["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    enriched["macd_hist"] = enriched["macd"] - enriched["macd_signal"]

    enriched["atr_14"] = _atr(high, low, close, period=14)
    bb_mid = close.rolling(20, min_periods=20).mean()
    bb_std = close.rolling(20, min_periods=20).std(ddof=0)
    enriched["bb_mid"] = bb_mid
    enriched["bb_upper"] = bb_mid + 2.0 * bb_std
    enriched["bb_lower"] = bb_mid - 2.0 * bb_std
    enriched["bb_width"] = (enriched["bb_upper"] - enriched["bb_lower"]) / close

    swing_high, swing_low = detect_swings(enriched, window=3)
    enriched["swing_high"] = swing_high
    enriched["swing_low"] = swing_low
    return enriched


def detect_swings(df: pd.DataFrame, window: int = 3) -> tuple[pd.Series, pd.Series]:
    """Detect centered swing highs and lows with a symmetric lookback window."""

    if window < 1:
        raise ValueError("swing window must be positive")
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    swing_high = np.full(len(df), np.nan)
    swing_low = np.full(len(df), np.nan)
    span = window * 2 + 1

    for idx in range(window, len(df) - window):
        high_slice = high[idx - window : idx + window + 1]
        low_slice = low[idx - window : idx + window + 1]
        if len(high_slice) != span or len(low_slice) != span:
            continue
        if high[idx] == np.max(high_slice) and np.sum(high_slice == high[idx]) == 1:
            swing_high[idx] = high[idx]
        if low[idx] == np.min(low_slice) and np.sum(low_slice == low[idx]) == 1:
            swing_low[idx] = low[idx]

    return pd.Series(swing_high, index=df.index), pd.Series(swing_low, index=df.index)


def latest_complete_row(df: pd.DataFrame) -> pd.Series:
    """Return the newest row with all core indicator values populated."""

    required = [
        "ema_20",
        "ema_50",
        "ema_200",
        "rsi_14",
        "macd",
        "macd_signal",
        "atr_14",
        "bb_mid",
        "bb_upper",
        "bb_lower",
    ]
    usable = df.dropna(subset=required)
    if usable.empty:
        raise ValueError("indicator frame has no complete rows")
    return usable.iloc[-1]


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    rsi = rsi.where(avg_gain != 0.0, 0.0)
    return rsi.clip(0.0, 100.0)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

