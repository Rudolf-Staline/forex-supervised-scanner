"""Diagnostics tests for rejected-but-scored scanner candidates."""

from __future__ import annotations

from app.core.diagnostics import build_gate_breakdown, failed_gate_names, rejection_category
from app.core.pipeline import ScannerService, _candidate_status
from app.core.types import DirectionBias, MarketRegime, OpportunityStatus, RawSetup, RejectionCategory, SetupFamily, TradingStyle
from app.data.providers import SyntheticForexDataProvider
from app.risk.engine import RiskEngine
from app.scoring.engine import ScoringEngine


def _candidate_setup(
    mtf_alignment: float = 90.0,
    volatility_suitability: float = 72.0,
    target_candidates: dict[str, float] | None = None,
) -> RawSetup:
    return RawSetup(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        family=SetupFamily.TREND_CONTINUATION,
        regime=MarketRegime.TRENDING_UP,
        direction=DirectionBias.LONG,
        entry=1.1000,
        stop_candidates={"atr": 1.0950},
        target_candidates=target_candidates or {"atr_extension": 1.1120},
        trend_clarity=82.0,
        structure_quality=76.0,
        mtf_alignment=mtf_alignment,
        volatility_suitability=volatility_suitability,
        momentum_confirmation=74.0,
        level_proximity=68.0,
        explanation="diagnostic candidate",
    )


def test_score_candidate_keeps_partial_quality_without_valid_risk_plan(settings) -> None:
    setup = _candidate_setup()
    score, bucket, components = ScoringEngine(settings).score_candidate(setup, None, spread_price=None)

    assert score > 0.0
    assert bucket.value in {"low", "medium", "high"}
    assert components["risk_reward"] == 0.0
    assert components["spread_friction"] == 0.0
    assert components["trend_clarity"] == setup.trend_clarity


def test_nearest_level_rr_block_becomes_execution_diagnostic(settings) -> None:
    setup = _candidate_setup(target_candidates={"next_resistance": 1.1060, "atr_extension": 1.1200})
    risk_decision = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING)
    assert risk_decision.plan is not None
    assert risk_decision.plan.risk_reward >= settings.styles[TradingStyle.DAY_TRADING].min_rr
    assert risk_decision.plan.tp2_risk_reward < settings.styles[TradingStyle.DAY_TRADING].min_rr

    result = ScoringEngine(settings).score_detailed(setup, risk_decision.plan, spread_price=0.0001)
    gates = build_gate_breakdown(
        setup=setup,
        risk_plan=risk_decision.plan,
        score=result.final_score,
        minimum_score=settings.setups.minimum_scores[SetupFamily.TREND_CONTINUATION],
        minimum_rr=settings.styles[TradingStyle.DAY_TRADING].min_rr,
    )

    assert gates.minimum_rr
    assert "minimum RR" not in failed_gate_names(gates)
    assert result.components["target_clearance"] < 100.0


def test_gate_breakdown_catches_conflicting_timeframes(settings) -> None:
    setup = _candidate_setup(mtf_alignment=35.0, volatility_suitability=40.0)
    risk_decision = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING)
    assert risk_decision.plan is not None
    score, _bucket, _components = ScoringEngine(settings).score_candidate(setup, risk_decision.plan, spread_price=0.0001)
    gates = build_gate_breakdown(
        setup=setup,
        risk_plan=risk_decision.plan,
        score=score,
        minimum_score=settings.setups.minimum_scores[SetupFamily.TREND_CONTINUATION],
        minimum_rr=settings.styles[TradingStyle.DAY_TRADING].min_rr,
    )

    assert not gates.multi_timeframe_alignment
    assert not gates.volatility
    assert rejection_category(gates, "score below threshold") == RejectionCategory.CONFLICTING_TIMEFRAMES


