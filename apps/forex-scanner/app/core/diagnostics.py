"""Diagnostic gates for raw setup evaluation and final trade rejection."""

from __future__ import annotations

from app.core.types import GateBreakdown, RawSetup, RejectionCategory, RiskPlan

# The setup detector already enforces the hard family-specific entry rules.
# These midline gates are diagnostic quality checks on the existing 0-100
# component scale; approval remains controlled by the configured risk and score
# gates, with these flags explaining which evidence was weakest.
TREND_GATE_MIN = 50.0
STRUCTURE_GATE_MIN = 50.0
MOMENTUM_GATE_MIN = 50.0
VOLATILITY_GATE_MIN = 45.0
MTF_ALIGNMENT_GATE_MIN = 50.0

GATE_LABELS: dict[str, str] = {
    "trend": "trend",
    "structure": "structure",
    "momentum": "momentum",
    "volatility": "volatility",
    "multi_timeframe_alignment": "multi-timeframe alignment",
    "minimum_rr": "minimum RR",
    "score_threshold": "score threshold",
}


def build_gate_breakdown(
    setup: RawSetup,
    risk_plan: RiskPlan | None,
    score: float,
    minimum_score: float,
    minimum_rr: float,
) -> GateBreakdown:
    """Build pass/fail diagnostics for a raw setup candidate."""

    return GateBreakdown(
        trend=setup.trend_clarity >= TREND_GATE_MIN,
        structure=setup.structure_quality >= STRUCTURE_GATE_MIN,
        momentum=setup.momentum_confirmation >= MOMENTUM_GATE_MIN,
        volatility=setup.volatility_suitability >= VOLATILITY_GATE_MIN,
        multi_timeframe_alignment=setup.mtf_alignment >= MTF_ALIGNMENT_GATE_MIN,
        minimum_rr=risk_plan is not None and risk_plan.risk_reward >= minimum_rr - 1e-9,
        score_threshold=score >= minimum_score - 1e-9,
    )


def failed_gate_names(gates: GateBreakdown) -> list[str]:
    """Return human-readable gate names that failed."""

    payload = gates.model_dump()
    return [GATE_LABELS[key] for key, passed in payload.items() if not passed]


def rejection_category(gates: GateBreakdown, rejection_reason: str | None) -> RejectionCategory:
    """Classify the main rejection driver from gate state and risk text."""

    reason = (rejection_reason or "").lower()
    if "data quality" in reason:
        return RejectionCategory.POOR_DATA_QUALITY
    if "activation quality" in reason:
        return RejectionCategory.WEAK_ACTIVATION
    if "invalidation quality" in reason:
        return RejectionCategory.WEAK_INVALIDATION
    if "execution score" in reason:
        return RejectionCategory.WEAK_EXECUTION
    if "context score" in reason:
        return RejectionCategory.WEAK_CONTEXT
    if "empirical score" in reason:
        return RejectionCategory.LOW_EMPIRICAL_SUPPORT
    if "stop" in reason or "trade direction" in reason:
        return RejectionCategory.INVALID_RISK
    if not gates.minimum_rr and ("risk/reward" in reason or "minimum rr" in reason or "take-profit" in reason):
        return RejectionCategory.INSUFFICIENT_RR
    if not gates.multi_timeframe_alignment:
        return RejectionCategory.CONFLICTING_TIMEFRAMES
    if not gates.structure or not gates.trend:
        return RejectionCategory.WEAK_STRUCTURE
    if not gates.momentum:
        return RejectionCategory.WEAK_MOMENTUM
    if not gates.volatility:
        return RejectionCategory.UNSUITABLE_VOLATILITY
    if not gates.score_threshold:
        return RejectionCategory.SCORE_TOO_LOW
    return RejectionCategory.INVALID_RISK


def rejection_summary(
    gates: GateBreakdown,
    rejection_reason: str | None,
    score: float,
    minimum_score: float,
    minimum_rr: float,
    risk_plan: RiskPlan | None,
) -> str:
    """Create a compact user-facing rejection summary."""

    failed = failed_gate_names(gates)
    parts: list[str] = []
    if rejection_reason:
        parts.append(rejection_reason)
    if failed:
        parts.append("failed gates: " + ", ".join(failed))
    if risk_plan is not None and not gates.minimum_rr:
        parts.append(f"RR {risk_plan.risk_reward:.2f} below required {minimum_rr:.2f}")
    if not gates.score_threshold:
        parts.append(f"score {score:.1f} below minimum {minimum_score:.1f}")
    return "; ".join(parts) if parts else "candidate did not pass final approval"
