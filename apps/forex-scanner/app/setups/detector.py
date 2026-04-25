"""Rules-based setup detection for trend continuation, breakout, and mean reversion."""

from __future__ import annotations

import math

import pandas as pd

from app.config.settings import AppSettings, StyleSettings
from app.core.types import DirectionBias, MarketRegime, RawSetup, RegimeResult, SetupFamily, SetupSubtype, TradingStyle
from app.indicators.calculations import latest_complete_row
from app.indicators.levels import LevelSet, nearest_resistance, nearest_support


def detect_setups(
    symbol: str,
    style: TradingStyle,
    higher_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    trigger_df: pd.DataFrame,
    higher_regime: RegimeResult,
    entry_regime: RegimeResult,
    trigger_regime: RegimeResult,
    levels: LevelSet,
    settings: AppSettings,
) -> list[RawSetup]:
    """Detect all enabled setup families for one symbol and style."""

    style_settings = settings.styles[style]
    setups: list[RawSetup] = []
    if settings.setups.enabled[SetupFamily.TREND_CONTINUATION]:
        setups.extend(
            _trend_continuation(
                symbol,
                style,
                entry_df,
                trigger_df,
                higher_regime,
                entry_regime,
                trigger_regime,
                levels,
                style_settings,
                settings,
            )
        )
    if settings.setups.enabled[SetupFamily.BREAKOUT_CONFIRMATION]:
        setups.extend(
            _breakout_confirmation(
                symbol,
                style,
                entry_df,
                trigger_df,
                higher_regime,
                entry_regime,
                trigger_regime,
                levels,
                style_settings,
                settings,
            )
        )
    if settings.setups.enabled[SetupFamily.MEAN_REVERSION]:
        setups.extend(
            _mean_reversion(
                symbol,
                style,
                entry_df,
                trigger_df,
                higher_regime,
                entry_regime,
                trigger_regime,
                levels,
                style_settings,
                settings,
            )
        )
    return setups


