"""Indicator calculation tests."""

from app.indicators.calculations import add_indicators
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
