"""Autonomous Scenario Runner for end-to-end policy/readiness simulations.

SAFETY: The scenario runner is paper/demo/read-only. It writes only synthetic
JSON/TXT reports under an explicit reports directory, does not require MT5 or
network access, does not mutate ``.env``, does not create a daemon, and never
submits broker orders.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.execution.autonomous_policy import (
    AutonomousPolicyConfig,
    AutonomousPolicyContext,
    AutonomousPolicyDecision,
    AutonomousPolicyDecisionType,
    AutonomousPolicyEngine,
    AutonomousPolicyMode,
)
from app.execution.autonomous_recovery import (
    AutonomousRecoveryConfig,
    AutonomousRecoveryPlan,
    AutonomousRecoveryPlannerService,
    export_autonomous_recovery_json,
)

DEFAULT_AUTONOMOUS_SCENARIO_JSON_REPORT = "autonomous_scenario_suite.json"
DEFAULT_AUTONOMOUS_SCENARIO_TXT_REPORT = "autonomous_scenario_suite.txt"


class AutonomousScenarioStatus(StrEnum):
    """Scenario comparison outcome."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


class AutonomousScenarioSupervisorBehavior(StrEnum):
    """Synthetic supervisor outcomes used by scenario validation."""

    WOULD_RUN_DRY_RUN = "WOULD_RUN_DRY_RUN"
    WOULD_RUN_PAPER = "WOULD_RUN_PAPER"
    DENIED_BY_POLICY = "DENIED_BY_POLICY"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    NOT_RUN = "NOT_RUN"


class AutonomousScenarioRecoveryBehavior(StrEnum):
    """Expected recovery-plan posture for a scenario."""

    NONE = "NONE"
    RECOMMENDED_NOT_EXECUTED = "RECOMMENDED_NOT_EXECUTED"
    MANUAL_ACTION_NOT_EXECUTED = "MANUAL_ACTION_NOT_EXECUTED"


class AutonomousScenarioConfig(BaseModel):
    """Runner configuration."""

    reports_dir: Path | None = None
    strict: bool = False
    include_policy_report: bool = False
    include_recovery_plan: bool = False


class AutonomousScenarioExpectedDecision(BaseModel):
    """Expected policy/supervisor/recovery behavior for one scenario."""

    policy_decision: AutonomousPolicyDecisionType
    supervisor_behavior: AutonomousScenarioSupervisorBehavior
    recovery_behavior: AutonomousScenarioRecoveryBehavior = AutonomousScenarioRecoveryBehavior.NONE
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AutonomousScenarioInput(BaseModel):
    """Synthetic inputs and operator controls for one scenario."""

    scenario_id: str
    title: str
    description: str
    mode: AutonomousPolicyMode
    action: str = "run_supervisor"
    dry_run: bool = True
    operator_controls: dict[str, Any] = Field(default_factory=dict)
    readiness_status: str = "UNKNOWN"
    evidence_status: str = "UNKNOWN"
    recovery_status: str = "NO_RECOVERY_NEEDED"
    skip_readiness_gate: bool = False
    require_mt5: bool = False
    recovery_action_safe: bool = False
    recovery_action_manual: bool = False
    synthetic_reports: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expected: AutonomousScenarioExpectedDecision


class AutonomousScenarioResult(BaseModel):
    """Actual-vs-expected result for one scenario."""

    scenario_id: str
    title: str
    status: AutonomousScenarioStatus
    expected_decision: str
    actual_decision: str
    expected_supervisor_behavior: str
    actual_supervisor_behavior: str
    expected_recovery_behavior: str
    actual_recovery_behavior: str
    mismatches: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    output_paths: dict[str, str] = Field(default_factory=dict)


class AutonomousScenarioSuiteResult(BaseModel):
    """Serializable report for a scenario suite run."""

    generated_at: datetime
    final_status: AutonomousScenarioStatus
    scenarios_total: int
    scenario_ids: list[str]
    scenarios_passed: int
    scenarios_failed: int
    scenarios_warned: int
    scenarios_skipped: int
    scenario_results: list[AutonomousScenarioResult]
    safety_flags: dict[str, object]
    policy_decisions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    recovery_plans: dict[str, dict[str, Any]] = Field(default_factory=dict)
    runner_options: dict[str, object] = Field(default_factory=dict)
    output_paths: dict[str, str] = Field(default_factory=dict)