def _trend_continuation(
    symbol: str,
    style: TradingStyle,
    entry_df: pd.DataFrame,
    trigger_df: pd.DataFrame,
    higher_regime: RegimeResult,
    entry_regime: RegimeResult,
    trigger_regime: RegimeResult,
    levels: LevelSet,
    style_settings: StyleSettings,
    settings: AppSettings,
) -> list[RawSetup]:
    row = latest_complete_row(entry_df)
    trigger_row = latest_complete_row(trigger_df)
    entry = float(row["close"])
    atr = float(row["atr_14"])
    tolerance = settings.setups.pullback_ema_tolerance_atr * atr
    recent = entry_df.dropna(subset=["ema_20", "ema_50"]).tail(18)
    setups: list[RawSetup] = []

    if higher_regime.regime in {MarketRegime.TRENDING_UP, MarketRegime.WEAK_TREND_UP}:
        touched_ema20 = bool((recent["low"] <= recent["ema_20"] + tolerance).any())
        touched_ema50 = bool((recent["low"] <= recent["ema_50"] + tolerance).any())
        touched_dynamic_support = bool(
            touched_ema20
            or touched_ema50
        )
        recovered = entry > float(row["ema_20"]) and float(trigger_row["close"]) > float(trigger_row["ema_20"])
        momentum_ok = float(row["rsi_14"]) >= 46.0 or float(trigger_row["macd_hist"]) >= 0.0
        above_long_term = entry > float(row["ema_200"])
        conditions = {
            "pullback touched EMA20/EMA50 support": touched_dynamic_support,
            "entry and trigger recovered above EMA20": recovered,
            "momentum stopped fighting the trend": momentum_ok,
        }
        passed = sum(conditions.values())
        if passed >= 2 and above_long_term:
            support = nearest_support(levels, entry)
            missing = [label for label, ok in conditions.items() if not ok]
            early = passed < 3 or higher_regime.regime == MarketRegime.WEAK_TREND_UP
            setups.append(
                _build_setup(
                    symbol=symbol,
                    style=style,
                    family=SetupFamily.TREND_CONTINUATION,
                    subtype=_trend_subtype(touched_ema20, touched_ema50, support.price if support else None, entry, atr),
                    direction=DirectionBias.LONG,
                    regime=higher_regime.regime,
                    entry=entry,
                    row=row,
                    entry_df=entry_df,
                    levels=levels,
                    level_price=support.price if support else None,
                    style_settings=style_settings,
                    higher_regime=higher_regime,
                    entry_regime=entry_regime,
                    trigger_regime=trigger_regime,
                    explanation=(
                        "Bullish pullback context is active with higher-timeframe uptrend intact."
                        if early
                        else "Bullish pullback recovered above short-term EMA with higher-timeframe uptrend intact."
                    ),
                    invalidation="bullish pullback is invalidated below the recent swing low or EMA50 structure",
                    missing_conditions=missing,
                    watchlist_candidate=early,
                )
            )

    if higher_regime.regime in {MarketRegime.TRENDING_DOWN, MarketRegime.WEAK_TREND_DOWN}:
        touched_ema20 = bool((recent["high"] >= recent["ema_20"] - tolerance).any())
        touched_ema50 = bool((recent["high"] >= recent["ema_50"] - tolerance).any())
        touched_dynamic_resistance = bool(
            touched_ema20
            or touched_ema50
        )
        recovered = entry < float(row["ema_20"]) and float(trigger_row["close"]) < float(trigger_row["ema_20"])
        momentum_ok = float(row["rsi_14"]) <= 54.0 or float(trigger_row["macd_hist"]) <= 0.0
        below_long_term = entry < float(row["ema_200"])
        conditions = {
            "pullback touched EMA20/EMA50 resistance": touched_dynamic_resistance,
            "entry and trigger recovered below EMA20": recovered,
            "momentum stopped fighting the downtrend": momentum_ok,
        }
        passed = sum(conditions.values())
        if passed >= 2 and below_long_term:
            resistance = nearest_resistance(levels, entry)
            missing = [label for label, ok in conditions.items() if not ok]
            early = passed < 3 or higher_regime.regime == MarketRegime.WEAK_TREND_DOWN
            setups.append(
                _build_setup(
                    symbol=symbol,
                    style=style,
                    family=SetupFamily.TREND_CONTINUATION,
                    subtype=_trend_subtype(touched_ema20, touched_ema50, resistance.price if resistance else None, entry, atr),
                    direction=DirectionBias.SHORT,
                    regime=higher_regime.regime,
                    entry=entry,
                    row=row,
                    entry_df=entry_df,
                    levels=levels,
                    level_price=resistance.price if resistance else None,
                    style_settings=style_settings,
                    higher_regime=higher_regime,
                    entry_regime=entry_regime,
                    trigger_regime=trigger_regime,
                    explanation=(
                        "Bearish pullback context is active with higher-timeframe downtrend intact."
                        if early
                        else "Bearish pullback rejected short-term EMA with higher-timeframe downtrend intact."
                    ),
                    invalidation="bearish pullback is invalidated above the recent swing high or EMA50 structure",
                    missing_conditions=missing,
                    watchlist_candidate=early,
                )
            )
    return setups


