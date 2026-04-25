"""Risk engine tests."""

import pytest

from app.core.types import DirectionBias, MarketRegime, RawSetup, SetupFamily, TradingStyle
from app.risk.engine import RiskEngine


def _raw_setup(
    direction: DirectionBias = DirectionBias.LONG,
    entry: float = 1.1000,
    stop_candidates: dict[str, float] | None = None,
    target_candidates: dict[str, float] | None = None,
) -> RawSetup:
    return RawSetup(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        family=SetupFamily.TREND_CONTINUATION,
        regime=MarketRegime.TRENDING_UP if direction == DirectionBias.LONG else MarketRegime.TRENDING_DOWN,
        direction=direction,
        entry=entry,
        stop_candidates=stop_candidates or {"atr": 1.0950, "recent_swing": 1.0920, "ema50_structure": 1.0940},
        target_candidates=target_candidates or {"atr_extension": 1.1160},
        trend_clarity=80.0,
        structure_quality=75.0,
        mtf_alignment=90.0,
        volatility_suitability=70.0,
        momentum_confirmation=72.0,
        level_proximity=80.0,
        explanation="test",
    )


def test_risk_engine_uses_conservative_long_stop_and_min_rr(settings) -> None:
    setup = _raw_setup()
    decision = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING)
    assert decision.plan is not None
    assert decision.plan.stop_loss == 1.0920
    assert decision.plan.risk_reward >= settings.styles[TradingStyle.DAY_TRADING].min_rr


def test_risk_engine_uses_fixed_rr_target_when_no_structure_target(settings) -> None:
    setup = _raw_setup(stop_candidates={"atr": 1.0950}, target_candidates={"atr_extension": 1.1200})
    decision = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING)
    assert decision.plan is not None
    assert decision.plan.target_method == "fixed_rr"
    assert decision.plan.take_profit == pytest.approx(1.1075)
    assert decision.plan.tp1 < decision.plan.tp2 < decision.plan.tp3


def test_risk_engine_conservative_profile_can_use_next_key_zone_target(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.risk.target_profile = "conservative"
    setup = _raw_setup(
        stop_candidates={"atr": 1.0950},
        target_candidates={"next_resistance": 1.1090, "atr_extension": 1.1200},
    )
    decision = RiskEngine(adjusted).plan(setup, TradingStyle.DAY_TRADING)
    assert decision.plan is not None
    assert decision.plan.target_method == "next_resistance"
    assert decision.plan.take_profit == 1.1090


def test_risk_engine_aggressive_profile_can_use_atr_extension_target(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.risk.target_profile = "aggressive"
    setup = _raw_setup(
        stop_candidates={"atr": 1.0950},
        target_candidates={"atr_extension": 1.1090},
    )
    decision = RiskEngine(adjusted).plan(setup, TradingStyle.DAY_TRADING)
    assert decision.plan is not None
    assert decision.plan.target_method == "atr_extension"


def test_risk_engine_rejects_invalid_stop_side(settings) -> None:
    setup = _raw_setup(stop_candidates={"atr": 1.1010}, target_candidates={"atr_extension": 1.1200})
    decision = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING)
    assert decision.plan is None
    assert decision.rejection_reason == "no valid stop-loss candidate"


def test_risk_engine_keeps_nearest_level_block_as_target_diagnostic(settings) -> None:
    setup = _raw_setup(
        stop_candidates={"atr": 1.0950},
        target_candidates={"next_resistance": 1.1060, "atr_extension": 1.1200},
    )
    decision = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING)
    assert decision.plan is not None
    assert decision.plan.take_profit == pytest.approx(1.1075)
    assert decision.plan.tp2 == pytest.approx(1.1060)
    assert decision.plan.tp2_risk_reward < settings.styles[TradingStyle.DAY_TRADING].min_rr
    assert decision.required_min_rr == settings.styles[TradingStyle.DAY_TRADING].min_rr
    assert decision.rejection_reason is None


def test_risk_engine_uses_style_specific_minimum_rr(settings) -> None:
    setup = _raw_setup(
        stop_candidates={"atr": 1.0950},
        target_candidates={"next_resistance": 1.1070, "atr_extension": 1.1200},
    )
    scalping = RiskEngine(settings).plan(setup, TradingStyle.SCALPING)
    day_trading = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING)

    assert scalping.plan is not None
    assert scalping.plan.risk_reward >= settings.styles[TradingStyle.SCALPING].min_rr
    assert day_trading.plan is not None
    assert day_trading.plan.risk_reward >= settings.styles[TradingStyle.DAY_TRADING].min_rr