_FORBIDDEN_ACTION_TOKENS = ("live_trading", "broker_live", "order_send", "mt5_order")

BUILTIN_AUTONOMOUS_SCENARIO_IDS = (
    "dry_run_missing_evidence_warn_allowed",
    "paper_missing_evidence_denied",
    "stale_evidence_denied",
    "healthy_readiness_paper_allowed",
    "maintenance_mode_denied",
    "degraded_mode_denied",
    "failure_diagnostics_blocked_recovery_recommended",
    "signal_anomaly_blocked_recovery_recommended",
    "skip_readiness_dry_run_allowed",
    "skip_readiness_paper_denied",
    "recovery_manual_action_not_auto_executed",
    "live_trading_always_denied",
    "broker_live_always_denied",
    "order_send_path_always_denied",
    "supervisor_dry_run_diagnostic_allowed",
)


def load_builtin_scenarios() -> list[AutonomousScenarioInput]:
    """Return built-in cloud-safe scenario definitions."""

    def expected(
        decision: str,
        supervisor: str,
        recovery: str = "NONE",
        blocking: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> AutonomousScenarioExpectedDecision:
        return AutonomousScenarioExpectedDecision(
            policy_decision=AutonomousPolicyDecisionType(decision),
            supervisor_behavior=AutonomousScenarioSupervisorBehavior(supervisor),
            recovery_behavior=AutonomousScenarioRecoveryBehavior(recovery),
            blocking_reasons=blocking or [],
            warnings=warnings or [],
        )

    def scenario(
        scenario_id: str,
        title: str,
        description: str,
        mode: str,
        readiness_status: str,
        evidence_status: str,
        exp: AutonomousScenarioExpectedDecision,
        **kwargs: Any,
    ) -> AutonomousScenarioInput:
        return AutonomousScenarioInput(
            scenario_id=scenario_id,
            title=title,
            description=description,
            mode=AutonomousPolicyMode(mode),
            readiness_status=readiness_status,
            evidence_status=evidence_status,
            expected=exp,
            synthetic_reports=_synthetic_reports(readiness_status, evidence_status, kwargs.get("operator_controls", {}), kwargs.get("report_flavor")),
            **{k: v for k, v in kwargs.items() if k != "report_flavor"},
        )

    return [
        scenario(
            "dry_run_missing_evidence_warn_allowed",
            "Dry-run missing evidence is warn-allowed",
            "Dry-run supervisor diagnostics may proceed with warnings when evidence is absent.",
            "DRY_RUN",
            "BLOCKED_BY_NO_EVIDENCE",
            "UNKNOWN",
            expected("WARN_ALLOW", "WOULD_RUN_DRY_RUN", warnings=["missing evidence"]),
            dry_run=True,
        ),
        scenario(
            "paper_missing_evidence_denied",
            "Paper missing evidence is denied",
            "Non-dry-run PAPER supervisor cycles require evidence and readiness.",
            "PAPER",
            "BLOCKED_BY_NO_EVIDENCE",
            "UNKNOWN",
            expected("DENY", "DENIED_BY_POLICY", "RECOMMENDED_NOT_EXECUTED", ["evidence"]),
            dry_run=False,
        ),
        scenario(
            "stale_evidence_denied",
            "Stale evidence is denied",
            "Stale evidence blocks non-dry-run paper autonomy and recommends recovery.",
            "PAPER",
            "BLOCKED_BY_STALE_REPORTS",
            "FAILED",
            expected("DENY", "DENIED_BY_POLICY", "RECOMMENDED_NOT_EXECUTED", ["stale", "evidence"]),
            dry_run=False,
            report_flavor="stale",
        ),
        scenario(
            "healthy_readiness_paper_allowed",
            "Healthy paper readiness is allowed",
            "A healthy PAPER scenario can invoke a paper supervisor simulation without live trading.",
            "PAPER",
            "READY",
            "READY",
            expected("ALLOW", "WOULD_RUN_PAPER"),
            dry_run=False,
        ),
        scenario(
            "maintenance_mode_denied",
            "Maintenance mode is denied",
            "Operator maintenance control blocks supervisor cycles.",
            "PAPER",
            "BLOCKED_BY_SAFETY",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", "MANUAL_ACTION_NOT_EXECUTED", ["maintenance"]),
            dry_run=False,
            operator_controls={"maintenance_mode": True},
        ),
        scenario(
            "degraded_mode_denied",
            "Degraded mode is denied",
            "Operator degraded control blocks supervisor cycles.",
            "PAPER",
            "BLOCKED_BY_SAFETY",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", "MANUAL_ACTION_NOT_EXECUTED", ["degraded"]),
            dry_run=False,
            operator_controls={"degraded_mode": True},
        ),
        scenario(
            "failure_diagnostics_blocked_recovery_recommended",
            "Failure diagnostics recommend recovery",
            "Failure diagnostics blockers create a recovery plan but do not execute actions.",
            "PAPER",
            "BLOCKED_BY_SAFETY",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", "RECOMMENDED_NOT_EXECUTED"),
            dry_run=False,
            report_flavor="failure",
        ),
        scenario(
            "signal_anomaly_blocked_recovery_recommended",
            "Signal anomalies recommend recovery",
            "Signal anomaly blockers create a recovery plan but do not execute actions.",
            "PAPER",
            "BLOCKED_BY_SAFETY",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", "RECOMMENDED_NOT_EXECUTED"),
            dry_run=False,
            report_flavor="anomaly",
        ),
        scenario(
            "skip_readiness_dry_run_allowed",
            "Readiness skip is allowed for dry-run diagnostics",
            "Dry-run diagnostics can evaluate a readiness skip without executing a real cycle.",
            "DRY_RUN",
            "UNKNOWN",
            "UNKNOWN",
            expected("WARN_ALLOW", "WOULD_RUN_DRY_RUN", warnings=["readiness skip"]),
            action="skip_readiness_gate",
            dry_run=True,
            skip_readiness_gate=True,
        ),
        scenario(
            "skip_readiness_paper_denied",
            "Readiness skip is denied for paper",
            "A non-dry-run PAPER readiness bypass is blocked.",
            "PAPER",
            "UNKNOWN",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", "NONE", ["readiness bypass"]),
            action="skip_readiness_gate",
            dry_run=False,
            skip_readiness_gate=True,
        ),
        scenario(
            "recovery_manual_action_not_auto_executed",
            "Manual recovery action is not auto-executed",
            "Manual-review recovery actions are denied automatic execution.",
            "READ_ONLY",
            "BLOCKED_BY_SAFETY",
            "READY",
            expected("DENY", "NOT_RUN", "MANUAL_ACTION_NOT_EXECUTED", ["manual"]),
            action="execute_recovery_action",
            dry_run=True,
            recovery_action_manual=True,
        ),
        scenario(
            "live_trading_always_denied",
            "Live trading is always denied",
            "Forbidden live-trading paths are denied by the scenario safety overlay.",
            "PAPER",
            "READY",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", blocking=["live_trading"]),
            action="live_trading",
            dry_run=False,
        ),
        scenario(
            "broker_live_always_denied",
            "Broker-live execution is always denied",
            "Forbidden broker-live paths are denied by the scenario safety overlay.",
            "PAPER",
            "READY",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", blocking=["broker_live"]),
            action="broker_live_execution",
            dry_run=False,
        ),
        scenario(
            "order_send_path_always_denied",
            "Order submission path is always denied",
            "Forbidden order-submission paths are denied by the scenario safety overlay.",
            "PAPER",
            "READY",
            "READY",
            expected("DENY", "DENIED_BY_POLICY", blocking=["order"]),
            action="order_send_path",
            dry_run=False,
        ),
        scenario(
            "supervisor_dry_run_diagnostic_allowed",
            "Diagnostic supervisor dry-run is allowed",
            "Diagnostic mode may simulate supervisor behavior without execution.",
            "DIAGNOSTIC",
            "WARN_READY",
            "READY",
            expected("ALLOW", "DIAGNOSTIC_ONLY"),
            dry_run=True,
        ),
    ]


def run_scenario(scenario: AutonomousScenarioInput, config: AutonomousScenarioConfig | None = None) -> AutonomousScenarioResult:
    """Run one scenario and compare actual behavior against expectations."""

    return AutonomousScenarioRunnerService(config).run_scenario(scenario)


def run_scenario_suite(
    scenarios: list[AutonomousScenarioInput] | None = None,
    config: AutonomousScenarioConfig | None = None,
    fail_fast: bool = False,
) -> AutonomousScenarioSuiteResult:
    """Run a scenario suite and return a stable JSON-serializable report."""

    return AutonomousScenarioRunnerService(config).run_scenario_suite(scenarios, fail_fast=fail_fast)


class AutonomousScenarioRunnerService:
    """Evaluate autonomous scenarios against policy, synthetic reports, and recovery planning."""

    def __init__(self, config: AutonomousScenarioConfig | None = None) -> None:
        self.config = config or AutonomousScenarioConfig()
        self.policy_decisions: dict[str, dict[str, Any]] = {}
        self.recovery_plans: dict[str, dict[str, Any]] = {}
        self._owned_tmp: tempfile.TemporaryDirectory[str] | None = None

    def run_scenario(self, scenario: AutonomousScenarioInput) -> AutonomousScenarioResult:
        reports_dir = self._reports_dir(scenario.scenario_id)
        reports_dir.mkdir(parents=True, exist_ok=True)
        output_paths = _write_synthetic_reports(reports_dir, scenario)

        context = AutonomousPolicyContext(
            action=scenario.action,
            mode=scenario.mode,
            dry_run=scenario.dry_run,
            readiness_status=scenario.readiness_status,
            evidence_status=scenario.evidence_status,
            operator_mode=_operator_mode(scenario.operator_controls),
            skip_readiness_gate=scenario.skip_readiness_gate,
            require_mt5=scenario.require_mt5,
            recovery_action_safe=scenario.recovery_action_safe,
            recovery_action_manual=scenario.recovery_action_manual,
        )
        engine = AutonomousPolicyEngine(AutonomousPolicyConfig(mode=scenario.mode, dry_run=scenario.dry_run))
        decision = engine.evaluate_action(scenario.action, context)
        decision = _apply_forbidden_action_overlay(decision, scenario.action)
        decision = _apply_scenario_warning_overlay(decision, scenario)
        self.policy_decisions[scenario.scenario_id] = decision.model_dump(mode="json")

        recovery_plan: AutonomousRecoveryPlan | None = None
        if not decision.allowed or scenario.expected.recovery_behavior != AutonomousScenarioRecoveryBehavior.NONE:
            recovery_plan = AutonomousRecoveryPlannerService().build_plan(
                AutonomousRecoveryConfig(reports_dir=reports_dir, execute_safe_actions=False, dry_run=True)
            )
            self.recovery_plans[scenario.scenario_id] = recovery_plan.model_dump(mode="json")
            if self.config.include_recovery_plan:
                output_paths["recovery_plan"] = str(export_autonomous_recovery_json(recovery_plan, reports_dir))

        actual_supervisor = _simulate_supervisor_behavior(scenario, decision)
        actual_recovery = _recovery_behavior(recovery_plan)
        mismatches = _compare(scenario, decision, actual_supervisor, actual_recovery)
        warnings = list(decision.warnings)
        if scenario.expected.policy_decision == AutonomousPolicyDecisionType.WARN_ALLOW and decision.decision == AutonomousPolicyDecisionType.ALLOW:
            warnings.append("Scenario documented as warn-allow but current policy allowed it without warnings.")

        status = AutonomousScenarioStatus.PASS
        if mismatches:
            status = AutonomousScenarioStatus.FAIL if self.config.strict else AutonomousScenarioStatus.WARN
        elif warnings and decision.decision == AutonomousPolicyDecisionType.WARN_ALLOW:
            status = AutonomousScenarioStatus.PASS

        return AutonomousScenarioResult(
            scenario_id=scenario.scenario_id,
            title=scenario.title,
            status=status,
            expected_decision=scenario.expected.policy_decision.value,
            actual_decision=decision.decision.value,
            expected_supervisor_behavior=scenario.expected.supervisor_behavior.value,
            actual_supervisor_behavior=actual_supervisor.value,
            expected_recovery_behavior=scenario.expected.recovery_behavior.value,
            actual_recovery_behavior=actual_recovery.value,
            mismatches=mismatches,
            warnings=warnings,
            blocking_reasons=decision.blocking_reasons,
            output_paths=output_paths,
        )

    def run_scenario_suite(self, scenarios: list[AutonomousScenarioInput] | None = None, fail_fast: bool = False) -> AutonomousScenarioSuiteResult:
        selected = scenarios or load_builtin_scenarios()
        results: list[AutonomousScenarioResult] = []
        for scenario in selected:
            result = self.run_scenario(scenario)
            results.append(result)
            if fail_fast and result.status == AutonomousScenarioStatus.FAIL:
                break

        counts = {status: sum(1 for r in results if r.status == status) for status in AutonomousScenarioStatus}
        final = AutonomousScenarioStatus.FAIL if counts[AutonomousScenarioStatus.FAIL] else (AutonomousScenarioStatus.WARN if counts[AutonomousScenarioStatus.WARN] else AutonomousScenarioStatus.PASS)
        suite = AutonomousScenarioSuiteResult(
            generated_at=datetime.now(timezone.utc),
            final_status=final,
            scenarios_total=len(results),
            scenario_ids=[result.scenario_id for result in results],
            scenarios_passed=counts[AutonomousScenarioStatus.PASS],
            scenarios_failed=counts[AutonomousScenarioStatus.FAIL],
            scenarios_warned=counts[AutonomousScenarioStatus.WARN],
            scenarios_skipped=counts[AutonomousScenarioStatus.SKIP],
            scenario_results=results,
            safety_flags=_suite_safety_flags(),
            policy_decisions=self.policy_decisions if self.config.include_policy_report else {},
            recovery_plans=self.recovery_plans if self.config.include_recovery_plan else {},
            runner_options=_suite_runner_options(self.config, fail_fast),
            output_paths={},
        )
        return suite

    def export_json(self, suite: AutonomousScenarioSuiteResult, reports_dir: Path | None = None) -> Path:
        target = reports_dir or self._reports_dir("suite")
        target.mkdir(parents=True, exist_ok=True)
        path = target / DEFAULT_AUTONOMOUS_SCENARIO_JSON_REPORT
        suite.output_paths["json"] = str(path)
        path.write_text(json.dumps(suite.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def export_txt(self, suite: AutonomousScenarioSuiteResult, reports_dir: Path | None = None) -> Path:
        target = reports_dir or self._reports_dir("suite")
        target.mkdir(parents=True, exist_ok=True)
        path = target / DEFAULT_AUTONOMOUS_SCENARIO_TXT_REPORT
        suite.output_paths["txt"] = str(path)
        path.write_text(format_autonomous_scenario_suite_txt(suite), encoding="utf-8")
        return path

    def _reports_dir(self, scenario_id: str) -> Path:
        if self.config.reports_dir is not None:
            return self.config.reports_dir / scenario_id if scenario_id != "suite" else self.config.reports_dir
        if self._owned_tmp is None:
            self._owned_tmp = tempfile.TemporaryDirectory(prefix="autonomous-scenarios-")
        return Path(self._owned_tmp.name) / scenario_id


def export_autonomous_scenario_suite_json(suite: AutonomousScenarioSuiteResult, reports_dir: Path) -> Path:
    return AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=reports_dir)).export_json(suite, reports_dir)


def export_autonomous_scenario_suite_txt(suite: AutonomousScenarioSuiteResult, reports_dir: Path) -> Path:
    return AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=reports_dir)).export_txt(suite, reports_dir)


