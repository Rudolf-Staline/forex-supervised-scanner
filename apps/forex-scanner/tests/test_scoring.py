"""Scoring engine tests."""

import pytest

from datetime import datetime, timezone

from app.config.settings import LayerWeights, ScoreWeights
from app.core.types import DataQualityDiagnostic, DirectionBias, MarketRegime, RawSetup, RiskPlan, SessionName, SetupFamily, TradingStyle
from app.scoring.engine import ScoringEngine, market_session


def _scored_setup() -> RawSetup:
    return RawSetup(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        family=SetupFamily.TREND_CONTINUATION,
        regime=MarketRegime.TRENDING_UP,
        direction=DirectionBias.LONG,
        entry=1.1000,
        stop_candidates={"atr": 1.0950},
        target_candidates={"atr_extension": 1.1120},
        trend_clarity=85.0,
        structure_quality=80.0,
        mtf_alignment=90.0,
        volatility_suitability=78.0,
        momentum_confirmation=76.0,
        level_proximity=70.0,
        explanation="test",
    )


def _risk_plan() -> RiskPlan:
    return RiskPlan(
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1120,
        tp1=1.1050,
        tp2=1.1120,
        tp3=1.1160,
        risk_reward=2.4,
        tp1_risk_reward=1.0,
        tp2_risk_reward=2.4,
        tp3_risk_reward=3.2,
        stop_method="atr",
        target_method="atr",
    )


def test_scoring_returns_bucket_and_components(settings) -> None:
    setup = _scored_setup()
    risk = _risk_plan()
    score, bucket, components = ScoringEngine(settings).score(setup, risk, spread_price=0.0001)
    assert score > 60.0
    assert bucket.value in {"medium", "high"}
    assert "risk_reward" in components
    assert "target_clearance" in components


def test_scoring_uses_configurable_weights(settings) -> None:
    adjusted = settings.model_copy(
        update={
            "weights": ScoreWeights(
                trend_clarity=1.0,
                structure_quality=0.0,
                multi_timeframe_alignment=0.0,
                volatility_suitability=0.0,
                momentum_confirmation=0.0,
                spread_friction=0.0,
                risk_reward=0.0,
                level_proximity=0.0,
            ),
            "layer_weights": LayerWeights(technical=1.0, execution=0.0, context=0.0, empirical=0.0),
        },
        deep=True,
    )
    setup = _scored_setup()
    risk = _risk_plan()
    score, _bucket, components = ScoringEngine(adjusted).score(setup, risk, spread_price=0.0001)

    assert score == pytest.approx(components["trend_clarity"], abs=0.01)


def test_scoring_penalizes_high_friction_spread(settings) -> None:
    setup = _scored_setup()
    risk = _risk_plan()
    clean_score, _bucket, clean_components = ScoringEngine(settings).score(setup, risk, spread_price=0.00005)
    expensive_score, _bucket, expensive_components = ScoringEngine(settings).score(setup, risk, spread_price=0.0015)

    assert expensive_components["spread_friction"] < clean_components["spread_friction"]
    assert expensive_score < clean_score


def test_scoring_exposes_configured_minimum_score_gate(settings) -> None:
    engine = ScoringEngine(settings)
    assert engine.minimum_score(SetupFamily.TREND_CONTINUATION) == settings.setups.minimum_scores[SetupFamily.TREND_CONTINUATION]
    assert engine.minimum_score(SetupFamily.BREAKOUT_CONFIRMATION) == settings.setups.minimum_scores[SetupFamily.BREAKOUT_CONFIRMATION]


def test_scoring_returns_technical_execution_and_grade(settings) -> None:
    result = ScoringEngine(settings).score_detailed(_scored_setup(), _risk_plan(), spread_price=0.0001)
    assert result.technical_score > 70.0
    assert result.execution_score > 0.0
    assert result.context_score > 0.0
    assert result.empirical_score == settings.empirical.neutral_score
    assert result.final_score > 0.0
    assert result.grade.value in {"A", "B", "C", "D"}


def test_multilayer_scoring_keeps_technical_read_without_risk_plan(settings) -> None:
    result = ScoringEngine(settings).score_detailed(_scored_setup(), None, spread_price=None)

    assert result.technical_score > 70.0
    assert result.execution_score < result.technical_score
    assert result.final_score > 0.0
    assert result.components["risk_reward"] == 0.0


def test_context_score_penalizes_poor_data_quality(settings) -> None:
    setup = _scored_setup()
    risk = _risk_plan()
    clean = ScoringEngine(settings).score_detailed(
        setup,
        risk,
        spread_price=0.0001,
        data_quality=DataQualityDiagnostic(score=96.0, missing_bars=0, spread_available=True, resampled=False),
        timestamp=datetime(2025, 1, 1, 14, tzinfo=timezone.utc),
    )
    dirty = ScoringEngine(settings).score_detailed(
        setup,
        risk,
        spread_price=0.0001,
        data_quality=DataQualityDiagnostic(score=45.0, missing_bars=12, spread_available=False, resampled=True, warnings=["stale"]),
        timestamp=datetime(2025, 1, 1, 22, tzinfo=timezone.utc),
    )

    assert clean.session == SessionName.NEW_YORK_OVERLAP
    assert dirty.components["data_quality_execution"] < clean.components["data_quality_execution"]
    assert dirty.execution_score < clean.execution_score
    assert dirty.context_score < clean.context_score
    assert dirty.final_score < clean.final_score


def test_recalibrated_layer_weights_prioritize_execution_for_final_quality(settings) -> None:
    weights = settings.layer_weights.as_dict()
    setup = _scored_setup()
    risk = _risk_plan()
    strong_execution = ScoringEngine(settings).score_detailed(setup, risk, spread_price=0.00005)
    weak_execution = ScoringEngine(settings).score_detailed(setup, risk, spread_price=0.0015)

    assert weights["execution"] >= weights["technical"] - 0.05
    assert weights["context"] + weights["empirical"] >= 0.40
    assert strong_execution.execution_score > weak_execution.execution_score
    assert strong_execution.final_score > weak_execution.final_score


def test_market_session_mapping() -> None:
    assert market_session(datetime(2025, 1, 1, 8, tzinfo=timezone.utc)) == SessionName.LONDON
    assert market_session(datetime(2025, 1, 1, 22, tzinfo=timezone.utc)) == SessionName.OFF_HOURS


def test_activation_and_invalidation_quality_affect_execution_score(settings) -> None:
    strong_setup = _scored_setup().model_copy(update={"activation_quality": 88.0, "invalidation_quality": 82.0, "atr": 0.003})
    weak_setup = _scored_setup().model_copy(update={"activation_quality": 25.0, "invalidation_quality": 28.0, "atr": 0.0005})
    risk = _risk_plan()

    strong = ScoringEngine(settings).score_detailed(strong_setup, risk, spread_price=0.0001)
    weak = ScoringEngine(settings).score_detailed(weak_setup, risk, spread_price=0.0001)

    assert weak.components["activation_quality"] < strong.components["activation_quality"]
    assert weak.components["invalidation_quality"] < strong.components["invalidation_quality"]
    assert weak.execution_score < strong.execution_score
