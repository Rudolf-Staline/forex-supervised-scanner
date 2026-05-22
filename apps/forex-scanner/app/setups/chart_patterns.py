"""Lightweight chart-pattern confluence detection.

These helpers never create executable setups by themselves. They only describe
classic patterns that can enrich an existing scanner setup.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
import pandas as pd

from app.core.types import DirectionBias

MAX_PATTERN_SCORE = 15.0


class ChartPatternSignal(BaseModel):
    """One non-executable chart-pattern hint."""

    pattern_name: str
    direction: DirectionBias
    confidence: float = Field(ge=0.0, le=100.0)
    entry_hint: float | None = Field(default=None, gt=0.0)
    stop_hint: float | None = Field(default=None, gt=0.0)
    target_hint: float | None = Field(default=None, gt=0.0)
    explanation: str


def detect_chart_patterns(frame: pd.DataFrame) -> list[ChartPatternSignal]:
    """Detect a small set of classic patterns from recent OHLC candles."""

    candles = frame.dropna(subset=["open", "high", "low", "close"]).tail(80)
    if len(candles) < 8:
        return []
    patterns: list[ChartPatternSignal] = []
    patterns.extend(_engulfing(candles))
    pin_bar = _pin_bar(candles)
    if pin_bar is not None:
        patterns.append(pin_bar)
    double_top = _double_level(candles, top=True)
    if double_top is not None:
        patterns.append(double_top)
    double_bottom = _double_level(candles, top=False)
    if double_bottom is not None:
        patterns.append(double_bottom)
    breakout = _breakout_retest(candles)
    if breakout is not None:
        patterns.append(breakout)
    return patterns


def pattern_score(patterns: list[ChartPatternSignal], direction: DirectionBias | None = None) -> float:
    """Return a capped 0-15 confluence score for compatible patterns."""

    compatible = [pattern for pattern in patterns if direction is None or pattern.direction == direction]
    if not compatible:
        return 0.0
    best_confidence = max(pattern.confidence for pattern in compatible)
    diversity_bonus = min(10.0, max(0, len(compatible) - 1) * 3.0)
    score = (best_confidence * 0.12) + diversity_bonus
    return round(min(MAX_PATTERN_SCORE, score), 2)


def _engulfing(candles: pd.DataFrame) -> list[ChartPatternSignal]:
    previous = candles.iloc[-2]
    current = candles.iloc[-1]
    previous_body_low = min(float(previous["open"]), float(previous["close"]))
    previous_body_high = max(float(previous["open"]), float(previous["close"]))
    current_body_low = min(float(current["open"]), float(current["close"]))
    current_body_high = max(float(current["open"]), float(current["close"]))
    patterns: list[ChartPatternSignal] = []
    if float(previous["close"]) < float(previous["open"]) and float(current["close"]) > float(current["open"]):
        if current_body_low <= previous_body_low and current_body_high >= previous_body_high:
            patterns.append(
                ChartPatternSignal(
                    pattern_name="bullish_engulfing",
                    direction=DirectionBias.LONG,
                    confidence=72.0,
                    entry_hint=float(current["close"]),
                    stop_hint=float(current["low"]),
                    target_hint=float(current["close"]) + (float(current["close"]) - float(current["low"])) * 1.5,
                    explanation="Bullish engulfing candle confirms buying pressure after a bearish candle.",
                )
            )
    if float(previous["close"]) > float(previous["open"]) and float(current["close"]) < float(current["open"]):
        if current_body_low <= previous_body_low and current_body_high >= previous_body_high:
            patterns.append(
                ChartPatternSignal(
                    pattern_name="bearish_engulfing",
                    direction=DirectionBias.SHORT,
                    confidence=72.0,
                    entry_hint=float(current["close"]),
                    stop_hint=float(current["high"]),
                    target_hint=float(current["close"]) - (float(current["high"]) - float(current["close"])) * 1.5,
                    explanation="Bearish engulfing candle confirms selling pressure after a bullish candle.",
                )
            )
    return patterns


def _pin_bar(candles: pd.DataFrame) -> ChartPatternSignal | None:
    row = candles.iloc[-1]
    open_price = float(row["open"])
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    body = abs(close - open_price)
    candle_range = max(high - low, 1e-12)
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    if lower_wick >= body * 2.2 and lower_wick >= candle_range * 0.48:
        return ChartPatternSignal(
            pattern_name="pin_bar",
            direction=DirectionBias.LONG,
            confidence=64.0,
            entry_hint=close,
            stop_hint=low,
            target_hint=close + (close - low) * 1.5,
            explanation="Bullish pin bar shows lower-wick rejection.",
        )
    if upper_wick >= body * 2.2 and upper_wick >= candle_range * 0.48:
        return ChartPatternSignal(
            pattern_name="pin_bar",
            direction=DirectionBias.SHORT,
            confidence=64.0,
            entry_hint=close,
            stop_hint=high,
            target_hint=close - (high - close) * 1.5,
            explanation="Bearish pin bar shows upper-wick rejection.",
        )
    return None


def _double_level(candles: pd.DataFrame, *, top: bool) -> ChartPatternSignal | None:
    highs = candles["high"].astype(float)
    lows = candles["low"].astype(float)
    closes = candles["close"].astype(float)
    recent = highs if top else lows
    first_window = recent.iloc[-18:-7]
    second_window = recent.iloc[-7:]
    if first_window.empty or second_window.empty:
        return None
    first = float(first_window.max() if top else first_window.min())
    second = float(second_window.max() if top else second_window.min())
    avg_range = float((highs - lows).tail(20).mean())
    tolerance = max(avg_range * 0.35, abs(closes.iloc[-1]) * 0.00025)
    if abs(first - second) > tolerance:
        return None
    close = float(closes.iloc[-1])
    if top and close >= second - tolerance * 0.2:
        return ChartPatternSignal(
            pattern_name="double_top",
            direction=DirectionBias.SHORT,
            confidence=66.0,
            entry_hint=close,
            stop_hint=max(first, second) + tolerance,
            target_hint=close - avg_range * 2.0,
            explanation="Double top resistance is visible; needs broader bearish confirmation.",
        )
    if not top and close <= second + tolerance * 0.2:
        return ChartPatternSignal(
            pattern_name="double_bottom",
            direction=DirectionBias.LONG,
            confidence=66.0,
            entry_hint=close,
            stop_hint=min(first, second) - tolerance,
            target_hint=close + avg_range * 2.0,
            explanation="Double bottom support is visible; needs broader bullish confirmation.",
        )
    return None


def _breakout_retest(candles: pd.DataFrame) -> ChartPatternSignal | None:
    prior = candles.iloc[-30:-3]
    if len(prior) < 12:
        return None
    previous = candles.iloc[-2]
    current = candles.iloc[-1]
    resistance = float(prior["high"].max())
    support = float(prior["low"].min())
    avg_range = float((candles["high"] - candles["low"]).tail(20).mean())
    buffer = max(avg_range * 0.2, float(current["close"]) * 0.00015)
    close = float(current["close"])
    if float(previous["close"]) > resistance + buffer and float(current["low"]) <= resistance + buffer and close > resistance:
        return ChartPatternSignal(
            pattern_name="breakout_retest",
            direction=DirectionBias.LONG,
            confidence=74.0,
            entry_hint=close,
            stop_hint=resistance - buffer,
            target_hint=close + avg_range * 2.0,
            explanation="Bullish breakout retest is visible around prior resistance.",
        )
    if float(previous["close"]) < support - buffer and float(current["high"]) >= support - buffer and close < support:
        return ChartPatternSignal(
            pattern_name="breakout_retest",
            direction=DirectionBias.SHORT,
            confidence=74.0,
            entry_hint=close,
            stop_hint=support + buffer,
            target_hint=close - avg_range * 2.0,
            explanation="Bearish breakout retest is visible around prior support.",
        )
    return None