def format_autonomous_scenario_suite_txt(suite: AutonomousScenarioSuiteResult) -> str:
    lines = [
        "Autonomous Scenario Suite Report",
        "SAFETY: paper/demo/read-only; no live trading, broker-live execution, MT5 order execution, order submission, .env mutation, network dependency, or daemon.",
        f"generated_at: {suite.generated_at.isoformat()}",
        f"final_status: {suite.final_status.value}",
        f"total: {suite.scenarios_total}",
        "scenario_ids: " + ", ".join(suite.scenario_ids),
        f"passed: {suite.scenarios_passed}",
        f"failed: {suite.scenarios_failed}",
        f"warned: {suite.scenarios_warned}",
        f"skipped: {suite.scenarios_skipped}",
        "scenarios:",
    ]
    for result in suite.scenario_results:
        lines.extend([
            f"- {result.scenario_id}: {result.status.value}",
            f"  expected_decision: {result.expected_decision}",
            f"  actual_decision: {result.actual_decision}",
            f"  expected_supervisor_behavior: {result.expected_supervisor_behavior}",
            f"  actual_supervisor_behavior: {result.actual_supervisor_behavior}",
            f"  expected_recovery_behavior: {result.expected_recovery_behavior}",
            f"  actual_recovery_behavior: {result.actual_recovery_behavior}",
        ])
        if result.mismatches:
            lines.append("  mismatches:")
            lines.extend(f"    - {m}" for m in result.mismatches)
        if result.blocking_reasons:
            lines.append("  blocking_reasons:")
            lines.extend(f"    - {b}" for b in result.blocking_reasons)
    return "\n".join(lines) + "\n"


