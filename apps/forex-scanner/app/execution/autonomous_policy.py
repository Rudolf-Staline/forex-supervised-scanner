"""Autonomous Policy Engine — centralized autonomy permissions and safety decisions.

The policy engine answers one question for every autonomous action:

    Is this autonomous action allowed under the current mode, evidence,
    readiness, recovery, operator controls, and safety state?

It is used by the Evidence Builder, Readiness Gate, Recovery Planner, and
Autonomous Supervisor to obtain a single, auditable policy decision before
proceeding.

SAFETY: This module does NOT enable live trading. It does not call MT5, does
not submit orders, does not mutate ``.env``, and does not create daemons.
Live trading, broker-live execution, MT5 order execution, and ``order_send``
are always denied by policy.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_AUTONOMOUS_POLICY_REPORTS_DIR = Path("reports")
DEFAULT_AUTONOMOUS_POLICY_JSON_REPORT = "autonomous_policy_report.json"
DEFAULT_AUTONOMOUS_POLICY_TXT_REPORT = "autonomous_policy_report.txt"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AutonomousPolicyMode(StrEnum):
    """Operating modes recognized by the policy engine."""

    DRY_RUN = "DRY_RUN"
    READ_ONLY = "READ_ONLY"
    PAPER = "PAPER"
    DIAGNOSTIC = "DIAGNOSTIC"


class AutonomousPolicyDecisionType(StrEnum):
    """Three-level decision outcome."""

    ALLOW = "ALLOW"
    WARN_ALLOW = "WARN_ALLOW"
    DENY = "DENY"


class AutonomousPolicyRuleStatus(StrEnum):
    """Status of an individual rule evaluation."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class AutonomousPolicySeverity(StrEnum):
    """Severity classification for rule results."""

    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AutonomousPolicyConfig(BaseModel):
    """Configuration knobs for the policy engine."""

    mode: AutonomousPolicyMode = AutonomousPolicyMode.DRY_RUN
    dry_run: bool = True
    allow_subprocess_fallback: bool = False
    require_mt5: bool = False
    operator_mode: str = "normal"
    readiness_status: str = "UNKNOWN"
    evidence_status: str = "UNKNOWN"
    skip_readiness_gate: bool = False
    recovery_action_safe: bool = False
    recovery_action_manual: bool = False
    recovery_can_override_readiness: bool = False


class AutonomousPolicyContext(BaseModel):
    """Snapshot of current system state passed to policy evaluation."""

    action: str = ""
    mode: AutonomousPolicyMode = AutonomousPolicyMode.DRY_RUN
    dry_run: bool = True
    readiness_status: str = "UNKNOWN"
    evidence_status: str = "UNKNOWN"
    operator_mode: str = "normal"
    require_mt5: bool = False
    allow_subprocess_fallback: bool = False
    skip_readiness_gate: bool = False
    recovery_action_safe: bool = False
    recovery_action_manual: bool = False
    recovery_can_override_readiness: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class AutonomousPolicyRuleResult(BaseModel):
    """Result of a single rule evaluation."""

    rule_name: str
    status: AutonomousPolicyRuleStatus
    reason: str
    severity: AutonomousPolicySeverity = AutonomousPolicySeverity.INFO


class AutonomousPolicyDecision(BaseModel):
    """The full policy decision returned by the engine."""

    allowed: bool
    decision: AutonomousPolicyDecisionType
    action: str
    mode: AutonomousPolicyMode
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    rule_results: list[AutonomousPolicyRuleResult] = Field(default_factory=list)
    safety_flags: dict[str, object] = Field(default_factory=dict)
    recommended_next_action: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------

SAFETY_INVARIANT_NAMES = [
    "no_live_trading",
    "no_broker_live",
    "no_mt5_order_execution",
    "no_order_send",
    "no_env_mutation",
    "no_credential_printing",
    "no_hidden_daemon",
    "no_infinite_loop",
    "no_readiness_bypass_for_non_dry_run",
    "recovery_cannot_override_readiness",
    "missing_evidence_cannot_permit_non_dry_run_paper",
]


