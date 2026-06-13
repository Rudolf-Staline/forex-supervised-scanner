"""Decision trace, score decomposition, and threshold policy reporting.

All reporting in this module is strictly paper/demo only. It never authorizes
live trading, never calls ``order_send``, never mutates ``.env`` and never
embeds credentials or MT5 secrets in any rendered report.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.adaptive_thresholds.provider import AdaptiveThresholdProvider
from app.config.instruments import instrument_for_symbol
from app.config.settings import AppSettings
from app.core.types import Opportunity, OpportunityStatus, TradingStyle
from app.execution.demo_bot_config import EXECUTABLE_DEMO_STATUSES, DemoBotConfig

SECRET_KEY_PARTS = ("secret", "password", "token", "login", "account", "server", "key", ".env")
DEFAULT_DECISION_TRACE_JSON = "decision_trace.json"
DEFAULT_DECISION_TRACE_TXT = "decision_trace.txt"
DEFAULT_MIN_SCORE_POLICY_JSON = "min_score_policy_report.json"
DEFAULT_MIN_SCORE_POLICY_TXT = "min_score_policy_report.txt"
DEFAULT_SCORE_DECOMPOSITION_JSON = "score_decomposition.json"
DEFAULT_SCORE_DECOMPOSITION_TXT = "score_decomposition.txt"

PAPER_SAFETY_FLAGS: dict[str, Any] = {
    "mode": "paper/demo only",
    "live_trading": False,
    "broker_live_execution": False,
    "order_send_called": False,
    "env_mutated": False,
}


class GateResult(BaseModel):
    """Reusable structured gate result for score, bot, market, and risk gates."""

    name: str
    layer: str
    value: float | str | bool | None = None
    minimum: float | str | bool | None = None
    maximum: float | None = None
    margin: float | None = None
    passed: bool
    severity: str = "info"
    reason: str


class GateMargin(BaseModel):
    """Human readable margin for an approval, bot, market, or risk gate.

    Retained for backward compatibility with the original decision-trace
    surface; new diagnostics should prefer :class:`GateResult`.
    """

    name: str
    value: float | str | bool | None = None
    minimum: float | str | bool | None = None
    maximum: float | None = None
    margin: float | None = None
    passed: bool
    severity: str = "info"
    explanation: str


class MinScorePolicyReport(BaseModel):
    """Explain scanner-vs-bot threshold policy for one symbol/style."""

    symbol: str
    style: str
    instrument_min_score: float
    adaptive_enabled: bool = False
    adaptive_mode: str = "report_only"
    adaptive_base_min_score: float | None = None
    adaptive_recommended_min_score: float | None = None
    adaptive_effective_min_score: float | None = None
    demo_bot_min_score: float
    effective_scanner_threshold: float
    effective_bot_threshold: float
    threshold_source: str
    mismatch_warnings: list[str] = Field(default_factory=list)


class ScoreDecompositionReport(BaseModel):
    """Machine-readable explanation of how a final score was assembled."""

    symbol: str
    style: str
    provider: str | None = None
    setup_family: str
    setup_subtype: str
    direction: str
    status: str
    final_score: float | None = None
    technical_score: float | None = None
    execution_score: float | None = None
    context_score: float | None = None
    empirical_score: float | None = None
    pattern_score: float = 0.0
    score_components: dict[str, float] = Field(default_factory=dict)
    technical_weights: dict[str, float] = Field(default_factory=dict)
    layer_weights: dict[str, float] = Field(default_factory=dict)
    active_min_score: float
    min_score_source: str
    approval_gate_results: list[GateResult] = Field(default_factory=list)
    bot_gate_results: list[GateResult] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    min_score_policy: MinScorePolicyReport
    safety_flags: dict[str, Any] = Field(default_factory=lambda: dict(PAPER_SAFETY_FLAGS))


class DecisionTrace(BaseModel):
    """Structured, sanitized explanation of one scanner/bot decision."""

    trace_id: str
    cycle_id: str | None = None
    generated_at: datetime
    timestamp: datetime
    symbol: str
    style: str
    provider: str | None = None
    broker_mode: str | None = None
    setup_family: str
    setup_subtype: str
    direction: str
    status: str
    accepted: bool = False
    order_ids: list[str] = Field(default_factory=list)
    # Score decomposition
    final_score: float | None = None
    technical_score: float | None = None
    execution_score: float | None = None
    context_score: float | None = None
    empirical_score: float | None = None
    pattern_score: float = 0.0
    score_components: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, Any] = Field(default_factory=dict)
    # Risk plan
    risk_reward: float | None = None
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    # Market microstructure / context
    spread: float | None = None
    atr: float | None = None
    spread_atr_ratio: float | None = None
    data_quality_score: float | None = None
    session: str | None = None
    market_regime: str | None = None
    htf_regime: str | None = None
    entry_regime: str | None = None
    trigger_regime: str | None = None
    # Thresholds and gates
    active_min_score: float
    threshold_source: str
    min_score_policy: MinScorePolicyReport
    gate_results: list[GateResult] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    primary_rejection_reason: str | None = None
    safety_flags: dict[str, Any] = Field(default_factory=lambda: dict(PAPER_SAFETY_FLAGS))
    # Backward-compatible nested surface (retained for existing consumers)
    raw_setup: dict[str, Any] = Field(default_factory=dict)
    risk_plan: dict[str, Any] = Field(default_factory=dict)
    approval_gates: list[GateMargin] = Field(default_factory=list)
    bot_gates: list[GateMargin] = Field(default_factory=list)
    gate_margin_report: list[GateMargin] = Field(default_factory=list)
    paper_order_preflight_result: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=lambda: {"mode": "paper/demo only", "live_trading": False})


def build_decision_trace(
    opportunity: Opportunity,
    settings: AppSettings,
    *,
    bot_decision: Any | None = None,
    paper_order_ids: list[str] | None = None,
    cycle_id: str | None = None,
    provider: str | None = None,
    broker_mode: str | None = None,
) -> DecisionTrace:
    """Build a sanitized decision trace without credentials or raw environment values."""

    instrument = instrument_for_symbol(opportunity.symbol)
    policy = build_min_score_policy(opportunity, settings)
    active_min = policy.effective_scanner_threshold
    reasons = _reasons(opportunity, bot_decision)
    now = datetime.now(timezone.utc)
    order_ids = list(paper_order_ids or getattr(bot_decision, "order_ids", []) or [])
    accepted = (
        bool(getattr(bot_decision, "accepted", False))
        if bot_decision is not None
        else opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}
    )
    spread_atr = _spread_atr_ratio(opportunity)
    raw_setup = {
        "family": opportunity.setup_family.value,
        "subtype": opportunity.setup_subtype.value,
        "direction": opportunity.direction.value,
        "regime": opportunity.regime.value,
        "session": opportunity.session.value if opportunity.session else None,
        "timeframes": {
            "higher": opportunity.timeframe_higher.value,
            "entry": opportunity.timeframe_entry.value,
            "trigger": opportunity.timeframe_trigger.value,
        },
        "activation_quality": opportunity.activation_quality,
        "invalidation_quality": opportunity.invalidation_quality,
        "detected_patterns": opportunity.detected_patterns,
        "pattern_explanations": opportunity.pattern_explanations,
    }
    risk_plan = {
        "entry": opportunity.entry,
        "stop_loss": opportunity.stop_loss,
        "take_profit": opportunity.take_profit,
        "tp1": opportunity.tp1,
        "tp2": opportunity.tp2,
        "tp3": opportunity.tp3,
        "risk_reward": opportunity.risk_reward,
        "required_min_rr": opportunity.required_min_rr or instrument.min_risk_reward,
        "invalidation": opportunity.invalidation,
    }
    approval_gate_results = _approval_gate_results(opportunity, settings, active_min)
    bot_gate_results = _bot_gate_results(opportunity, settings, bot_decision)
    return DecisionTrace(
        trace_id=str(uuid.uuid4()),
        cycle_id=cycle_id,
        generated_at=now,
        timestamp=now,
        symbol=opportunity.symbol,
        style=opportunity.style.value,
        provider=provider if provider is not None else opportunity.provider,
        broker_mode=broker_mode if broker_mode is not None else (os.getenv("BROKER_MODE", "paper").strip().lower() or "paper"),
        setup_family=opportunity.setup_family.value,
        setup_subtype=opportunity.setup_subtype.value,
        direction=opportunity.direction.value,
        status=opportunity.status.value,
        accepted=accepted,
        order_ids=order_ids,
        final_score=opportunity.final_score,
        technical_score=opportunity.technical_score,
        execution_score=opportunity.execution_score,
        context_score=opportunity.context_score,
        empirical_score=opportunity.empirical_score,
        pattern_score=opportunity.pattern_score,
        score_components={key: round(float(value), 4) for key, value in opportunity.score_components.items()},
        weights={"components": settings.weights.as_dict(), "layers": settings.layer_weights.as_dict()},
        risk_reward=opportunity.risk_reward,
        entry=opportunity.entry,
        stop_loss=opportunity.stop_loss,
        take_profit=opportunity.take_profit,
        tp1=opportunity.tp1,
        tp2=opportunity.tp2,
        tp3=opportunity.tp3,
        spread=opportunity.spread,
        atr=opportunity.atr,
        spread_atr_ratio=spread_atr,
        data_quality_score=_data_quality_score(opportunity),
        session=opportunity.session.value if opportunity.session else None,
        market_regime=opportunity.regime.value if opportunity.regime else None,
        htf_regime=opportunity.htf_regime.value if opportunity.htf_regime else None,
        entry_regime=opportunity.entry_regime.value if opportunity.entry_regime else None,
        trigger_regime=opportunity.trigger_regime.value if opportunity.trigger_regime else None,
        active_min_score=active_min,
        threshold_source=policy.threshold_source,
        min_score_policy=policy,
        gate_results=[*approval_gate_results, *bot_gate_results],
        rejection_reasons=reasons,
        primary_rejection_reason=reasons[0] if reasons else None,
        # Backward-compatible nested surface
        raw_setup=_sanitize(raw_setup),
        risk_plan=_sanitize(risk_plan),
        approval_gates=_approval_gates(opportunity, settings, active_min),
        bot_gates=_bot_gates(opportunity, settings, bot_decision),
        gate_margin_report=_gate_margin_report(opportunity, settings, active_min, bot_decision),
        paper_order_preflight_result=_paper_preflight(bot_decision, order_ids),
    )


def build_min_score_policy(opportunity: Opportunity, settings: AppSettings) -> MinScorePolicyReport:
    """Build scanner/bot threshold diagnostics from a scored opportunity."""

    instrument = instrument_for_symbol(opportunity.symbol)
    demo_min = DemoBotConfig.from_settings(settings).min_score
    adaptive_enabled = bool(getattr(opportunity, "adaptive_threshold_enabled", False))
    adaptive_settings = getattr(settings, "adaptive_thresholds", None)
    config_enabled = bool(getattr(adaptive_settings, "enabled", False))
    mode = getattr(adaptive_settings, "mode", "report_only")
    base = opportunity.base_min_score if opportunity.base_min_score is not None else instrument.min_score
    effective = opportunity.effective_min_score if opportunity.effective_min_score is not None else base
    scanner_threshold = effective if adaptive_enabled and mode == "scanner_effective" else base
    bot_threshold = effective if adaptive_enabled and mode == "scanner_effective" else demo_min
    source = "adaptive" if adaptive_enabled and mode == "scanner_effective" else "instrument/static"
    return MinScorePolicyReport(
        symbol=opportunity.symbol,
        style=opportunity.style.value,
        instrument_min_score=instrument.min_score,
        adaptive_enabled=config_enabled,
        adaptive_mode=mode,
        adaptive_base_min_score=opportunity.base_min_score,
        adaptive_recommended_min_score=opportunity.adaptive_min_score,
        adaptive_effective_min_score=opportunity.effective_min_score,
        demo_bot_min_score=demo_min,
        effective_scanner_threshold=scanner_threshold,
        effective_bot_threshold=bot_threshold,
        threshold_source=source,
        mismatch_warnings=_threshold_warnings(scanner_threshold, demo_min, instrument.min_score, base, bot_threshold),
    )


def build_min_score_policy_report(symbol: str, style: TradingStyle | str, settings: AppSettings) -> MinScorePolicyReport:
    """Build a min-score policy report directly from settings without a scan.

    Uses the adaptive threshold provider so the report is accurate whether
    adaptive thresholds are disabled, in report-only mode, or scanner-effective.
    """

    style_enum = TradingStyle(style) if not isinstance(style, TradingStyle) else style
    instrument = instrument_for_symbol(symbol)
    demo_min = DemoBotConfig.from_settings(settings).min_score
    adaptive_settings = getattr(settings, "adaptive_thresholds", None)
    enabled = bool(getattr(adaptive_settings, "enabled", False))
    mode = getattr(adaptive_settings, "mode", "report_only")

    provider = AdaptiveThresholdProvider(settings)
    result = provider.get_threshold(symbol, style_enum)
    base = result.base_min_score
    recommended = result.recommended_min_score
    effective = result.effective_min_score
    can_apply = enabled and mode == "scanner_effective" and not result.is_fallback
    scanner_threshold = effective if can_apply else base
    bot_threshold = effective if can_apply else demo_min
    source = "adaptive" if can_apply else "instrument/static"
    return MinScorePolicyReport(
        symbol=symbol,
        style=style_enum.value,
        instrument_min_score=instrument.min_score,
        adaptive_enabled=enabled,
        adaptive_mode=mode,
        adaptive_base_min_score=base,
        adaptive_recommended_min_score=recommended,
        adaptive_effective_min_score=effective,
        demo_bot_min_score=demo_min,
        effective_scanner_threshold=scanner_threshold,
        effective_bot_threshold=bot_threshold,
        threshold_source=source,
        mismatch_warnings=_threshold_warnings(scanner_threshold, demo_min, instrument.min_score, base, bot_threshold),
    )


def build_score_decomposition(
    opportunity: Opportunity,
    settings: AppSettings,
    *,
    provider: str | None = None,
) -> ScoreDecompositionReport:
    """Decompose how a final score was assembled for one scanned opportunity."""

    policy = build_min_score_policy(opportunity, settings)
    active_min = policy.effective_scanner_threshold
    return ScoreDecompositionReport(
        symbol=opportunity.symbol,
        style=opportunity.style.value,
        provider=provider if provider is not None else opportunity.provider,
        setup_family=opportunity.setup_family.value,
        setup_subtype=opportunity.setup_subtype.value,
        direction=opportunity.direction.value,
        status=opportunity.status.value,
        final_score=opportunity.final_score,
        technical_score=opportunity.technical_score,
        execution_score=opportunity.execution_score,
        context_score=opportunity.context_score,
        empirical_score=opportunity.empirical_score,
        pattern_score=opportunity.pattern_score,
        score_components={key: round(float(value), 4) for key, value in opportunity.score_components.items()},
        technical_weights=settings.weights.as_dict(),
        layer_weights=settings.layer_weights.as_dict(),
        active_min_score=active_min,
        min_score_source=policy.threshold_source,
        approval_gate_results=_approval_gate_results(opportunity, settings, active_min),
        bot_gate_results=_bot_gate_results(opportunity, settings, None),
        rejection_reasons=_reasons(opportunity, None),
        min_score_policy=policy,
    )


# --------------------------------------------------------------------------- #
# Exporters
# --------------------------------------------------------------------------- #
def export_decision_traces(traces: list[DecisionTrace], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / DEFAULT_DECISION_TRACE_JSON
    txt_path = reports_dir / DEFAULT_DECISION_TRACE_TXT
    json_path.write_text(
        json.dumps([_sanitize(t.model_dump(mode="json")) for t in traces], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    txt_path.write_text(render_decision_traces_text(traces), encoding="utf-8")
    return json_path, txt_path


def export_min_score_policy_report(traces: list[DecisionTrace], reports_dir: Path) -> tuple[Path, Path]:
    policies = [trace.min_score_policy for trace in traces]
    return export_min_score_policies(policies, reports_dir)


def export_min_score_policies(policies: list[MinScorePolicyReport], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / DEFAULT_MIN_SCORE_POLICY_JSON
    txt_path = reports_dir / DEFAULT_MIN_SCORE_POLICY_TXT
    json_path.write_text(json.dumps([p.model_dump(mode="json") for p in policies], indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(render_min_score_policy_text(policies), encoding="utf-8")
    return json_path, txt_path


def export_score_decomposition(reports: list[ScoreDecompositionReport], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / DEFAULT_SCORE_DECOMPOSITION_JSON
    txt_path = reports_dir / DEFAULT_SCORE_DECOMPOSITION_TXT
    json_path.write_text(
        json.dumps([_sanitize(r.model_dump(mode="json")) for r in reports], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    txt_path.write_text(render_score_decomposition_text(reports), encoding="utf-8")
    return json_path, txt_path


# --------------------------------------------------------------------------- #
# Text renderers
# --------------------------------------------------------------------------- #
def render_decision_traces_text(traces: list[DecisionTrace]) -> str:
    lines = ["Decision Trace Report", "mode=paper/demo only live_trading=false", ""]
    if not traces:
        lines.append("No decisions available.")
        return "\n".join(lines) + "\n"
    for trace in traces:
        lines.extend([
            f"trace_id={trace.trace_id} cycle_id={trace.cycle_id or '-'}",
            f"symbol={trace.symbol} style={trace.style} provider={trace.provider} broker_mode={trace.broker_mode}",
            f"setup={trace.setup_family}/{trace.setup_subtype} direction={trace.direction} status={trace.status} accepted={str(trace.accepted).lower()} order_ids={','.join(trace.order_ids) or '-'}",
            f"final_score={_fmt(trace.final_score)} active_min_score={trace.active_min_score:.1f} threshold_source={trace.threshold_source}",
            f"technical={_fmt(trace.technical_score)} execution={_fmt(trace.execution_score)} context={_fmt(trace.context_score)} empirical={_fmt(trace.empirical_score)} pattern={trace.pattern_score:.2f}",
            f"risk_reward={_fmt(trace.risk_reward)} spread_atr_ratio={_fmt(trace.spread_atr_ratio)} data_quality={_fmt(trace.data_quality_score)} preflight={trace.paper_order_preflight_result.get('status')}",
        ])
        strongest = _strongest_positive_factors(trace)
        if strongest:
            lines.append("strongest_positive_factors: " + ", ".join(f"{name}={value}" for name, value in strongest))
        weakest = _weakest_gates(trace.gate_results)
        if weakest:
            lines.append("weakest_gates: " + "; ".join(f"{gate.name}(margin={gate.margin})" for gate in weakest))
        lines.append("reasons=" + ("; ".join(trace.rejection_reasons) if trace.rejection_reasons else "none"))
        lines.append(f"primary_rejection_reason={trace.primary_rejection_reason or 'none'}")
        lines.append("gate_results:")
        for gate in trace.gate_results:
            state = "pass" if gate.passed else "fail"
            lines.append(
                f"  - [{gate.layer}] {gate.name}: {state} value={gate.value} min={gate.minimum} max={gate.maximum} "
                f"margin={gate.margin} severity={gate.severity} :: {gate.reason}"
            )
        lines.append("next_diagnostic_action: " + _next_diagnostic_action(trace))
        lines.append("")
    return "\n".join(lines)


def render_min_score_policy_text(policies: list[MinScorePolicyReport]) -> str:
    lines = [
        "Min Score Policy Report",
        "mode=paper/demo only; thresholds are diagnostic and not auto-mutated",
        "",
    ]
    if not policies:
        lines.append("No threshold policies available.")
        return "\n".join(lines) + "\n"
    for policy in policies:
        lines.extend([
            f"symbol={policy.symbol} style={policy.style}",
            f"adaptive_enabled={str(policy.adaptive_enabled).lower()} adaptive_mode={policy.adaptive_mode}",
            f"instrument_min_score={policy.instrument_min_score:.1f} adaptive_base={_fmt(policy.adaptive_base_min_score)} "
            f"adaptive_recommended={_fmt(policy.adaptive_recommended_min_score)} adaptive_effective={_fmt(policy.adaptive_effective_min_score)}",
            f"effective_scanner_threshold={policy.effective_scanner_threshold:.1f} demo_bot_min_score={policy.demo_bot_min_score:.1f} "
            f"effective_bot_threshold={policy.effective_bot_threshold:.1f} source={policy.threshold_source}",
            "warnings=" + ("; ".join(policy.mismatch_warnings) if policy.mismatch_warnings else "none"),
            "",
        ])
    return "\n".join(lines)


def render_score_decomposition_text(reports: list[ScoreDecompositionReport]) -> str:
    lines = ["Score Decomposition Report", "mode=paper/demo only live_trading=false", ""]
    if not reports:
        lines.append("No score decompositions available.")
        return "\n".join(lines) + "\n"
    for report in reports:
        lines.extend([
            f"symbol={report.symbol} style={report.style} provider={report.provider}",
            f"setup={report.setup_family}/{report.setup_subtype} direction={report.direction} status={report.status}",
            f"final_score={_fmt(report.final_score)} active_min_score={report.active_min_score:.1f} min_score_source={report.min_score_source}",
            f"technical={_fmt(report.technical_score)} execution={_fmt(report.execution_score)} context={_fmt(report.context_score)} "
            f"empirical={_fmt(report.empirical_score)} pattern={report.pattern_score:.2f}",
            "layer_weights: " + ", ".join(f"{name}={value}" for name, value in sorted(report.layer_weights.items())),
            "technical_weights: " + ", ".join(f"{name}={value}" for name, value in sorted(report.technical_weights.items())),
            "score_components:",
        ])
        for name, value in sorted(report.score_components.items()):
            lines.append(f"  - {name}={value}")
        lines.append("approval_gate_results:")
        for gate in report.approval_gate_results:
            state = "pass" if gate.passed else "fail"
            lines.append(f"  - [{gate.layer}] {gate.name}: {state} value={gate.value} min={gate.minimum} margin={gate.margin} :: {gate.reason}")
        lines.append("bot_gate_results:")
        for gate in report.bot_gate_results:
            state = "pass" if gate.passed else "fail"
            lines.append(f"  - [{gate.layer}] {gate.name}: {state} value={gate.value} min={gate.minimum} margin={gate.margin} :: {gate.reason}")
        lines.append("rejection_reasons=" + ("; ".join(report.rejection_reasons) if report.rejection_reasons else "none"))
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Gate result builders (Task F)
# --------------------------------------------------------------------------- #
def _approval_gate_results(opportunity: Opportunity, settings: AppSettings, active_min: float) -> list[GateResult]:
    approval = settings.approval
    instrument = instrument_for_symbol(opportunity.symbol)
    quality = _data_quality_score(opportunity)
    spread_atr = _spread_atr_ratio(opportunity)
    required_rr = opportunity.required_min_rr or settings.styles[opportunity.style].min_rr
    return [
        _min_gate_result("final score", "score", opportunity.final_score, active_min, "Final scanner score must meet the active scanner threshold."),
        _min_gate_result("risk/reward", "risk", opportunity.risk_reward, required_rr, "Planned reward must compensate configured paper/demo risk."),
        _min_gate_result("execution score", "execution", opportunity.execution_score, approval.minimum_execution_score, "Execution layer must clear approval quality."),
        _min_gate_result("context score", "context", opportunity.context_score, approval.minimum_context_score, "Market/session context must clear approval quality."),
        _min_gate_result("empirical score", "empirical", opportunity.empirical_score, approval.minimum_empirical_score, "Historical calibration support must clear approval quality."),
        _min_gate_result("data quality", "data", quality, approval.minimum_data_quality_score, "Input data quality must clear approval quality."),
        _min_gate_result("activation quality", "execution", opportunity.activation_quality, approval.minimum_activation_quality, "Activation evidence must be strong enough."),
        _min_gate_result("invalidation quality", "execution", opportunity.invalidation_quality, approval.minimum_invalidation_quality, "Invalidation evidence must be strong enough."),
        _max_gate_result("spread/ATR", "market", spread_atr, instrument.max_spread_atr, "Spread friction must stay below the instrument max_spread_atr."),
    ]


def _bot_gate_results(opportunity: Opportunity, settings: AppSettings, bot_decision: Any | None) -> list[GateResult]:
    demo_config = DemoBotConfig.from_settings(settings)
    status = opportunity.status.value
    status_allowed = status in EXECUTABLE_DEMO_STATUSES and status in demo_config.allowed_statuses
    levels_present = all(
        value is not None
        for value in (opportunity.entry, opportunity.stop_loss, opportunity.take_profit, opportunity.tp1, opportunity.tp2, opportunity.tp3)
    )
    direction_executable = opportunity.direction.value in {"long", "short"}
    return [
        _min_gate_result("demo bot min score", "bot", opportunity.final_score, demo_config.min_score, "Paper demo bot requires its configured min_score."),
        _min_gate_result("demo bot risk/reward", "bot", opportunity.risk_reward, demo_config.min_rr, "Paper demo bot requires its configured minimum RR."),
        _bool_gate_result(
            "status allowed",
            "bot",
            status,
            ",".join(sorted(demo_config.allowed_statuses)),
            status_allowed,
            "Demo bot only executes approved/premium statuses enabled by configuration.",
        ),
        _bool_gate_result(
            "executable levels present",
            "risk",
            levels_present,
            True,
            levels_present,
            "Entry, stop-loss, take-profit, and staged targets must all be present.",
        ),
        _bool_gate_result(
            "direction executable",
            "bot",
            opportunity.direction.value,
            "long|short",
            direction_executable,
            "Only long/short setups are executable in paper/demo mode.",
        ),
    ]


def _min_gate_result(name: str, layer: str, value: float | None, minimum: float, reason: str) -> GateResult:
    numeric = float(value) if value is not None else None
    passed = numeric is not None and numeric >= minimum - 1e-9
    margin = None if numeric is None else round(numeric - minimum, 4)
    return GateResult(
        name=name,
        layer=layer,
        value=None if numeric is None else round(numeric, 4),
        minimum=round(minimum, 4),
        margin=margin,
        passed=passed,
        severity=_severity(passed, margin),
        reason=reason,
    )


def _max_gate_result(name: str, layer: str, value: float | None, maximum: float, reason: str) -> GateResult:
    numeric = float(value) if value is not None else None
    # Missing spread/ATR is not a friction failure; it cannot be measured.
    passed = numeric is None or numeric <= maximum + 1e-9
    margin = None if numeric is None else round(maximum - numeric, 4)
    return GateResult(
        name=name,
        layer=layer,
        value=None if numeric is None else round(numeric, 4),
        minimum="not above maximum",
        maximum=round(maximum, 4),
        margin=margin,
        passed=passed,
        severity=_severity(passed, margin),
        reason=reason,
    )


def _bool_gate_result(name: str, layer: str, value: Any, minimum: Any, passed: bool, reason: str) -> GateResult:
    return GateResult(
        name=name,
        layer=layer,
        value=value,
        minimum=minimum,
        margin=None,
        passed=passed,
        severity="info" if passed else "blocker",
        reason=reason,
    )


# --------------------------------------------------------------------------- #
# Backward-compatible GateMargin builders
# --------------------------------------------------------------------------- #
def _approval_gates(opportunity: Opportunity, settings: AppSettings, active_min: float) -> list[GateMargin]:
    approval = settings.approval
    quality = _data_quality_score(opportunity)
    return [
        _min_gate("final score", opportunity.final_score, active_min, "Final scanner score must meet the active scanner threshold."),
        _min_gate("risk/reward", opportunity.risk_reward, opportunity.required_min_rr or settings.styles[opportunity.style].min_rr, "Planned reward must compensate configured paper/demo risk."),
        _min_gate("execution score", opportunity.execution_score, approval.minimum_execution_score, "Execution layer must clear approval quality."),
        _min_gate("context score", opportunity.context_score, approval.minimum_context_score, "Market/session context must clear approval quality."),
        _min_gate("empirical score", opportunity.empirical_score, approval.minimum_empirical_score, "Historical calibration support must clear approval quality."),
        _min_gate("data quality", quality, approval.minimum_data_quality_score, "Input data quality must clear approval quality."),
        _min_gate("activation quality", opportunity.activation_quality, approval.minimum_activation_quality, "Activation evidence must be strong enough."),
        _min_gate("invalidation quality", opportunity.invalidation_quality, approval.minimum_invalidation_quality, "Invalidation evidence must be strong enough."),
    ]


def _bot_gates(opportunity: Opportunity, settings: AppSettings, bot_decision: Any | None) -> list[GateMargin]:
    demo_config = DemoBotConfig.from_settings(settings)
    accepted = bool(getattr(bot_decision, "accepted", False)) if bot_decision is not None else opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}
    return [
        _min_gate("demo bot final score", opportunity.final_score, demo_config.min_score, "Paper demo bot requires its configured min_score."),
        _min_gate("demo bot risk/reward", opportunity.risk_reward, demo_config.min_rr, "Paper demo bot requires its configured minimum RR."),
        GateMargin(name="demo bot decision", value=accepted, minimum=True, passed=accepted, severity="info" if accepted else "blocker", explanation="Bot decision after paper/demo safety gates."),
    ]


def _gate_margin_report(opportunity: Opportunity, settings: AppSettings, active_min: float, bot_decision: Any | None) -> list[GateMargin]:
    instrument = instrument_for_symbol(opportunity.symbol)
    quality = _data_quality_score(opportunity)
    spread_atr = _spread_atr_ratio(opportunity)
    session = opportunity.session.value if opportunity.session else "unknown"
    allowed = session in instrument.allowed_sessions
    return [
        _min_gate("final score", opportunity.final_score, active_min, "Final score versus active scanner threshold."),
        _min_gate("risk/reward", opportunity.risk_reward, opportunity.required_min_rr or instrument.min_risk_reward, "Risk/reward versus required minimum."),
        _min_gate("execution score", opportunity.execution_score, settings.approval.minimum_execution_score, "Execution score approval margin."),
        _min_gate("context score", opportunity.context_score, settings.approval.minimum_context_score, "Context score approval margin."),
        _min_gate("empirical score", opportunity.empirical_score, settings.approval.minimum_empirical_score, "Empirical score approval margin."),
        _min_gate("data quality", quality, settings.approval.minimum_data_quality_score, "Data quality approval margin."),
        _min_gate("activation quality", opportunity.activation_quality, settings.approval.minimum_activation_quality, "Activation quality approval margin."),
        _min_gate("invalidation quality", opportunity.invalidation_quality, settings.approval.minimum_invalidation_quality, "Invalidation quality approval margin."),
        _max_gate("spread/ATR", spread_atr, instrument.max_spread_atr, "Spread friction must stay below the instrument max_spread_atr."),
        GateMargin(name="session", value=session, minimum=", ".join(instrument.allowed_sessions), passed=allowed, severity="info" if allowed else "warning", explanation="Current coarse FX session compared with instrument demo sessions."),
        GateMargin(name="market regime", value=opportunity.regime.value, minimum="tradable setup regime", passed=opportunity.regime.value != "no-trade", severity="info" if opportunity.regime.value != "no-trade" else "blocker", explanation="Market regime should not be no-trade."),
        GateMargin(name="operator controls", value="not available in scanner trace", minimum="maintenance=false,degraded=false", passed=True, severity="info", explanation="Operator controls are evaluated by demo bot cycle when available."),
        GateMargin(name="daily risk", value="evaluated by demo bot", minimum="daily limits pass", passed=not _bot_reasons_contain(bot_decision, ("daily", "trades_today", "open trades", "cooldown")), severity="info", explanation="Daily risk limits remain enforced by the paper demo bot."),
    ]


def _min_gate(name: str, value: float | None, minimum: float, explanation: str) -> GateMargin:
    numeric = float(value) if value is not None else None
    passed = numeric is not None and numeric >= minimum - 1e-9
    margin = None if numeric is None else round(numeric - minimum, 4)
    return GateMargin(name=name, value=None if numeric is None else round(numeric, 4), minimum=round(minimum, 4), margin=margin, passed=passed, severity=_severity(passed, margin), explanation=explanation)


def _max_gate(name: str, value: float | None, maximum: float, explanation: str) -> GateMargin:
    numeric = float(value) if value is not None else None
    passed = numeric is not None and numeric <= maximum + 1e-9
    margin = None if numeric is None else round(maximum - numeric, 4)
    return GateMargin(name=name, value=None if numeric is None else round(numeric, 4), minimum="not above maximum", maximum=round(maximum, 4), margin=margin, passed=passed, severity=_severity(passed, margin), explanation=explanation)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _threshold_warnings(scanner_threshold: float, demo_min: float, instrument_min: float, base: float, bot_threshold: float) -> list[str]:
    warnings: list[str] = []
    if abs(scanner_threshold - demo_min) > 1e-9:
        warnings.append(
            f"scanner threshold {scanner_threshold:.1f} differs from demo_bot.min_score {demo_min:.1f}; bot uses the stricter "
            "configured paper/demo gate unless adaptive scanner_effective is active"
        )
    if abs(instrument_min - base) > 1e-9:
        warnings.append(f"adaptive base_min_score {base:.1f} differs from instrument min_score {instrument_min:.1f}")
    if bot_threshold < scanner_threshold - 1e-9:
        warnings.append("effective bot threshold is below scanner threshold; this is diagnostic only and does not relax scanner approval")
    return warnings


def _severity(passed: bool, margin: float | None) -> str:
    if passed:
        return "info"
    if margin is None or margin <= -10:
        return "blocker"
    return "warning"


def _strongest_positive_factors(trace: DecisionTrace, top_n: int = 3) -> list[tuple[str, float]]:
    ranked = sorted(trace.score_components.items(), key=lambda item: item[1], reverse=True)
    return [(name, value) for name, value in ranked[:top_n] if value > 0]


def _weakest_gates(gates: list[GateResult], top_n: int = 3) -> list[GateResult]:
    failed = [gate for gate in gates if not gate.passed]
    return sorted(failed, key=lambda gate: (gate.margin if gate.margin is not None else -1e9))[:top_n]


def _next_diagnostic_action(trace: DecisionTrace) -> str:
    if trace.accepted:
        return "none; opportunity accepted in paper/demo mode"
    weakest = _weakest_gates(trace.gate_results, top_n=1)
    if weakest:
        gate = weakest[0]
        if gate.layer == "score":
            return f"inspect score_decomposition for {trace.symbol}; final score below active min {trace.active_min_score:.1f}"
        if gate.layer == "bot":
            return f"review min_score_policy_report for {trace.symbol}; bot gate '{gate.name}' failed"
        return f"investigate '{gate.name}' ({gate.layer} layer); margin={gate.margin}"
    if trace.primary_rejection_reason:
        return f"address primary rejection reason: {trace.primary_rejection_reason}"
    return "no blocking gate detected; re-run scan to refresh diagnostics"


def _reasons(opportunity: Opportunity, bot_decision: Any | None) -> list[str]:
    reasons: list[str] = []
    if opportunity.rejection_reason:
        reasons.append(opportunity.rejection_reason)
    reasons.extend(opportunity.missing_conditions)
    reasons.extend(str(reason) for reason in getattr(bot_decision, "reasons", []) or [])
    return list(dict.fromkeys(reason for reason in reasons if reason))


def _paper_preflight(bot_decision: Any | None, paper_order_ids: list[str] | None) -> dict[str, Any]:
    if bot_decision is None:
        return {"status": "not_run", "created_order_ids": paper_order_ids or [], "explanation": "Scanner-only trace; paper order preflight was not executed."}
    accepted = bool(getattr(bot_decision, "accepted", False))
    order_ids = list(paper_order_ids or getattr(bot_decision, "order_ids", []) or [])
    if accepted and order_ids:
        status = "paper_order_created"
        explanation = "Paper-only order was created after demo bot gates."
    elif accepted:
        status = "accepted_no_order"
        explanation = "Demo bot accepted, but no paper order id was recorded."
    else:
        status = "blocked"
        explanation = "; ".join(getattr(bot_decision, "reasons", []) or []) or "Demo bot blocked this opportunity."
    return {"status": status, "created_order_ids": order_ids, "explanation": explanation}


def _bot_reasons_contain(bot_decision: Any | None, needles: tuple[str, ...]) -> bool:
    haystack = " ".join(str(reason).lower() for reason in getattr(bot_decision, "reasons", []) or [])
    return any(needle in haystack for needle in needles)


def _data_quality_score(opportunity: Opportunity) -> float:
    return opportunity.data_quality.score if opportunity.data_quality else 100.0


def _spread_atr_ratio(opportunity: Opportunity) -> float | None:
    if opportunity.spread is None or opportunity.atr in (None, 0):
        return None
    return round(opportunity.spread / opportunity.atr, 6)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SECRET_KEY_PARTS):
                clean[str(key)] = "[redacted]"
            else:
                clean[str(key)] = _sanitize(item)
        return clean
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value