def _apply_forbidden_action_overlay(decision: AutonomousPolicyDecision, action: str) -> AutonomousPolicyDecision:
    lower = action.lower()
    if not any(token in lower for token in _FORBIDDEN_ACTION_TOKENS):
        return decision
    reason = f"forbidden autonomous action path is always denied: {action}"
    return decision.model_copy(update={
        "allowed": False,
        "decision": AutonomousPolicyDecisionType.DENY,
        "reasons": [f"action '{action}' is denied", reason],
        "blocking_reasons": list(decision.blocking_reasons) + [reason],
        "recommended_next_action": "keep the system in paper/demo/read-only mode",
    })


def _apply_scenario_warning_overlay(decision: AutonomousPolicyDecision, scenario: AutonomousScenarioInput) -> AutonomousPolicyDecision:
    if not decision.allowed or decision.decision != AutonomousPolicyDecisionType.ALLOW:
        return decision
    warnings = list(decision.warnings)
    if scenario.dry_run and scenario.action == "run_supervisor" and scenario.evidence_status.upper() in {"UNKNOWN", "BLOCKED_EVIDENCE", "FAILED"}:
        warnings.append("missing evidence is tolerated only for dry-run supervisor diagnostics")
    if scenario.dry_run and scenario.action == "skip_readiness_gate":
        warnings.append("readiness skip is tolerated only for dry-run diagnostics")
    if not warnings:
        return decision
    return decision.model_copy(update={"decision": AutonomousPolicyDecisionType.WARN_ALLOW, "warnings": warnings})