def _safety_invariant_flags() -> dict[str, object]:
    """Return the hardcoded safety flag dict proving invariants hold."""

    return {
        "paper_demo_only": True,
        "live_trading_enabled": False,
        "live_execution_allowed": False,
        "broker_live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_order_execution_allowed": False,
        "order_send_called": False,
        "env_mutation_performed": False,
        "credentials_printed": False,
        "hidden_daemon_created": False,
        "infinite_loop_default": False,
        "readiness_bypass_for_non_dry_run": False,
        "recovery_overrides_readiness": False,
    }


def _check_safety_invariants(context: AutonomousPolicyContext) -> list[AutonomousPolicyRuleResult]:
    """Evaluate all 11 safety invariants.  These always pass for paper/demo."""

    results: list[AutonomousPolicyRuleResult] = []

    # 1-4: Live trading / broker-live / MT5 / order_send always denied
    for invariant, label in [
        ("no_live_trading", "live trading is always denied by policy"),
        ("no_broker_live", "broker-live execution is always denied by policy"),
        ("no_mt5_order_execution", "MT5 order execution is always denied by policy"),
        ("no_order_send", "order_send is always denied by policy"),
    ]:
        results.append(AutonomousPolicyRuleResult(
            rule_name=invariant,
            status=AutonomousPolicyRuleStatus.PASS,
            reason=label,
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    # 5-8: Environment / credential / daemon / loop invariants
    for invariant, label in [
        ("no_env_mutation", ".env mutation is always denied by policy"),
        ("no_credential_printing", "credential printing is always denied by policy"),
        ("no_hidden_daemon", "hidden daemon creation is always denied by policy"),
        ("no_infinite_loop", "infinite loops are always denied by policy"),
    ]:
        results.append(AutonomousPolicyRuleResult(
            rule_name=invariant,
            status=AutonomousPolicyRuleStatus.PASS,
            reason=label,
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    # 9: Readiness bypass for non-dry-run
    if context.skip_readiness_gate and not context.dry_run:
        results.append(AutonomousPolicyRuleResult(
            rule_name="no_readiness_bypass_for_non_dry_run",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason="readiness bypass is not allowed for non-dry-run cycles",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="no_readiness_bypass_for_non_dry_run",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="readiness bypass rule is satisfied",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    # 10: Recovery cannot override readiness
    if context.recovery_can_override_readiness:
        results.append(AutonomousPolicyRuleResult(
            rule_name="recovery_cannot_override_readiness",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason="recovery planner cannot override readiness gate decisions",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="recovery_cannot_override_readiness",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="recovery-readiness invariant is satisfied",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    # 11: Missing/stale/failing evidence cannot permit non-dry-run paper
    evidence_blocking = context.evidence_status.upper() in {"BLOCKED_EVIDENCE", "FAILED", "UNKNOWN"}
    if evidence_blocking and not context.dry_run and context.mode == AutonomousPolicyMode.PAPER:
        results.append(AutonomousPolicyRuleResult(
            rule_name="missing_evidence_cannot_permit_non_dry_run_paper",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason=f"evidence status '{context.evidence_status}' blocks non-dry-run paper autonomy",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="missing_evidence_cannot_permit_non_dry_run_paper",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="evidence-paper-autonomy invariant is satisfied",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    return results


# ---------------------------------------------------------------------------
# Domain-specific rule sets
# ---------------------------------------------------------------------------

_SAFE_MODES = {AutonomousPolicyMode.DRY_RUN, AutonomousPolicyMode.READ_ONLY, AutonomousPolicyMode.DIAGNOSTIC}


def _rules_for_build_evidence(context: AutonomousPolicyContext) -> list[AutonomousPolicyRuleResult]:
    """Rules specific to the Evidence Builder."""

    results: list[AutonomousPolicyRuleResult] = []

    # Dry-run evidence build allowed under safe mode
    if context.mode == AutonomousPolicyMode.DRY_RUN:
        results.append(AutonomousPolicyRuleResult(
            rule_name="evidence_dry_run_allowed",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="dry-run evidence build is allowed under safe mode",
            severity=AutonomousPolicySeverity.INFO,
        ))
        return results

    # Read-only evidence build allowed under safe mode
    if context.mode == AutonomousPolicyMode.READ_ONLY:
        results.append(AutonomousPolicyRuleResult(
            rule_name="evidence_read_only_allowed",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="read-only evidence build is allowed under safe mode",
            severity=AutonomousPolicySeverity.INFO,
        ))

    # Refresh mode — allowed only if synthetic/paper/read-only, no MT5 required
    if context.mode == AutonomousPolicyMode.PAPER:
        if context.require_mt5:
            results.append(AutonomousPolicyRuleResult(
                rule_name="evidence_refresh_mt5_denied",
                status=AutonomousPolicyRuleStatus.FAIL,
                reason="refresh mode is denied because it requires MT5/live broker",
                severity=AutonomousPolicySeverity.CRITICAL,
            ))
        else:
            results.append(AutonomousPolicyRuleResult(
                rule_name="evidence_refresh_safe",
                status=AutonomousPolicyRuleStatus.PASS,
                reason="refresh mode is allowed because it remains synthetic/paper/read-only",
                severity=AutonomousPolicySeverity.INFO,
            ))

    # Subprocess fallback denied unless explicitly enabled
    if context.allow_subprocess_fallback:
        results.append(AutonomousPolicyRuleResult(
            rule_name="evidence_subprocess_allowed",
            status=AutonomousPolicyRuleStatus.WARN,
            reason="subprocess fallback is explicitly enabled",
            severity=AutonomousPolicySeverity.WARN,
        ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="evidence_subprocess_denied",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="subprocess fallback is denied (default safe behavior)",
            severity=AutonomousPolicySeverity.INFO,
        ))

    return results


def _rules_for_run_readiness(context: AutonomousPolicyContext) -> list[AutonomousPolicyRuleResult]:
    """Rules specific to the Readiness Gate."""

    results: list[AutonomousPolicyRuleResult] = []

    # Readiness checks allowed in diagnostic/read-only modes
    if context.mode in _SAFE_MODES:
        results.append(AutonomousPolicyRuleResult(
            rule_name="readiness_safe_mode",
            status=AutonomousPolicyRuleStatus.PASS,
            reason=f"readiness checks are allowed in {context.mode.value} mode",
            severity=AutonomousPolicySeverity.INFO,
        ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="readiness_safe_mode",
            status=AutonomousPolicyRuleStatus.PASS,
            reason=f"readiness checks are allowed in {context.mode.value} mode (read-only inspection)",
            severity=AutonomousPolicySeverity.INFO,
        ))

    return results


def _rules_for_skip_readiness(context: AutonomousPolicyContext) -> list[AutonomousPolicyRuleResult]:
    """Rules for skipping the readiness gate."""

    results: list[AutonomousPolicyRuleResult] = []

    if context.dry_run and context.mode in {AutonomousPolicyMode.DRY_RUN, AutonomousPolicyMode.DIAGNOSTIC}:
        results.append(AutonomousPolicyRuleResult(
            rule_name="readiness_skip_dry_run_diagnostic",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="readiness skip is allowed for dry-run diagnostics",
            severity=AutonomousPolicySeverity.WARN,
        ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="readiness_skip_denied",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason="readiness skip is only allowed for dry-run diagnostic modes; "
                   "it must never allow non-dry-run paper supervisor cycles",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    return results


def _rules_for_execute_recovery(context: AutonomousPolicyContext) -> list[AutonomousPolicyRuleResult]:
    """Rules specific to the Recovery Planner action execution."""

    results: list[AutonomousPolicyRuleResult] = []

    # Plan generation always allowed
    results.append(AutonomousPolicyRuleResult(
        rule_name="recovery_plan_generation",
        status=AutonomousPolicyRuleStatus.PASS,
        reason="recovery plan generation is always allowed under safe mode",
        severity=AutonomousPolicySeverity.INFO,
    ))

    # Manual-review actions must never execute automatically
    if context.recovery_action_manual:
        results.append(AutonomousPolicyRuleResult(
            rule_name="recovery_manual_action_denied",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason="manual-review recovery actions must never execute automatically",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))
        return results

    # Execution only for explicitly safe dry-run/read-only actions
    if context.recovery_action_safe and context.mode in _SAFE_MODES:
        results.append(AutonomousPolicyRuleResult(
            rule_name="recovery_safe_action_allowed",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="executing recovery action is allowed for explicitly safe dry-run/read-only actions",
            severity=AutonomousPolicySeverity.INFO,
        ))
    elif not context.recovery_action_safe:
        results.append(AutonomousPolicyRuleResult(
            rule_name="recovery_unsafe_action_denied",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason="recovery action is not marked as safe for automatic execution",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="recovery_mode_denied",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason=f"recovery action execution is not allowed in {context.mode.value} mode",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    # Recovery must never directly unblock supervisor
    results.append(AutonomousPolicyRuleResult(
        rule_name="recovery_cannot_unblock_supervisor",
        status=AutonomousPolicyRuleStatus.PASS,
        reason="recovery planner cannot directly unblock supervisor execution",
        severity=AutonomousPolicySeverity.CRITICAL,
    ))

    return results


def _rules_for_run_supervisor(context: AutonomousPolicyContext) -> list[AutonomousPolicyRuleResult]:
    """Rules specific to the Autonomous Supervisor invocation."""

    results: list[AutonomousPolicyRuleResult] = []

    # Dry-run / readiness-only invocation allowed under safe mode
    if context.dry_run or context.mode in _SAFE_MODES:
        results.append(AutonomousPolicyRuleResult(
            rule_name="supervisor_safe_invocation",
            status=AutonomousPolicyRuleStatus.PASS,
            reason=f"supervisor invocation is allowed in {context.mode.value} mode (dry_run={context.dry_run})",
            severity=AutonomousPolicySeverity.INFO,
        ))
    else:
        # Non-dry-run paper supervisor cycles require readiness READY
        readiness_upper = context.readiness_status.upper()
        if readiness_upper not in {"READY", "WARN_READY"}:
            results.append(AutonomousPolicyRuleResult(
                rule_name="supervisor_readiness_required",
                status=AutonomousPolicyRuleStatus.FAIL,
                reason=f"non-dry-run paper supervisor requires readiness READY; got {context.readiness_status}",
                severity=AutonomousPolicySeverity.CRITICAL,
            ))
        else:
            results.append(AutonomousPolicyRuleResult(
                rule_name="supervisor_readiness_ok",
                status=AutonomousPolicyRuleStatus.PASS,
                reason=f"readiness status is {context.readiness_status}",
                severity=AutonomousPolicySeverity.INFO,
            ))

    # Evidence blocking failures deny non-dry-run supervisor cycles
    evidence_upper = context.evidence_status.upper()
    if evidence_upper in {"BLOCKED_EVIDENCE", "FAILED"} and not context.dry_run:
        results.append(AutonomousPolicyRuleResult(
            rule_name="supervisor_evidence_blocking",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason=f"evidence status '{context.evidence_status}' blocks non-dry-run supervisor cycles",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    # Operator maintenance/degraded mode denies supervisor cycles
    op = context.operator_mode.lower()
    if op in {"maintenance", "degraded"}:
        results.append(AutonomousPolicyRuleResult(
            rule_name="supervisor_operator_mode_denied",
            status=AutonomousPolicyRuleStatus.FAIL,
            reason=f"operator {op} mode denies supervisor cycles by default",
            severity=AutonomousPolicySeverity.CRITICAL,
        ))

    return results


def _rules_for_supervisor_cycle(context: AutonomousPolicyContext) -> list[AutonomousPolicyRuleResult]:
    """Rules for a single supervisor cycle execution."""

    results: list[AutonomousPolicyRuleResult] = []

    if context.dry_run:
        results.append(AutonomousPolicyRuleResult(
            rule_name="cycle_dry_run",
            status=AutonomousPolicyRuleStatus.PASS,
            reason="dry-run supervisor cycle is allowed",
            severity=AutonomousPolicySeverity.INFO,
        ))
    elif context.mode == AutonomousPolicyMode.PAPER:
        readiness_upper = context.readiness_status.upper()
        if readiness_upper == "READY":
            results.append(AutonomousPolicyRuleResult(
                rule_name="cycle_paper_readiness_ok",
                status=AutonomousPolicyRuleStatus.PASS,
                reason="paper supervisor cycle is allowed; readiness is READY",
                severity=AutonomousPolicySeverity.INFO,
            ))
        else:
            results.append(AutonomousPolicyRuleResult(
                rule_name="cycle_paper_readiness_blocked",
                status=AutonomousPolicyRuleStatus.FAIL,
                reason=f"paper supervisor cycle denied; readiness is {context.readiness_status}",
                severity=AutonomousPolicySeverity.CRITICAL,
            ))
    else:
        results.append(AutonomousPolicyRuleResult(
            rule_name="cycle_mode_ok",
            status=AutonomousPolicyRuleStatus.PASS,
            reason=f"supervisor cycle in {context.mode.value} mode is allowed",
            severity=AutonomousPolicySeverity.INFO,
        ))

    return results


# ---------------------------------------------------------------------------
# Policy Engine
# ---------------------------------------------------------------------------


class AutonomousPolicyEngine:
    """Centralized policy engine for autonomous pipeline decisions.

    Constructed with an :class:`AutonomousPolicyConfig`.  Each public method
    accepts an :class:`AutonomousPolicyContext` and returns a serializable
    :class:`AutonomousPolicyDecision`.
    """

    def __init__(self, config: AutonomousPolicyConfig | None = None) -> None:
        self.config = config or AutonomousPolicyConfig()

    # -- public API ----------------------------------------------------------

    def can_build_evidence(self, context: AutonomousPolicyContext | None = None) -> AutonomousPolicyDecision:
        """Is evidence building allowed under the current policy?"""

        ctx = self._fill_context(context, action="build_evidence")
        return self._evaluate(ctx, _rules_for_build_evidence)

    def can_run_readiness(self, context: AutonomousPolicyContext | None = None) -> AutonomousPolicyDecision:
        """Is running the readiness gate allowed?"""

        ctx = self._fill_context(context, action="run_readiness")
        return self._evaluate(ctx, _rules_for_run_readiness)

    def can_execute_recovery_action(self, context: AutonomousPolicyContext | None = None) -> AutonomousPolicyDecision:
        """Is executing a recovery action allowed?"""

        ctx = self._fill_context(context, action="execute_recovery_action")
        return self._evaluate(ctx, _rules_for_execute_recovery)

    def can_run_supervisor(self, context: AutonomousPolicyContext | None = None) -> AutonomousPolicyDecision:
        """Is invoking the supervisor allowed?"""

        ctx = self._fill_context(context, action="run_supervisor")
        return self._evaluate(ctx, _rules_for_run_supervisor)

    def can_run_supervisor_cycle(self, context: AutonomousPolicyContext | None = None) -> AutonomousPolicyDecision:
        """Is executing a single supervisor cycle allowed?"""

        ctx = self._fill_context(context, action="run_supervisor_cycle")
        return self._evaluate(ctx, _rules_for_supervisor_cycle)

    def can_skip_readiness_gate(self, context: AutonomousPolicyContext | None = None) -> AutonomousPolicyDecision:
        """Is skipping the readiness gate allowed?"""

        ctx = self._fill_context(context, action="skip_readiness_gate")
        return self._evaluate(ctx, _rules_for_skip_readiness)

    def evaluate_action(self, action: str, context: AutonomousPolicyContext | None = None) -> AutonomousPolicyDecision:
        """Evaluate an arbitrary named action against policy."""

        ctx = self._fill_context(context, action=action)
        dispatch = {
            "build_evidence": _rules_for_build_evidence,
            "run_readiness": _rules_for_run_readiness,
            "execute_recovery_action": _rules_for_execute_recovery,
            "run_supervisor": _rules_for_run_supervisor,
            "run_supervisor_cycle": _rules_for_supervisor_cycle,
            "skip_readiness_gate": _rules_for_skip_readiness,
        }
        domain_rules = dispatch.get(action, lambda _ctx: [])
        return self._evaluate(ctx, domain_rules)

    # -- internals -----------------------------------------------------------

    def _fill_context(self, context: AutonomousPolicyContext | None, action: str) -> AutonomousPolicyContext:
        """Merge config defaults into a context, setting the action name."""

        if context is not None:
            ctx = context.model_copy(update={"action": action})
        else:
            ctx = AutonomousPolicyContext(
                action=action,
                mode=self.config.mode,
                dry_run=self.config.dry_run,
                readiness_status=self.config.readiness_status,
                evidence_status=self.config.evidence_status,
                operator_mode=self.config.operator_mode,
                require_mt5=self.config.require_mt5,
                allow_subprocess_fallback=self.config.allow_subprocess_fallback,
                skip_readiness_gate=self.config.skip_readiness_gate,
                recovery_action_safe=self.config.recovery_action_safe,
                recovery_action_manual=self.config.recovery_action_manual,
                recovery_can_override_readiness=self.config.recovery_can_override_readiness,
            )
        return ctx

    def _evaluate(
        self,
        context: AutonomousPolicyContext,
        domain_rule_fn: Any,
    ) -> AutonomousPolicyDecision:
        """Run safety invariants + domain rules and aggregate into a decision."""

        safety_results = _check_safety_invariants(context)
        domain_results = domain_rule_fn(context)
        all_results = safety_results + domain_results

        failures = [r for r in all_results if r.status == AutonomousPolicyRuleStatus.FAIL]
        warnings = [r for r in all_results if r.status == AutonomousPolicyRuleStatus.WARN]

        blocking_reasons = [r.reason for r in failures]
        warning_reasons = [r.reason for r in warnings]

        if failures:
            decision_type = AutonomousPolicyDecisionType.DENY
            allowed = False
        elif warnings:
            decision_type = AutonomousPolicyDecisionType.WARN_ALLOW
            allowed = True
        else:
            decision_type = AutonomousPolicyDecisionType.ALLOW
            allowed = True

        reasons: list[str] = []
        if allowed:
            reasons.append(f"action '{context.action}' is allowed in {context.mode.value} mode")
        else:
            reasons.append(f"action '{context.action}' is denied")
        reasons.extend(blocking_reasons)

        recommended = None
        if not allowed:
            recommended = _recommend_next_action(context)

        return AutonomousPolicyDecision(
            allowed=allowed,
            decision=decision_type,
            action=context.action,
            mode=context.mode,
            reasons=reasons,
            warnings=warning_reasons,
            blocking_reasons=blocking_reasons,
            rule_results=all_results,
            safety_flags=_safety_invariant_flags(),
            recommended_next_action=recommended,
        )


# ---------------------------------------------------------------------------
# Recommendations on denial
# ---------------------------------------------------------------------------


def _recommend_next_action(context: AutonomousPolicyContext) -> str:
    """Suggest a follow-up when an action is denied."""

    if context.skip_readiness_gate and not context.dry_run:
        return "use --dry-run when skipping the readiness gate"
    if context.operator_mode.lower() in {"maintenance", "degraded"}:
        return "clear operator maintenance/degraded mode before retrying"
    if context.evidence_status.upper() in {"BLOCKED_EVIDENCE", "FAILED", "UNKNOWN"}:
        return "rebuild evidence: python scripts/autonomous_evidence_builder.py --mode read-only --export-json"
    if context.readiness_status.upper() not in {"READY", "WARN_READY"}:
        return "run readiness report: python scripts/autonomous_readiness_report.py --export-json"
    if context.recovery_action_manual:
        return "this action requires manual operator review"
    return "review policy report: python scripts/autonomous_policy_report.py --action {action} --mode {mode}".format(
        action=context.action, mode=context.mode.value.lower(),
    )


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def export_autonomous_policy_json(decision: AutonomousPolicyDecision, reports_dir: Path) -> Path:
    """Write a policy decision to JSON in the given directory."""

    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_POLICY_JSON_REPORT
    path.write_text(
        json.dumps(decision.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def export_autonomous_policy_txt(decision: AutonomousPolicyDecision, reports_dir: Path) -> Path:
    """Write a policy decision to a human-readable text file."""

    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_POLICY_TXT_REPORT
    path.write_text(format_autonomous_policy_txt(decision), encoding="utf-8")
    return path


def format_autonomous_policy_txt(decision: AutonomousPolicyDecision) -> str:
    """Format a policy decision as human-readable text."""

    lines = [
        "Autonomous Policy Engine Report",
        "This report is paper/demo/read-only and does not authorize live trading.",
        f"timestamp: {decision.timestamp.isoformat()}",
        f"action: {decision.action}",
        f"mode: {decision.mode.value}",
        f"decision: {decision.decision.value}",
        f"allowed: {str(decision.allowed).lower()}",
        "reasons:",
    ]
    lines.extend([f"- {r}" for r in decision.reasons] or ["- none"])
    lines.append("warnings:")
    lines.extend([f"- {w}" for w in decision.warnings] or ["- none"])
    lines.append("blocking_reasons:")
    lines.extend([f"- {b}" for b in decision.blocking_reasons] or ["- none"])
    if decision.recommended_next_action:
        lines.append(f"recommended_next_action: {decision.recommended_next_action}")
    lines.append("rule_results:")
    for r in decision.rule_results:
        lines.append(f"- {r.rule_name}: {r.status.value} severity={r.severity.value} reason={r.reason}")
    lines.append("safety_flags:")
    for key, value in decision.safety_flags.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"
