"""Setup detection tests."""

from app.core.types import DirectionBias, MarketRegime, PriceLevel, RegimeResult, SetupFamily, SetupSubtype, TradingStyle
from app.indicators.levels import LevelSet
from app.indicators.levels import find_key_levels
from app.setups.detector import detect_setups
from app.setups.detector import _breakout_subtype, _mean_reversion_subtype, _trend_subtype
from tests.conftest import enriched_trend_frame


def _regime(
    regime: MarketRegime,
    direction: DirectionBias,
    trend: float = 82.0,
    structure: float = 78.0,
    volatility: float = 74.0,
    momentum: float = 70.0,
) -> RegimeResult:
    return RegimeResult(
        regime=regime,
        direction_bias=direction,
        trend_clarity=trend,
        structure_quality=structure,
        volatility_score=volatility,
        momentum_score=momentum,
        explanation="test regime",
    )


def test_trend_continuation_detects_bullish_pullback(settings) -> None:
    df = enriched_trend_frame()
    row_idx = df.index[-1]
    df.loc[row_idx, "ema_20"] = df.loc[row_idx, "close"] - 0.0006
    df.loc[row_idx, "ema_50"] = df.loc[row_idx, "close"] - 0.0012
    df.loc[row_idx, "ema_200"] = df.loc[row_idx, "close"] - 0.0060
    df.loc[row_idx, "rsi_14"] = 58.0
    df.loc[row_idx, "macd_hist"] = 0.0002
    df.loc[df.index[-3], "low"] = df.loc[row_idx, "ema_20"] - 0.0001
    df.loc[df.index[-4], "swing_low"] = df.loc[row_idx, "ema_50"] - 0.0010

    trending = _regime(MarketRegime.TRENDING_UP, DirectionBias.LONG)
    levels = find_key_levels(df)
    setups = detect_setups(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        higher_df=df,
        entry_df=df,
        trigger_df=df,
        higher_regime=trending,
        entry_regime=trending,
        trigger_regime=trending,
        levels=levels,
        settings=settings,
    )
    setup = next(item for item in setups if item.family == SetupFamily.TREND_CONTINUATION and item.direction == DirectionBias.LONG)
    assert setup.entry == float(df["close"].iloc[-1])
    assert setup.subtype in {SetupSubtype.SHALLOW_EMA20_PULLBACK, SetupSubtype.EMA50_PULLBACK, SetupSubtype.RETEST_CONTINUATION}
    assert {"atr", "ema50_structure", "recent_swing"}.issubset(setup.stop_candidates)
    assert {"atr_extension", "bollinger_outer"}.issubset(setup.target_candidates)
    assert setup.invalidation_notes


def test_breakout_confirmation_detects_bullish_breakout(settings) -> None:
    df = enriched_trend_frame(rows=300, trend=0.0)
    row_idx = df.index[-1]
    prior_resistance = float(df.iloc[-91:-1]["high"].max())
    atr = float(df.loc[row_idx, "atr_14"])
    entry = prior_resistance + settings.setups.breakout_buffer_atr * atr + atr * 0.2
    df.loc[row_idx, ["open", "close"]] = entry
    df.loc[row_idx, "high"] = entry + atr * 0.3
    df.loc[row_idx, "low"] = entry - atr * 0.2
    df.loc[row_idx, "rsi_14"] = 61.0
    df.loc[row_idx, "macd_hist"] = 0.0002

    breakout = _regime(MarketRegime.BREAKOUT_CANDIDATE, DirectionBias.LONG)
    setups = detect_setups(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        higher_df=df,
        entry_df=df,
        trigger_df=df,
        higher_regime=breakout,
        entry_regime=breakout,
        trigger_regime=breakout,
        levels=find_key_levels(df),
        settings=settings,
    )

    setup = next(item for item in setups if item.family == SetupFamily.BREAKOUT_CONFIRMATION)
    assert setup.direction == DirectionBias.LONG
    assert setup.regime == MarketRegime.BREAKOUT_CANDIDATE
    assert setup.subtype in {
        SetupSubtype.BREAKOUT_CLOSE,
        SetupSubtype.BREAKOUT_RETEST,
        SetupSubtype.SQUEEZE_BREAKOUT,
        SetupSubtype.MOMENTUM_BREAKOUT,
    }
    assert "broken_resistance" in setup.stop_candidates