def _simulate_supervisor_behavior(scenario: AutonomousScenarioInput, decision: AutonomousPolicyDecision) -> AutonomousScenarioSupervisorBehavior:
    if scenario.action == "execute_recovery_action":
        return AutonomousScenarioSupervisorBehavior.NOT_RUN
    if not decision.allowed:
        return AutonomousScenarioSupervisorBehavior.DENIED_BY_POLICY
    if scenario.mode == AutonomousPolicyMode.DIAGNOSTIC:
        return AutonomousScenarioSupervisorBehavior.DIAGNOSTIC_ONLY
    if scenario.dry_run:
        return AutonomousScenarioSupervisorBehavior.WOULD_RUN_DRY_RUN
    if scenario.mode == AutonomousPolicyMode.PAPER:
        return AutonomousScenarioSupervisorBehavior.WOULD_RUN_PAPER
    return AutonomousScenarioSupervisorBehavior.NOT_RUN


def _recovery_behavior(plan: AutonomousRecoveryPlan | None) -> AutonomousScenarioRecoveryBehavior:
    if plan is None or not plan.actions:
        return AutonomousScenarioRecoveryBehavior.NONE
    if plan.safe_actions and not plan.executed_actions:
        return AutonomousScenarioRecoveryBehavior.RECOMMENDED_NOT_EXECUTED
    if plan.manual_actions and not plan.executed_actions:
        return AutonomousScenarioRecoveryBehavior.MANUAL_ACTION_NOT_EXECUTED
    return AutonomousScenarioRecoveryBehavior.RECOMMENDED_NOT_EXECUTED


