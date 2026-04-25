"""Market regime classification from trend, structure, momentum, and volatility."""

from __future__ import annotations

import math

import pandas as pd

from app.core.types import DirectionBias, MarketRegime, RegimeResult
from app.indicators.calculations import latest_complete_row
from app.market_regime.structure import detect_structure
from app.market_regime.volatility import detect_volatility_regime


class MarketRegimeDetector:
    """Classify a symbol/timeframe into a technical market regime."""

    def analyze(self, df: pd.DataFrame) -> RegimeResult:
        """Return a market regime result for an indicator-enriched OHLCV frame."""

        try:
            row = latest_complete_row(df)
        except ValueError as exc:
            return RegimeResult(
                regime=MarketRegime.NO_TRADE,
                direction_bias=DirectionBias.NO_TRADE,
                trend_clarity=0.0,
                structure_quality=0.0,
                volatility_score=0.0,
                momentum_score=0.0,
                explanation=str(exc),
            )

        close = float(row["close"])
        atr = float(row["atr_14"])
        if atr <= 0 or not math.isfinite(atr):
            return RegimeResult(
                regime=MarketRegime.NO_TRADE,
                direction_bias=DirectionBias.NO_TRADE,
                trend_clarity=0.0,
                structure_quality=0.0,
                volatility_score=0.0,
                momentum_score=0.0,
                explanation="ATR is unavailable or non-positive",
            )

        structure_bias, structure_quality, structure_text = detect_structure(df)
        ema_bias, ema_clarity = _ema_trend_bias(row, df)
        momentum_bias, momentum_score = _momentum_bias(row)
        volatility = detect_volatility_regime(df)
        breakout_candidate = _is_breakout_candidate(df, row)

        directional_votes = [bias for bias in (ema_bias, structure_bias, momentum_bias) if bias != DirectionBias.NO_TRADE]
        long_votes = directional_votes.count(DirectionBias.LONG)
        short_votes = directional_votes.count(DirectionBias.SHORT)

        if volatility.is_unstable:
            regime = MarketRegime.HIGH_VOLATILITY
            direction = DirectionBias.NO_TRADE
            explanation = volatility.explanation
        elif ema_bias == DirectionBias.LONG and (
            (long_votes >= 2 and ema_clarity >= 55.0)
            or (long_votes >= 1 and short_votes == 0 and ema_clarity >= 75.0)
        ):
            regime = MarketRegime.TRENDING_UP
            direction = DirectionBias.LONG
            explanation = f"EMA alignment is bullish and structure shows {structure_text}"
        elif ema_bias == DirectionBias.SHORT and (
            (short_votes >= 2 and ema_clarity >= 55.0)
            or (short_votes >= 1 and long_votes == 0 and ema_clarity >= 75.0)
        ):
            regime = MarketRegime.TRENDING_DOWN
            direction = DirectionBias.SHORT
            explanation = f"EMA alignment is bearish and structure shows {structure_text}"
        elif breakout_candidate:
            regime = MarketRegime.BREAKOUT_CANDIDATE
            direction = DirectionBias.LONG if long_votes >= short_votes else DirectionBias.SHORT
            explanation = "price is pressing a recent range boundary with usable volatility"
        elif ema_bias == DirectionBias.LONG and long_votes >= 1 and ema_clarity >= 45.0 and short_votes == 0:
            regime = MarketRegime.WEAK_TREND_UP
            direction = DirectionBias.LONG
            explanation = f"bullish trend evidence is developing but not fully confirmed; structure shows {structure_text}"
        elif ema_bias == DirectionBias.SHORT and short_votes >= 1 and ema_clarity >= 45.0 and long_votes == 0:
            regime = MarketRegime.WEAK_TREND_DOWN
            direction = DirectionBias.SHORT
            explanation = f"bearish trend evidence is developing but not fully confirmed; structure shows {structure_text}"
        elif ema_clarity <= 48.0 and volatility.suitability_score >= 35.0:
            regime = MarketRegime.RANGING
            direction = DirectionBias.NO_TRADE
            explanation = f"trend clarity is muted with {structure_text}"
        else:
            regime = MarketRegime.TRANSITION
            direction = DirectionBias.NO_TRADE
            explanation = "technical evidence is mixed and transitioning across trend, momentum, and structure"

        return RegimeResult(
            regime=regime,
            direction_bias=direction,
            trend_clarity=ema_clarity,
            structure_quality=structure_quality,
            volatility_score=volatility.suitability_score,
            momentum_score=momentum_score,
            explanation=explanation,
        )


