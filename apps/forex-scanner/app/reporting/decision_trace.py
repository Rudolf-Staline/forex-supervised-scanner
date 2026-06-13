"""Decision trace and threshold policy reporting for paper/demo decisions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config.instruments import instrument_for_symbol
from app.config.settings import AppSettings
from app.core.types import Opportunity, OpportunityStatus
from app.execution.demo_bot_config import DemoBotConfig

SECRET_KEY_PARTS = ("secret", "password", "token", "login", "account", "server", "key", ".env")
DEFAULT_DECISION_TRACE_JSON = "decision_trace.json"
DEFAULT_DECISION_TRACE_TXT = "decision_trace.txt"
DEFAULT_MIN_SCORE_POLICY_JSON = "min_score_policy_report.json"
DEFAULT_MIN_SCORE_POLICY_TXT = "min_score_policy_report.txt"


class GateMargin(BaseModel):
    """Human readable margin for an approval, bot, market, or risk gate."""

    name: str
    value: float | str | bool | None = None
    minimum: float | str | bool | None = None
    maximum: float | None = None
    margin: float | None = None
    passed: bool
    severity: str = "info"
    explanation: str


class MinScorePolicyReport(BaseModel):
    """Explain scanner-vs-bot threshold policy for one opportunity."""

    symbol: str
    style: str
    instrument_min_score: float
    adaptive_base_min_score: float | None = None
    adaptive_recommended_min_score: float | None = None
    adaptive_effective_min_score: float | None = None
    demo_bot_min_score: float
    effective_scanner_threshold: float
    effective_bot_threshold: float
    threshold_source: str
    mismatch_warnings: list[str] = Field(default_factory=list)


class DecisionTrace(BaseModel):
    """Structured, sanitized explanation of one scanner/bot decision."""

    trace_id: str
    generated_at: datetime
    symbol: str
    style: str
    raw_setup: dict[str, Any]
    risk_plan: dict[str, Any]
    score_components: dict[str, float]
    weights: dict[str, Any]
    technical_score: float | None = None
    execution_score: float | None = None
    context_score: float | None = None
    empirical_score: float | None = None
    pattern_score: float = 0.0
    final_score: float | None = None
    active_min_score: float
    threshold_source: str
    status: str
    approval_gates: list[GateMargin]
    bot_gates: list[GateMargin]
    gate_margin_report: list[GateMargin]
    rejection_reasons: list[str] = Field(default_factory=list)
    paper_order_preflight_result: dict[str, Any] = Field(default_factory=dict)
    min_score_policy: MinScorePolicyReport
    safety: dict[str, Any] = Field(default_factory=lambda: {"mode": "paper/demo only", "live_trading": False})


def build_decision_trace(
    opportunity: Opportunity,
    settings: AppSettings,
    *,
    bot_decision: Any | None = None,
    paper_order_ids: list[str] | None = None,
) -> DecisionTrace:
    """Build a sanitized decision trace without credentials or raw environment values."""

    instrument = instrument_for_symbol(opportunity.symbol)
    policy = build_min_score_policy(opportunity, settings)
    active_min = policy.effective_scanner_threshold
    reasons = _reasons(opportunity, bot_decision)
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
    return DecisionTrace(
        trace_id=str(uuid.uuid4()),
        generated_at=datetime.now(timezone.utc),
        symbol=opportunity.symbol,
        style=opportunity.style.value,
        raw_setup=_sanitize(raw_setup),
        risk_plan=_sanitize(risk_plan),
        score_components={key: round(float(value), 4) for key, value in opportunity.score_components.items()},
        weights={"components": settings.weights.as_dict(), "layers": settings.layer_weights.as_dict()},
        technical_score=opportunity.technical_score,
        execution_score=opportunity.execution_score,
        context_score=opportunity.context_score,
        empirical_score=opportunity.empirical_score,
        pattern_score=opportunity.pattern_score,
        final_score=opportunity.final_score,
        active_min_score=active_min,
        threshold_source=policy.threshold_source,
        status=opportunity.status.value,
        approval_gates=_approval_gates(opportunity, settings, active_min),
        bot_gates=_bot_gates(opportunity, settings, bot_decision),
        gate_margin_report=_gate_margin_report(opportunity, settings, active_min, bot_decision),
        rejection_reasons=reasons,
        paper_order_preflight_result=_paper_preflight(bot_decision, paper_order_ids),
        min_score_policy=policy,
    )


def build_min_score_policy(opportunity: Opportunity, settings: AppSettings) -> MinScorePolicyReport:
    """Build scanner/bot threshold diagnostics and mismatch warnings."""

    instrument = instrument_for_symbol(opportunity.symbol)
    demo_min = DemoBotConfig.from_settings(settings).min_score
    adaptive_enabled = bool(getattr(opportunity, "adaptive_threshold_enabled", False))
    mode = getattr(getattr(settings, "adaptive_thresholds", None), "mode", "report_only")
    base = opportunity.base_min_score if opportunity.base_min_score is not None else instrument.min_score
    effective = opportunity.effective_min_score if opportunity.effective_min_score is not None else base
    scanner_threshold = effective if adaptive_enabled and mode == "scanner_effective" else base
    bot_threshold = effective if adaptive_enabled and mode == "scanner_effective" else demo_min
    source = "adaptive" if adaptive_enabled and mode == "scanner_effective" else "instrument/static"
    warnings: list[str] = []
    if abs(scanner_threshold - demo_min) > 1e-9:
        warnings.append(
            f"scanner threshold {scanner_threshold:.1f} differs from demo_bot.min_score {demo_min:.1f}; bot uses the stricter configured paper/demo gate unless adaptive scanner_effective is active"
        )
    if instrument.min_score != base:
        warnings.append(f"adaptive base_min_score {base:.1f} differs from instrument min_score {instrument.min_score:.1f}")
    if bot_threshold < scanner_threshold:
        warnings.append("effective bot threshold is below scanner threshold; this is diagnostic only and does not relax scanner approval")
    return MinScorePolicyReport(
        symbol=opportunity.symbol,
        style=opportunity.style.value,
        instrument_min_score=instrument.min_score,
        adaptive_base_min_score=opportunity.base_min_score,
        adaptive_recommended_min_score=opportunity.adaptive_min_score,
        adaptive_effective_min_score=opportunity.effective_min_score,
        demo_bot_min_score=demo_min,
        effective_scanner_threshold=scanner_threshold,
        effective_bot_threshold=bot_threshold,
        threshold_source=source,
        mismatch_warnings=warnings,
    )


def export_decision_traces(traces: list[DecisionTrace], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / DEFAULT_DECISION_TRACE_JSON
    txt_path = reports_dir / DEFAULT_DECISION_TRACE_TXT
    json_path.write_text(json.dumps([_sanitize(t.model_dump(mode="json")) for t in traces], indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(render_decision_traces_text(traces), encoding="utf-8")
    return json_path, txt_path


def export_min_score_policy_report(traces: list[DecisionTrace], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / DEFAULT_MIN_SCORE_POLICY_JSON
    txt_path = reports_dir / DEFAULT_MIN_SCORE_POLICY_TXT
    policies = [trace.min_score_policy for trace in traces]
    json_path.write_text(json.dumps([p.model_dump(mode="json") for p in policies], indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(render_min_score_policy_text(policies), encoding="utf-8")
    return json_path, txt_path


def render_decision_traces_text(traces: list[DecisionTrace]) -> str:
    lines = ["Decision Trace Report", "mode=paper/demo only live_trading=false", ""]
    if not traces:
        lines.append("No decisions available.")
        return "\n".join(lines) + "\n"
    for trace in traces:
        lines.extend([
            f"trace_id={trace.trace_id}",
            f"symbol={trace.symbol} style={trace.style} setup={trace.raw_setup.get('family')}/{trace.raw_setup.get('subtype')} status={trace.status}",
            f"final_score={_fmt(trace.final_score)} active_min_score={trace.active_min_score:.1f} threshold_source={trace.threshold_source}",
            f"technical={_fmt(trace.technical_score)} execution={_fmt(trace.execution_score)} context={_fmt(trace.context_score)} empirical={_fmt(trace.empirical_score)} pattern={trace.pattern_score:.2f}",
            f"risk_reward={_fmt(trace.risk_plan.get('risk_reward'))} preflight={trace.paper_order_preflight_result.get('status')}",
            "reasons=" + ("; ".join(trace.rejection_reasons) if trace.rejection_reasons else "none"),
            "gate_margins:",
        ])
        for gate in trace.gate_margin_report:
            state = "pass" if gate.passed else "fail"
            lines.append(f"  - {gate.name}: {state} value={gate.value} min={gate.minimum} max={gate.maximum} margin={gate.margin} severity={gate.severity} :: {gate.explanation}")
        lines.append("score_components:")
        for name, value in sorted(trace.score_components.items()):
            lines.append(f"  - {name}={value}")
        lines.append("weights:")
        for group, weights in trace.weights.items():
            lines.append(f"  - {group}={weights}")
        lines.append("")
    return "\n".join(lines)


def render_min_score_policy_text(policies: list[MinScorePolicyReport]) -> str:
    lines = ["Min Score Policy Report", "mode=paper/demo only; thresholds are diagnostic and not auto-mutated", ""]
    if not policies:
        lines.append("No threshold policies available.")
        return "\n".join(lines) + "\n"
    for policy in policies:
        lines.extend([
            f"symbol={policy.symbol} style={policy.style}",
            f"instrument_min_score={policy.instrument_min_score:.1f} adaptive_base={_fmt(policy.adaptive_base_min_score)} adaptive_recommended={_fmt(policy.adaptive_recommended_min_score)} adaptive_effective={_fmt(policy.adaptive_effective_min_score)}",
            f"demo_bot_min_score={policy.demo_bot_min_score:.1f} effective_scanner_threshold={policy.effective_scanner_threshold:.1f} effective_bot_threshold={policy.effective_bot_threshold:.1f} source={policy.threshold_source}",
            "warnings=" + ("; ".join(policy.mismatch_warnings) if policy.mismatch_warnings else "none"),
            "",
        ])
    return "\n".join(lines)


def _approval_gates(opportunity: Opportunity, settings: AppSettings, active_min: float) -> list[GateMargin]:
    approval = settings.approval
    quality = opportunity.data_quality.score if opportunity.data_quality else 100.0
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
    quality = opportunity.data_quality.score if opportunity.data_quality else 100.0
    spread_atr = None
    if opportunity.spread is not None and opportunity.atr not in (None, 0):
        spread_atr = opportunity.spread / opportunity.atr
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


def _severity(passed: bool, margin: float | None) -> str:
    if passed:
        return "info"
    if margin is None or margin <= -10:
        return "blocker"
    return "warning"


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