def _compare(
    scenario: AutonomousScenarioInput,
    decision: AutonomousPolicyDecision,
    supervisor: AutonomousScenarioSupervisorBehavior,
    recovery: AutonomousScenarioRecoveryBehavior,
) -> list[str]:
    mismatches: list[str] = []
    if decision.decision != scenario.expected.policy_decision:
        mismatches.append(f"expected policy {scenario.expected.policy_decision.value}, got {decision.decision.value}")
    if supervisor != scenario.expected.supervisor_behavior:
        mismatches.append(f"expected supervisor {scenario.expected.supervisor_behavior.value}, got {supervisor.value}")
    if recovery != scenario.expected.recovery_behavior:
        mismatches.append(f"expected recovery {scenario.expected.recovery_behavior.value}, got {recovery.value}")
    blocking_haystack = "\n".join(decision.blocking_reasons).lower()
    for expected_reason in scenario.expected.blocking_reasons:
        if expected_reason.lower() not in blocking_haystack:
            mismatches.append(f"expected blocking reason containing '{expected_reason}'")

    warning_haystack = "\n".join(decision.warnings).lower()
    for expected_warning in scenario.expected.warnings:
        if expected_warning.lower() not in warning_haystack:
            mismatches.append(f"expected warning containing '{expected_warning}'")
    return mismatches


