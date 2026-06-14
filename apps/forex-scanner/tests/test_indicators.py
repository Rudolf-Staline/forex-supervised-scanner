"""Indicator calculation tests."""

import numpy as np

from app.indicators.calculations import add_indicators, detect_swings
from app.indicators.levels import find_key_levels
from tests.conftest import make_ohlcv


def test_add_indicators_populates_core_columns() -> None:
    df = add_indicators(make_ohlcv())
    latest = df.dropna(subset=["ema_20", "ema_50", "ema_200", "rsi_14", "macd", "atr_14"]).iloc[-1]
    assert latest["ema_20"] > latest["ema_50"] > latest["ema_200"]
    assert 0.0 <= latest["rsi_14"] <= 100.0
    assert latest["atr_14"] > 0.0
    assert "bb_upper" in df.columns
    assert "swing_high" in df.columns


def test_support_resistance_levels_are_inferred_from_swings() -> None:
    df = add_indicators(make_ohlcv(rows=320, trend=0.0, start=1.2))
    levels = find_key_levels(df, lookback=220)
    current_price = float(df["close"].iloc[-1])

    assert levels.supports
    assert levels.resistances
    assert levels.supports[0].price < current_price
    assert levels.resistances[0].price > current_price
    assert levels.supports[0].strength > 0.0


def test_causal_swings_have_no_look_ahead_leak() -> None:
    """Swing columns on a [:T] slice must match indicators computed only on <= T.

    Regression guard for the look-ahead bug where centred swings near the
    backtest cut-off were confirmed using bars *after* the cut-off.
    """

    df = add_indicators(make_ohlcv(rows=400, trend=0.0, start=1.2))
    # Probe several cut-off positions, including ones near the tail where the
    # legacy centred detector would have leaked future confirmation bars.
    for cut in (150, 220, 300, len(df) - 5, len(df) - 1):
        timestamp = df.index[cut]

        sliced = df.loc[:timestamp]
        recomputed = add_indicators(make_ohlcv(rows=400, trend=0.0, start=1.2).loc[:timestamp])

        for column in ("swing_high", "swing_low"):
            left = sliced[column].to_numpy()
            right = recomputed[column].to_numpy()
            assert len(left) == len(right)
            # NaN positions must coincide and finite values must be identical.
            assert np.array_equal(np.isnan(left), np.isnan(right))
            np.testing.assert_allclose(left[~np.isnan(left)], right[~np.isnan(right)])


def test_detect_swings_causal_lags_centred_detection_by_window() -> None:
    df = add_indicators(make_ohlcv(rows=300, trend=0.0, start=1.3))
    window = 3
    centred_high, centred_low = detect_swings(df, window=window, causal=False)
    causal_high, causal_low = detect_swings(df, window=window, causal=True)

    # Every causal confirmation equals a centred pivot shifted forward by window.
    shifted_high = centred_high.shift(window)
    shifted_low = centred_low.shift(window)
    np.testing.assert_allclose(causal_high.to_numpy(), shifted_high.to_numpy(), equal_nan=True)
    np.testing.assert_allclose(causal_low.to_numpy(), shifted_low.to_numpy(), equal_nan=True)
    # The most recent window bars can never carry a confirmed swing.
    assert causal_high.tail(window).isna().all()
    assert causal_low.tail(window).isna().all()
