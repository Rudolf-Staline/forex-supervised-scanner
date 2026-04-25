"""Market regime classifier tests."""

import pandas as pd

from app.core.types import DirectionBias, MarketRegime
from app.indicators.calculations import add_indicators
from app.market_regime.regime import MarketRegimeDetector
from app.market_regime.volatility import detect_volatility_regime
from tests.conftest import enriched_trend_frame, make_ohlcv


def _high_volatility_frame() -> pd.DataFrame:
    raw = make_ohlcv(rows=300, trend=0.0, start=1.2)
    for idx in range(280, 300):
        raw.iloc[idx, raw.columns.get_loc("high")] += 0.05
        raw.iloc[idx, raw.columns.get_loc("low")] -= 0.05
        raw.iloc[idx, raw.columns.get_loc("close")] += 0.02 if idx % 2 == 0 else -0.02
    return add_indicators(raw)


def test_detector_classifies_clean_uptrend() -> None:
    df = enriched_trend_frame(trend=0.00045)
    result = MarketRegimeDetector().analyze(df)
    assert result.regime == MarketRegime.TRENDING_UP
    assert result.direction_bias == DirectionBias.LONG
    assert result.trend_clarity >= 55.0


def test_detector_classifies_clean_downtrend() -> None:
    df = enriched_trend_frame(rows=300, trend=-0.00025)
    result = MarketRegimeDetector().analyze(df)
    assert result.regime == MarketRegime.TRENDING_DOWN
    assert result.direction_bias == DirectionBias.SHORT


def test_detector_classifies_range_market() -> None:
    df = enriched_trend_frame(rows=280, trend=0.0)
    result = MarketRegimeDetector().analyze(df)
    assert result.regime == MarketRegime.RANGING
    assert result.direction_bias == DirectionBias.NO_TRADE


def test_detector_classifies_breakout_candidate() -> None:
    df = add_indicators(make_ohlcv(rows=300, trend=0.0, start=1.2))
    last_index = df.index[-1]
    prior_high = float(df.iloc[-81:-1]["high"].max())
    atr = float(df.loc[last_index, "atr_14"])
    close = prior_high - atr * 0.1

    df.loc[last_index, ["open", "close"]] = close
    df.loc[last_index, "high"] = close + atr * 0.2
    df.loc[last_index, "low"] = close - atr * 0.2
    df.loc[last_index, ["ema_20", "ema_50", "ema_200"]] = close
    df.loc[last_index, "rsi_14"] = 50.0
    df.loc[last_index, "macd_hist"] = 0.0
    df.loc[last_index, "bb_width"] = df["bb_width"].dropna().quantile(0.2)

    result = MarketRegimeDetector().analyze(df)
    assert result.regime == MarketRegime.BREAKOUT_CANDIDATE
    assert result.direction_bias == DirectionBias.LONG


def test_detector_classifies_high_volatility_as_no_trade() -> None:
    result = MarketRegimeDetector().analyze(_high_volatility_frame())
    assert result.regime == MarketRegime.HIGH_VOLATILITY
    assert result.direction_bias == DirectionBias.NO_TRADE
    assert result.volatility_score < 25.0


def test_volatility_regime_detector_flags_extreme_conditions() -> None:
    result = detect_volatility_regime(_high_volatility_frame())
    assert result.is_unstable
    assert result.percentile_rank >= 92.0