def test_mean_reversion_detects_range_support_long(settings) -> None:
    df = enriched_trend_frame(rows=300, trend=0.0)
    row_idx = df.index[-1]
    atr = float(df.loc[row_idx, "atr_14"])
    entry = float(df.loc[row_idx, "close"])
    support = entry - atr * 0.15
    resistance = entry + atr * 3.0
    df.loc[row_idx, "rsi_14"] = settings.setups.range_rsi_low - 3.0
    df.loc[df.index[-2], "rsi_14"] = settings.setups.range_rsi_low - 8.0
    df.loc[row_idx, "bb_lower"] = entry - atr * 0.1
    levels = LevelSet(
        supports=[PriceLevel(price=support, kind="support", strength=80.0, touches=3, label="test support")],
        resistances=[PriceLevel(price=resistance, kind="resistance", strength=80.0, touches=3, label="test resistance")],
    )
    ranging = _regime(MarketRegime.RANGING, DirectionBias.NO_TRADE, trend=32.0)

    setups = detect_setups(
        symbol="EUR/USD",
        style=TradingStyle.SCALPING,
        higher_df=df,
        entry_df=df,
        trigger_df=df,
        higher_regime=ranging,
        entry_regime=ranging,
        trigger_regime=ranging,
        levels=levels,
        settings=settings,
    )

    setup = next(item for item in setups if item.family == SetupFamily.MEAN_REVERSION)
    assert setup.direction == DirectionBias.LONG
    assert setup.regime == MarketRegime.RANGING
    assert setup.subtype == SetupSubtype.RANGE_EDGE_REVERSAL
    assert "level_invalidation" in setup.stop_candidates
    assert "next_resistance" in setup.target_candidates


def test_setup_disable_flag_blocks_family(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.setups.enabled[SetupFamily.TREND_CONTINUATION] = False
    df = enriched_trend_frame()
    row_idx = df.index[-1]
    df.loc[row_idx, "ema_20"] = df.loc[row_idx, "close"] - 0.0006
    df.loc[row_idx, "ema_50"] = df.loc[row_idx, "close"] - 0.0012
    df.loc[row_idx, "ema_200"] = df.loc[row_idx, "close"] - 0.0060
    df.loc[row_idx, "rsi_14"] = 58.0
    df.loc[row_idx, "macd_hist"] = 0.0002
    df.loc[df.index[-3], "low"] = df.loc[row_idx, "ema_20"] - 0.0001
    trending = _regime(MarketRegime.TRENDING_UP, DirectionBias.LONG)

    setups = detect_setups(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        higher_df=df,
        entry_df=df,
        trigger_df=df,
        higher_regime=trending,
        entry_regime=trending,
        trigger_regime=trending,
        levels=find_key_levels(df),
        settings=adjusted,
    )

    assert all(setup.family != SetupFamily.TREND_CONTINUATION for setup in setups)


def test_weak_market_conditions_return_no_raw_setups(settings) -> None:
    df = enriched_trend_frame(rows=300, trend=0.0)
    weak = _regime(MarketRegime.NO_TRADE, DirectionBias.NO_TRADE, trend=25.0, structure=25.0, volatility=40.0, momentum=40.0)
    setups = detect_setups(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        higher_df=df,
        entry_df=df,
        trigger_df=df,
        higher_regime=weak,
        entry_regime=weak,
        trigger_regime=weak,
        levels=find_key_levels(df),
        settings=settings,
    )
    assert setups == []


def test_requested_trend_subtype_assignment() -> None:
    assert _trend_subtype(True, False, None, 1.1, 0.001) == SetupSubtype.SHALLOW_EMA20_PULLBACK
    assert _trend_subtype(True, True, None, 1.1, 0.001) == SetupSubtype.EMA50_PULLBACK
    assert _trend_subtype(False, False, 1.0996, 1.1, 0.001) == SetupSubtype.RETEST_CONTINUATION


def test_requested_breakout_subtype_assignment() -> None:
    assert _breakout_subtype(True, False, False, False, 55.0, 0.0, DirectionBias.LONG) == SetupSubtype.BREAKOUT_CLOSE
    assert _breakout_subtype(False, True, False, False, 55.0, 0.0, DirectionBias.LONG) == SetupSubtype.BREAKOUT_RETEST
    assert _breakout_subtype(True, False, False, True, 55.0, 0.0, DirectionBias.LONG) == SetupSubtype.SQUEEZE_BREAKOUT
    assert _breakout_subtype(False, False, True, False, 63.0, 0.0002, DirectionBias.LONG) == SetupSubtype.MOMENTUM_BREAKOUT


def test_requested_mean_reversion_subtype_assignment() -> None:
    assert _mean_reversion_subtype(True, False, False) == SetupSubtype.RANGE_EDGE_REVERSAL
    assert _mean_reversion_subtype(False, True, False) == SetupSubtype.BOLLINGER_SNAPBACK
    assert _mean_reversion_subtype(False, False, True) == SetupSubtype.VOLATILITY_SPIKE_FADE