def test_scanner_returns_watchlist_but_scored_candidate_payload(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.provider.name = "synthetic"
    adjusted.provider.max_bars = 300
    adjusted.styles[TradingStyle.DAY_TRADING].lookback_bars = 220
    provider = SyntheticForexDataProvider(adjusted.provider)

    report = ScannerService(adjusted, provider).scan(TradingStyle.DAY_TRADING, ["GBP/USD"])
    rejected = [
        item
        for item in report.opportunities
        if item.status in {OpportunityStatus.WATCHLIST, OpportunityStatus.DETECTED}
        and item.raw_setup_family is not None
    ]

    assert rejected
    candidate = rejected[0]
    payload = candidate.model_dump(mode="json")
    assert candidate.pre_gate_score is not None and candidate.pre_gate_score > 0.0
    assert candidate.score == candidate.pre_gate_score
    assert candidate.gate_breakdown is not None
    assert candidate.status in {OpportunityStatus.WATCHLIST, OpportunityStatus.DETECTED}
    assert candidate.missing_conditions
    assert payload["raw_setup_family"] == candidate.raw_setup_family.value
    assert payload["pre_gate_score"] == candidate.pre_gate_score
    assert "technical_score" in payload


def test_status_transitions_include_premium(settings) -> None:
    setup = _candidate_setup()
    risk = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING).plan
    assert risk is not None

    assert _candidate_status(False, None, 30.0, 50.0, 20.0, 60.0, 55.0, 52.0) == OpportunityStatus.DETECTED
    assert _candidate_status(True, risk, 60.0, 65.0, 62.0, 64.0, 55.0, 52.0) == OpportunityStatus.WATCHLIST
    assert _candidate_status(False, risk, 62.0, 68.0, 43.0, 64.0, 55.0, 52.0) == OpportunityStatus.WATCHLIST
    assert _candidate_status(False, risk, 62.0, 68.0, 62.0, 45.0, 55.0, 52.0) == OpportunityStatus.WATCHLIST
    assert _candidate_status(False, risk, 62.0, 65.0, 62.0, 64.0, 55.0, 52.0) == OpportunityStatus.APPROVED
    assert _candidate_status(False, risk, 82.0, 75.0, 72.0, 74.0, 65.0, 52.0) == OpportunityStatus.PREMIUM


def test_data_quality_activation_and_invalidation_gates_demote_to_watchlist(settings) -> None:
    setup = _candidate_setup()
    risk = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING).plan
    assert risk is not None

    assert (
        _candidate_status(
            False,
            risk,
            68.0,
            70.0,
            66.0,
            64.0,
            55.0,
            52.0,
            settings.approval,
            data_quality_score=settings.approval.minimum_data_quality_score - 1.0,
            activation_quality=80.0,
            invalidation_quality=80.0,
        )
        == OpportunityStatus.WATCHLIST
    )
    assert (
        _candidate_status(
            False,
            risk,
            68.0,
            70.0,
            66.0,
            64.0,
            55.0,
            52.0,
            settings.approval,
            data_quality_score=80.0,
            activation_quality=settings.approval.minimum_activation_quality - 1.0,
            invalidation_quality=80.0,
        )
        == OpportunityStatus.WATCHLIST
    )
    assert (
        _candidate_status(
            False,
            risk,
            68.0,
            70.0,
            66.0,
            64.0,
            55.0,
            52.0,
            settings.approval,
            data_quality_score=80.0,
            activation_quality=80.0,
            invalidation_quality=settings.approval.minimum_invalidation_quality - 1.0,
        )
        == OpportunityStatus.WATCHLIST
    )


def test_premium_requires_clean_data_activation_and_invalidation(settings) -> None:
    setup = _candidate_setup()
    risk = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING).plan
    assert risk is not None

    strong_kwargs = {
        "watchlist_candidate": False,
        "risk_plan": risk,
        "final_score": 82.0,
        "technical_score": 75.0,
        "execution_score": 72.0,
        "context_score": 74.0,
        "empirical_score": 65.0,
        "minimum_score": 52.0,
        "approval": settings.approval,
    }
    assert (
        _candidate_status(
            **strong_kwargs,
            data_quality_score=settings.approval.premium_data_quality_score - 1.0,
            activation_quality=80.0,
            invalidation_quality=80.0,
        )
        == OpportunityStatus.APPROVED
    )
    assert (
        _candidate_status(
            **strong_kwargs,
            data_quality_score=80.0,
            activation_quality=80.0,
            invalidation_quality=80.0,
        )
        == OpportunityStatus.PREMIUM
    )


def test_rejection_category_identifies_approval_layer_gaps(settings) -> None:
    setup = _candidate_setup()
    risk = RiskEngine(settings).plan(setup, TradingStyle.DAY_TRADING).plan
    assert risk is not None
    gates = build_gate_breakdown(setup, risk, 70.0, 52.0, settings.styles[TradingStyle.DAY_TRADING].min_rr)

    assert rejection_category(gates, "data quality 52.0 below approval gate 58.0") == RejectionCategory.POOR_DATA_QUALITY
    assert rejection_category(gates, "activation quality 42.0 below approval gate 45.0") == RejectionCategory.WEAK_ACTIVATION
    assert rejection_category(gates, "invalidation quality 45.0 below approval gate 48.0") == RejectionCategory.WEAK_INVALIDATION