def _ema_trend_bias(row: pd.Series, df: pd.DataFrame) -> tuple[DirectionBias, float]:
    close = float(row["close"])
    ema20 = float(row["ema_20"])
    ema50 = float(row["ema_50"])
    ema200 = float(row["ema_200"])
    atr = float(row["atr_14"])
    spread_score = min(abs(ema20 - ema200) / max(atr, 1e-12), 4.0) / 4.0
    ema50_series = df["ema_50"].dropna()
    if len(ema50_series) > 12:
        slope = (float(ema50_series.iloc[-1]) - float(ema50_series.iloc[-12])) / max(atr, 1e-12)
    else:
        slope = 0.0
    slope_score = min(abs(slope), 3.0) / 3.0

    if close > ema20 > ema50 > ema200 and slope > 0:
        return DirectionBias.LONG, 45.0 + 35.0 * spread_score + 20.0 * slope_score
    if close < ema20 < ema50 < ema200 and slope < 0:
        return DirectionBias.SHORT, 45.0 + 35.0 * spread_score + 20.0 * slope_score
    if close > ema50 > ema200 and slope > 0:
        return DirectionBias.LONG, 42.0 + 28.0 * spread_score + 18.0 * slope_score
    if close < ema50 < ema200 and slope < 0:
        return DirectionBias.SHORT, 42.0 + 28.0 * spread_score + 18.0 * slope_score

    compressed = min(abs(ema20 - ema50) / max(atr, 1e-12), 2.0) / 2.0
    return DirectionBias.NO_TRADE, max(15.0, 52.0 - 35.0 * (1.0 - compressed))


def _momentum_bias(row: pd.Series) -> tuple[DirectionBias, float]:
    rsi = float(row["rsi_14"])
    macd_hist = float(row["macd_hist"])
    if rsi >= 55.0 and macd_hist > 0:
        return DirectionBias.LONG, min(100.0, 55.0 + (rsi - 55.0) * 2.0)
    if rsi <= 45.0 and macd_hist < 0:
        return DirectionBias.SHORT, min(100.0, 55.0 + (45.0 - rsi) * 2.0)
    if 45.0 < rsi < 55.0:
        return DirectionBias.NO_TRADE, 45.0
    return DirectionBias.NO_TRADE, 52.0


def _is_breakout_candidate(df: pd.DataFrame, row: pd.Series) -> bool:
    recent = df.dropna(subset=["atr_14"]).tail(90)
    if len(recent) < 40:
        return False
    close = float(row["close"])
    atr = float(row["atr_14"])
    prior = recent.iloc[:-1]
    recent_high = float(prior["high"].max())
    recent_low = float(prior["low"].min())
    distance_to_high = abs(recent_high - close) / max(atr, 1e-12)
    distance_to_low = abs(close - recent_low) / max(atr, 1e-12)
    bb_width = recent["bb_width"].dropna()
    if bb_width.empty:
        return False
    width_rank = float((bb_width <= float(bb_width.iloc[-1])).mean())
    near_boundary = min(distance_to_high, distance_to_low) <= 0.7
    recent_close = recent["close"].tail(6)
    pressure = close >= float(recent_close.quantile(0.72)) or close <= float(recent_close.quantile(0.28))
    return near_boundary and width_rank <= 0.7 and pressure