def _breakout_confirmation(
    symbol: str,
    style: TradingStyle,
    entry_df: pd.DataFrame,
    trigger_df: pd.DataFrame,
    higher_regime: RegimeResult,
    entry_regime: RegimeResult,
    trigger_regime: RegimeResult,
    levels: LevelSet,
    style_settings: StyleSettings,
    settings: AppSettings,
) -> list[RawSetup]:
    row = latest_complete_row(entry_df)
    trigger_row = latest_complete_row(trigger_df)
    entry = float(row["close"])
    atr = float(row["atr_14"])
    buffer = settings.setups.breakout_buffer_atr * atr
    lookback = min(140, max(55, style_settings.lookback_bars // 4))
    prior = entry_df.dropna(subset=["atr_14"]).tail(lookback).iloc[:-1]
    if len(prior) < 35:
        return []
    resistance = float(prior["high"].max())
    support = float(prior["low"].min())
    setups: list[RawSetup] = []
    regime_ok = higher_regime.regime in {
        MarketRegime.BREAKOUT_CANDIDATE,
        MarketRegime.TRENDING_UP,
        MarketRegime.TRENDING_DOWN,
        MarketRegime.WEAK_TREND_UP,
        MarketRegime.WEAK_TREND_DOWN,
        MarketRegime.TRANSITION,
        MarketRegime.RANGING,
    }

    trigger_close = float(trigger_row["close"])
    previous_close = float(entry_df["close"].iloc[-2]) if len(entry_df) >= 2 else entry
    previous_low = float(entry_df["low"].iloc[-2]) if len(entry_df) >= 2 else entry
    previous_high = float(entry_df["high"].iloc[-2]) if len(entry_df) >= 2 else entry
    squeeze = _is_squeeze(entry_df)

    if regime_ok:
        close_breakout = entry > resistance + buffer and trigger_close > resistance
        retest_breakout = previous_close > resistance + buffer and previous_low <= resistance + buffer and entry > resistance
        wick_confirmation = float(row["high"]) > resistance + buffer and entry > resistance - buffer * 0.25 and trigger_close > resistance
        momentum_ok = float(row["rsi_14"]) >= 53.0 or float(trigger_row["macd_hist"]) > 0.0
        signal_ok = close_breakout or retest_breakout or wick_confirmation
        missing = []
        if not close_breakout:
            missing.append("full close beyond resistance")
        if not momentum_ok:
            missing.append("bullish momentum confirmation")
        if signal_ok and momentum_ok:
            early = not close_breakout
            setup = _build_setup(
                symbol=symbol,
                style=style,
                family=SetupFamily.BREAKOUT_CONFIRMATION,
                subtype=_breakout_subtype(close_breakout, retest_breakout, wick_confirmation, squeeze, float(row["rsi_14"]), float(trigger_row["macd_hist"]), DirectionBias.LONG),
                direction=DirectionBias.LONG,
                regime=MarketRegime.BREAKOUT_CANDIDATE,
                entry=entry,
                row=row,
                entry_df=entry_df,
                levels=levels,
                level_price=resistance,
                style_settings=style_settings,
                higher_regime=higher_regime,
                entry_regime=entry_regime,
                trigger_regime=trigger_regime,
                explanation=(
                    "Bullish breakout is forming through resistance with trigger confirmation."
                    if early
                    else "Bullish breakout closed beyond recent resistance with trigger-timeframe confirmation."
                ),
                invalidation="breakout is invalidated by a close back below the broken resistance zone",
                missing_conditions=missing,
                watchlist_candidate=early,
            )
            setup.stop_candidates["broken_resistance"] = resistance - buffer
            setups.append(setup)

    if regime_ok:
        close_breakout = entry < support - buffer and trigger_close < support
        retest_breakout = previous_close < support - buffer and previous_high >= support - buffer and entry < support
        wick_confirmation = float(row["low"]) < support - buffer and entry < support + buffer * 0.25 and trigger_close < support
        momentum_ok = float(row["rsi_14"]) <= 47.0 or float(trigger_row["macd_hist"]) < 0.0
        signal_ok = close_breakout or retest_breakout or wick_confirmation
        missing = []
        if not close_breakout:
            missing.append("full close beyond support")
        if not momentum_ok:
            missing.append("bearish momentum confirmation")
        if signal_ok and momentum_ok:
            early = not close_breakout
            setup = _build_setup(
                symbol=symbol,
                style=style,
                family=SetupFamily.BREAKOUT_CONFIRMATION,
                subtype=_breakout_subtype(close_breakout, retest_breakout, wick_confirmation, squeeze, float(row["rsi_14"]), float(trigger_row["macd_hist"]), DirectionBias.SHORT),
                direction=DirectionBias.SHORT,
                regime=MarketRegime.BREAKOUT_CANDIDATE,
                entry=entry,
                row=row,
                entry_df=entry_df,
                levels=levels,
                level_price=support,
                style_settings=style_settings,
                higher_regime=higher_regime,
                entry_regime=entry_regime,
                trigger_regime=trigger_regime,
                explanation=(
                    "Bearish breakout is forming through support with trigger confirmation."
                    if early
                    else "Bearish breakout closed beyond recent support with trigger-timeframe confirmation."
                ),
                invalidation="breakout is invalidated by a close back above the broken support zone",
                missing_conditions=missing,
                watchlist_candidate=early,
            )
            setup.stop_candidates["broken_support"] = support + buffer
            setups.append(setup)
    return setups


def _mean_reversion(
    symbol: str,
    style: TradingStyle,
    entry_df: pd.DataFrame,
    trigger_df: pd.DataFrame,
    higher_regime: RegimeResult,
    entry_regime: RegimeResult,
    trigger_regime: RegimeResult,
    levels: LevelSet,
    style_settings: StyleSettings,
    settings: AppSettings,
) -> list[RawSetup]:
    row = latest_complete_row(entry_df)
    trigger = trigger_df.dropna(subset=["rsi_14", "bb_lower", "bb_upper"]).tail(3)
    trigger_row = latest_complete_row(trigger_df)
    entry = float(row["close"])
    atr = float(row["atr_14"])
    tolerance = settings.setups.level_tolerance_atr * atr
    setups: list[RawSetup] = []
    if higher_regime.regime not in {MarketRegime.RANGING, MarketRegime.BREAKOUT_CANDIDATE, MarketRegime.TRANSITION}:
        return setups

    support = nearest_support(levels, entry)
    support_near = support is not None and abs(entry - support.price) <= tolerance
    lower_band_touch = entry <= float(row["bb_lower"]) + tolerance
    rsi_recovering = len(trigger) >= 2 and float(trigger["rsi_14"].iloc[-1]) >= float(trigger["rsi_14"].iloc[-2])
    range_now = float(row["high"]) - float(row["low"])
    prior_range = float((entry_df["high"] - entry_df["low"]).tail(6).iloc[:-1].mean()) if len(entry_df) >= 6 else range_now
    downside_excess_slowing = float(row["low"]) < float(row["bb_lower"]) - tolerance * 0.25 and range_now <= prior_range * 1.15 and rsi_recovering
    if (support_near or lower_band_touch or downside_excess_slowing) and float(row["rsi_14"]) <= settings.setups.range_rsi_low and rsi_recovering:
        missing = [] if support_near or lower_band_touch else ["clean support or lower-band retest"]
        setups.append(
            _build_setup(
                symbol=symbol,
                style=style,
                family=SetupFamily.MEAN_REVERSION,
                subtype=_mean_reversion_subtype(support_near, lower_band_touch, downside_excess_slowing),
                direction=DirectionBias.LONG,
                regime=MarketRegime.RANGING,
                entry=entry,
                row=row,
                entry_df=entry_df,
                levels=levels,
                level_price=support.price if support else float(row["bb_lower"]),
                style_settings=style_settings,
                higher_regime=higher_regime,
                entry_regime=entry_regime,
                trigger_regime=trigger_regime,
                explanation="Range support or lower Bollinger Band is being tested with RSI beginning to recover.",
                invalidation="range long is invalidated below the support or lower-band rejection zone",
                missing_conditions=missing,
                watchlist_candidate=bool(missing),
            )
        )

    resistance = nearest_resistance(levels, entry)
    resistance_near = resistance is not None and abs(resistance.price - entry) <= tolerance
    upper_band_touch = entry >= float(row["bb_upper"]) - tolerance
    rsi_fading = len(trigger) >= 2 and float(trigger["rsi_14"].iloc[-1]) <= float(trigger["rsi_14"].iloc[-2])
    upside_excess_slowing = float(row["high"]) > float(row["bb_upper"]) + tolerance * 0.25 and range_now <= prior_range * 1.15 and rsi_fading
    if (resistance_near or upper_band_touch or upside_excess_slowing) and float(row["rsi_14"]) >= settings.setups.range_rsi_high and rsi_fading:
        missing = [] if resistance_near or upper_band_touch else ["clean resistance or upper-band retest"]
        setups.append(
            _build_setup(
                symbol=symbol,
                style=style,
                family=SetupFamily.MEAN_REVERSION,
                subtype=_mean_reversion_subtype(resistance_near, upper_band_touch, upside_excess_slowing),
                direction=DirectionBias.SHORT,
                regime=MarketRegime.RANGING,
                entry=entry,
                row=row,
                entry_df=entry_df,
                levels=levels,
                level_price=resistance.price if resistance else float(row["bb_upper"]),
                style_settings=style_settings,
                higher_regime=higher_regime,
                entry_regime=entry_regime,
                trigger_regime=trigger_regime,
                explanation="Range resistance or upper Bollinger Band is being tested with RSI starting to fade.",
                invalidation="range short is invalidated above the resistance or upper-band rejection zone",
                missing_conditions=missing,
                watchlist_candidate=bool(missing),
            )
        )
    return setups


def _build_setup(
    symbol: str,
    style: TradingStyle,
    family: SetupFamily,
    subtype: SetupSubtype,
    direction: DirectionBias,
    regime: MarketRegime,
    entry: float,
    row: pd.Series,
    entry_df: pd.DataFrame,
    levels: LevelSet,
    level_price: float | None,
    style_settings: StyleSettings,
    higher_regime: RegimeResult,
    entry_regime: RegimeResult,
    trigger_regime: RegimeResult,
    explanation: str,
    invalidation: str,
    missing_conditions: list[str] | None = None,
    watchlist_candidate: bool = False,
) -> RawSetup:
    atr = float(row["atr_14"])
    stop_candidates = _stop_candidates(direction, entry, row, entry_df, level_price, style_settings)
    target_candidates = _target_candidates(direction, entry, row, levels, style_settings)
    alignment = _alignment_score(direction, higher_regime, entry_regime, trigger_regime)
    activation_quality = _activation_quality(subtype, direction, entry, row, level_price, atr, missing_conditions or [], watchlist_candidate)
    invalidation_quality = _initial_invalidation_quality(direction, entry, atr, stop_candidates, level_price)
    return RawSetup(
        symbol=symbol,
        style=style,
        family=family,
        subtype=subtype,
        regime=regime,
        direction=direction,
        entry=entry,
        stop_candidates=stop_candidates,
        target_candidates=target_candidates,
        trend_clarity=higher_regime.trend_clarity,
        structure_quality=max(higher_regime.structure_quality, entry_regime.structure_quality * 0.9),
        mtf_alignment=alignment,
        volatility_suitability=min(higher_regime.volatility_score, entry_regime.volatility_score),
        momentum_confirmation=_momentum_confirmation(direction, row, trigger_regime),
        level_proximity=_level_proximity(direction, entry, atr, level_price),
        activation_quality=activation_quality,
        invalidation_quality=invalidation_quality,
        atr=atr,
        key_level_distances=_key_level_distances(direction, entry, atr, levels, level_price),
        explanation=explanation,
        invalidation_notes=[invalidation],
        missing_conditions=missing_conditions or [],
        watchlist_candidate=watchlist_candidate,
    )


def _trend_subtype(touched_ema20: bool, touched_ema50: bool, level_price: float | None, entry: float, atr: float) -> SetupSubtype:
    if level_price is not None and atr > 0.0 and abs(entry - level_price) <= atr * 0.75:
        return SetupSubtype.RETEST_CONTINUATION
    if touched_ema50:
        return SetupSubtype.EMA50_PULLBACK
    if touched_ema20:
        return SetupSubtype.SHALLOW_EMA20_PULLBACK
    return SetupSubtype.RETEST_CONTINUATION


def _breakout_subtype(
    close_breakout: bool,
    retest_breakout: bool,
    wick_confirmation: bool,
    squeeze: bool,
    rsi: float,
    macd_hist: float,
    direction: DirectionBias,
) -> SetupSubtype:
    if squeeze:
        return SetupSubtype.SQUEEZE_BREAKOUT
    if retest_breakout:
        return SetupSubtype.BREAKOUT_RETEST
    momentum = (direction == DirectionBias.LONG and (rsi >= 60.0 or macd_hist > 0.0)) or (
        direction == DirectionBias.SHORT and (rsi <= 40.0 or macd_hist < 0.0)
    )
    if wick_confirmation and momentum:
        return SetupSubtype.MOMENTUM_BREAKOUT
    if close_breakout:
        return SetupSubtype.BREAKOUT_CLOSE
    return SetupSubtype.MOMENTUM_BREAKOUT


def _mean_reversion_subtype(level_near: bool, band_touch: bool, volatility_excess_slowing: bool) -> SetupSubtype:
    if level_near:
        return SetupSubtype.RANGE_EDGE_REVERSAL
    if volatility_excess_slowing:
        return SetupSubtype.VOLATILITY_SPIKE_FADE
    if band_touch and not level_near:
        return SetupSubtype.BOLLINGER_SNAPBACK
    return SetupSubtype.RANGE_EDGE_REVERSAL


def _is_squeeze(entry_df: pd.DataFrame) -> bool:
    if "bb_width" not in entry_df:
        return False
    width = entry_df["bb_width"].dropna().tail(90)
    if len(width) < 30:
        return False
    latest = float(width.iloc[-1])
    percentile = float((width <= latest).mean() * 100.0)
    return percentile <= 28.0


def _activation_quality(
    subtype: SetupSubtype,
    direction: DirectionBias,
    entry: float,
    row: pd.Series,
    level_price: float | None,
    atr: float,
    missing_conditions: list[str],
    watchlist_candidate: bool,
) -> float:
    score = 82.0
    if watchlist_candidate:
        score -= 14.0
    score -= min(20.0, len(missing_conditions) * 7.0)
    if atr > 0.0 and level_price is not None:
        distance_atr = abs(entry - level_price) / atr
        if distance_atr <= 0.4:
            score += 8.0
        elif distance_atr > 1.4:
            score -= min(24.0, (distance_atr - 1.4) * 12.0)
    if atr > 0.0 and "ema_20" in row:
        extension_atr = abs(entry - float(row["ema_20"])) / atr
        if extension_atr > 1.25 and subtype in {
            SetupSubtype.SHALLOW_EMA20_PULLBACK,
            SetupSubtype.EMA50_PULLBACK,
            SetupSubtype.BREAKOUT_CLOSE,
            SetupSubtype.MOMENTUM_BREAKOUT,
        }:
            score -= min(22.0, (extension_atr - 1.25) * 16.0)
    if direction == DirectionBias.NO_TRADE:
        score = 0.0
    return max(0.0, min(100.0, score))


def _initial_invalidation_quality(
    direction: DirectionBias,
    entry: float,
    atr: float,
    stop_candidates: dict[str, float],
    level_price: float | None,
) -> float:
    if atr <= 0.0 or not stop_candidates:
        return 45.0
    directional = [
        abs(entry - price) / atr
        for price in stop_candidates.values()
        if (direction == DirectionBias.LONG and price < entry) or (direction == DirectionBias.SHORT and price > entry)
    ]
    if not directional:
        return 30.0
    best = max(directional)
    if 0.8 <= best <= 2.8:
        score = 82.0
    elif best < 0.8:
        score = 48.0 + best * 35.0
    else:
        score = max(38.0, 100.0 - (best - 2.8) * 18.0)
    if level_price is not None:
        score += 6.0
    return max(0.0, min(100.0, score))


def _key_level_distances(direction: DirectionBias, entry: float, atr: float, levels: LevelSet, level_price: float | None) -> dict[str, float]:
    if atr <= 0.0:
        return {}
    support = nearest_support(levels, entry)
    resistance = nearest_resistance(levels, entry)
    distances: dict[str, float] = {}
    if support is not None:
        distances["nearest_support_atr"] = round(abs(entry - support.price) / atr, 4)
    if resistance is not None:
        distances["nearest_resistance_atr"] = round(abs(resistance.price - entry) / atr, 4)
    if level_price is not None:
        distances["setup_level_atr"] = round(abs(entry - level_price) / atr, 4)
    if direction == DirectionBias.LONG and resistance is not None:
        distances["next_target_zone_atr"] = round(max(0.0, resistance.price - entry) / atr, 4)
    if direction == DirectionBias.SHORT and support is not None:
        distances["next_target_zone_atr"] = round(max(0.0, entry - support.price) / atr, 4)
    return distances


def _stop_candidates(
    direction: DirectionBias,
    entry: float,
    row: pd.Series,
    entry_df: pd.DataFrame,
    level_price: float | None,
    style_settings: StyleSettings,
) -> dict[str, float]:
    atr = float(row["atr_14"])
    buffer = style_settings.swing_buffer_atr * atr
    recent = entry_df.tail(80)
    swing_lows = recent["swing_low"].dropna()
    swing_highs = recent["swing_high"].dropna()
    candidates: dict[str, float] = {}
    if direction == DirectionBias.LONG:
        candidates["atr"] = entry - style_settings.atr_stop_multiplier * atr
        candidates["ema50_structure"] = float(row["ema_50"]) - buffer
        candidates["recent_swing"] = (float(swing_lows.iloc[-1]) if not swing_lows.empty else float(recent["low"].min())) - buffer
        if level_price is not None and level_price < entry:
            candidates["level_invalidation"] = level_price - buffer
    elif direction == DirectionBias.SHORT:
        candidates["atr"] = entry + style_settings.atr_stop_multiplier * atr
        candidates["ema50_structure"] = float(row["ema_50"]) + buffer
        candidates["recent_swing"] = (float(swing_highs.iloc[-1]) if not swing_highs.empty else float(recent["high"].max())) + buffer
        if level_price is not None and level_price > entry:
            candidates["level_invalidation"] = level_price + buffer
    return _finite_candidates(candidates)


def _target_candidates(
    direction: DirectionBias,
    entry: float,
    row: pd.Series,
    levels: LevelSet,
    style_settings: StyleSettings,
) -> dict[str, float]:
    atr = float(row["atr_14"])
    candidates: dict[str, float] = {}
    if direction == DirectionBias.LONG:
        candidates["atr_extension"] = entry + style_settings.atr_target_multiplier * atr
        candidates["bollinger_mid"] = float(row["bb_mid"])
        candidates["bollinger_outer"] = float(row["bb_upper"])
        resistance = nearest_resistance(levels, entry)
        if resistance is not None:
            candidates["next_resistance"] = resistance.price
    elif direction == DirectionBias.SHORT:
        candidates["atr_extension"] = entry - style_settings.atr_target_multiplier * atr
        candidates["bollinger_mid"] = float(row["bb_mid"])
        candidates["bollinger_outer"] = float(row["bb_lower"])
        support = nearest_support(levels, entry)
        if support is not None:
            candidates["next_support"] = support.price
    return _finite_candidates(candidates)


def _finite_candidates(candidates: dict[str, float]) -> dict[str, float]:
    return {key: value for key, value in candidates.items() if math.isfinite(value) and value > 0.0}


def _alignment_score(
    direction: DirectionBias,
    higher_regime: RegimeResult,
    entry_regime: RegimeResult,
    trigger_regime: RegimeResult,
) -> float:
    weights = [(higher_regime, 45.0), (entry_regime, 35.0), (trigger_regime, 20.0)]
    score = 0.0
    for regime, weight in weights:
        if regime.direction_bias == direction:
            score += weight
        elif regime.direction_bias == DirectionBias.NO_TRADE and regime.regime in {MarketRegime.RANGING, MarketRegime.BREAKOUT_CANDIDATE}:
            score += weight * 0.45
    return min(100.0, score)


def _momentum_confirmation(direction: DirectionBias, row: pd.Series, trigger_regime: RegimeResult) -> float:
    rsi = float(row["rsi_14"])
    macd_hist = float(row["macd_hist"])
    if direction == DirectionBias.LONG:
        score = 50.0 + max(0.0, rsi - 50.0) * 2.0 + (18.0 if macd_hist > 0 else 0.0)
    elif direction == DirectionBias.SHORT:
        score = 50.0 + max(0.0, 50.0 - rsi) * 2.0 + (18.0 if macd_hist < 0 else 0.0)
    else:
        score = 0.0
    if trigger_regime.direction_bias == direction:
        score += 12.0
    return min(100.0, score)


def _level_proximity(direction: DirectionBias, entry: float, atr: float, level_price: float | None) -> float:
    if level_price is None or atr <= 0:
        return 45.0
    distance_atr = abs(entry - level_price) / atr
    if direction == DirectionBias.LONG and level_price <= entry:
        return max(20.0, 100.0 - distance_atr * 32.0)
    if direction == DirectionBias.SHORT and level_price >= entry:
        return max(20.0, 100.0 - distance_atr * 32.0)
    return max(25.0, 70.0 - distance_atr * 18.0)