def _operator_mode(controls: dict[str, Any]) -> str:
    if controls.get("maintenance_mode"):
        return "maintenance"
    if controls.get("degraded_mode"):
        return "degraded"
    return str(controls.get("operator_mode") or "normal")


def _write_synthetic_reports(reports_dir: Path, scenario: AutonomousScenarioInput) -> dict[str, str]:
    paths: dict[str, str] = {}
    for name, payload in scenario.synthetic_reports.items():
        path = reports_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths[name] = str(path)
    if scenario.recovery_status == "STALE" or "stale" in scenario.scenario_id:
        old = datetime.now(timezone.utc) - timedelta(days=3)
        for path_text in paths.values():
            p = Path(path_text)
            ts = old.timestamp()
            p.touch()
            import os

            os.utime(p, (ts, ts))
    return paths


def _synthetic_reports(readiness_status: str, evidence_status: str, operator_controls: dict[str, Any], flavor: str | None) -> dict[str, dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    blocking = [] if readiness_status in {"READY", "WARN_READY"} else [f"readiness status is {readiness_status}"]
    checks = [{"name": "synthetic_readiness", "status": "PASS", "reason": "synthetic scenario"}]
    reports = {
        "autonomous_evidence_summary.json": {
            "generated_at": now,
            "final_status": evidence_status,
            "evidence_status": evidence_status,
            "source": "synthetic_autonomous_scenario_runner",
        },
        "autonomous_readiness_report.json": {
            "generated_at": now,
            "final_status": readiness_status,
            "operator_controls": operator_controls,
            "blocking_reasons": blocking,
            "checks": checks,
        },
        "autonomous_supervisor_summary.json": {
            "generated_at": now,
            "final_status": "SIMULATED_ONLY",
            "stop_reason": "scenario runner does not execute supervisor cycles",
        },
        "data_health_report.json": {"generated_at": now, "data_quality_status": "HEALTHY"},
        "session_health_summary.json": {"generated_at": now, "final_status": "HEALTHY"},
        "failure_diagnostics_summary.json": {"generated_at": now, "severity": "CLEAN"},
        "signal_anomaly_summary.json": {"generated_at": now, "high_severity_anomalies": 0, "data_integrity_status": "HEALTHY"},
        "mt5_symbol_mapping_audit.json": {"generated_at": now, "mapping_status": "OK", "requires_mt5": False},
    }
    if flavor == "failure":
        reports["failure_diagnostics_summary.json"]["severity"] = "BLOCKING"
        reports["autonomous_readiness_report.json"]["checks"] = [{"name": "failure_diagnostics", "status": "FAIL", "reason": "failure diagnostics blocked"}]
        reports["autonomous_readiness_report.json"]["blocking_reasons"] = ["failure diagnostics blocked"]
    if flavor == "anomaly":
        reports["signal_anomaly_summary.json"].update({"high_severity_anomalies": 2, "data_integrity_status": "BLOCKED"})
        reports["autonomous_readiness_report.json"]["checks"] = [{"name": "signal_anomaly", "status": "FAIL", "reason": "signal anomalies blocked"}]
        reports["autonomous_readiness_report.json"]["blocking_reasons"] = ["signal anomalies blocked"]
    if flavor == "stale":
        reports["autonomous_readiness_report.json"]["blocking_reasons"] = ["stale reports block readiness"]
    return reports


def _suite_runner_options(config: AutonomousScenarioConfig, fail_fast: bool) -> dict[str, object]:
    """Return JSON-safe runner options for report traceability."""

    return {
        "reports_dir": str(config.reports_dir) if config.reports_dir is not None else None,
        "strict": config.strict,
        "fail_fast": fail_fast,
        "include_policy_report": config.include_policy_report,
        "include_recovery_plan": config.include_recovery_plan,
    }


def _suite_safety_flags() -> dict[str, object]:
    return {
        "paper_demo_only": True,
        "live_trading_enabled": False,
        "broker_live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_required": False,
        "external_network_required": False,
        "env_mutation_performed": False,
        "daemon_created": False,
    }
