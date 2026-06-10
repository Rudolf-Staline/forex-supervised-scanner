"""Autonomous Recovery Planner for blocked readiness/evidence states.

The planner is intentionally diagnostic-only. It reads local JSON reports,
classifies blockers, and can optionally execute a tiny allow-list of dry-run or
read-only report builders. It never enables live trading, never mutates `.env`,
and never submits broker orders.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config.settings import PROJECT_ROOT

DEFAULT_AUTONOMOUS_RECOVERY_REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_AUTONOMOUS_RECOVERY_JSON_REPORT = "autonomous_recovery_plan.json"
DEFAULT_AUTONOMOUS_RECOVERY_TXT_REPORT = "autonomous_recovery_plan.txt"
DEFAULT_MAX_REPORT_AGE_MINUTES = 1440


class AutonomousRecoverySeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    BLOCKING = "BLOCKING"
    CRITICAL = "CRITICAL"


class AutonomousRecoveryCauseType(StrEnum):
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"
    SESSION_HEALTH_BLOCKED = "SESSION_HEALTH_BLOCKED"
    RISK_LIMIT_BLOCKED = "RISK_LIMIT_BLOCKED"
    OPERATOR_MAINTENANCE_MODE = "OPERATOR_MAINTENANCE_MODE"
    OPERATOR_DEGRADED_MODE = "OPERATOR_DEGRADED_MODE"
    FAILURE_DIAGNOSTICS_BLOCKED = "FAILURE_DIAGNOSTICS_BLOCKED"
    SIGNAL_ANOMALIES_BLOCKED = "SIGNAL_ANOMALIES_BLOCKED"
    SYMBOL_MAPPING_BLOCKED = "SYMBOL_MAPPING_BLOCKED"
    SUPERVISOR_ZERO_ORDER_STREAK = "SUPERVISOR_ZERO_ORDER_STREAK"
    SUPERVISOR_FAILURE_STREAK = "SUPERVISOR_FAILURE_STREAK"
    SAFETY_MODE_BLOCKED = "SAFETY_MODE_BLOCKED"
    UNKNOWN_BLOCKER = "UNKNOWN_BLOCKER"


class AutonomousRecoveryActionType(StrEnum):
    REBUILD_EVIDENCE_DRY_RUN = "REBUILD_EVIDENCE_DRY_RUN"
    REBUILD_EVIDENCE_READ_ONLY = "REBUILD_EVIDENCE_READ_ONLY"
    RUN_READINESS_ONLY = "RUN_READINESS_ONLY"
    RUN_FAILURE_DIAGNOSTICS = "RUN_FAILURE_DIAGNOSTICS"
    RUN_DATA_HEALTH_REPORT = "RUN_DATA_HEALTH_REPORT"
    RUN_SESSION_HEALTH_REPORT = "RUN_SESSION_HEALTH_REPORT"
    RUN_SIGNAL_ANOMALY_DETECTOR = "RUN_SIGNAL_ANOMALY_DETECTOR"
    RUN_STATIC_SYMBOL_MAPPING_AUDIT = "RUN_STATIC_SYMBOL_MAPPING_AUDIT"
    REVIEW_OPERATOR_CONTROLS = "REVIEW_OPERATOR_CONTROLS"
    REVIEW_RISK_LIMITS = "REVIEW_RISK_LIMITS"
    REVIEW_STALE_REPORTS = "REVIEW_STALE_REPORTS"
    KEEP_SUPERVISOR_BLOCKED = "KEEP_SUPERVISOR_BLOCKED"


class AutonomousRecoveryExecutionMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    READ_ONLY = "READ_ONLY"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class AutonomousRecoveryFinalStatus(StrEnum):
    NO_RECOVERY_NEEDED = "NO_RECOVERY_NEEDED"
    RECOVERY_RECOMMENDED = "RECOVERY_RECOMMENDED"
    RECOVERY_BLOCKING = "RECOVERY_BLOCKING"
    RECOVERY_EXECUTED = "RECOVERY_EXECUTED"
    RECOVERY_PARTIAL = "RECOVERY_PARTIAL"


class AutonomousRecoveryExecutionStatus(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class AutonomousRecoveryConfig(BaseModel):
    reports_dir: Path = DEFAULT_AUTONOMOUS_RECOVERY_REPORTS_DIR
    max_report_age_minutes: int = Field(default=DEFAULT_MAX_REPORT_AGE_MINUTES, ge=1)
    max_actions: int | None = Field(default=None, ge=1)
    include_manual_actions: bool = True
    execute_safe_actions: bool = False
    dry_run: bool = False
    fail_fast: bool = False


class AutonomousRecoveryCause(BaseModel):
    cause_type: AutonomousRecoveryCauseType
    source_report: str
    severity: AutonomousRecoverySeverity
    reason: str
    evidence_path: str | None = None
    suggested_action_ids: list[AutonomousRecoveryActionType] = Field(default_factory=list)


class AutonomousRecoveryAction(BaseModel):
    action_id: AutonomousRecoveryActionType
    title: str
    description: str
    safe_to_execute_automatically: bool
    execution_mode: AutonomousRecoveryExecutionMode
    command_suggestion: list[str] | None = None
    expected_output_files: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    related_causes: list[AutonomousRecoveryCauseType] = Field(default_factory=list)
    execution_status: AutonomousRecoveryExecutionStatus = AutonomousRecoveryExecutionStatus.NOT_REQUESTED
    execution_returncode: int | None = None
    execution_output: str | None = None


class AutonomousRecoveryPlan(BaseModel):
    generated_at: datetime
    final_status: AutonomousRecoveryFinalStatus
    causes: list[AutonomousRecoveryCause] = Field(default_factory=list)
    actions: list[AutonomousRecoveryAction] = Field(default_factory=list)
    safe_actions: list[str] = Field(default_factory=list)
    manual_actions: list[str] = Field(default_factory=list)
    executed_actions: list[str] = Field(default_factory=list)
    skipped_actions: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    safety_flags: dict[str, object] = Field(default_factory=dict)
    next_recommended_command: str | None = None


CommandRunner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class AutonomousRecoveryPlannerService:
    """Classify readiness/evidence blockers and propose bounded safe recovery."""

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self.command_runner = command_runner or _run_command

    def build_plan(self, config: AutonomousRecoveryConfig | None = None) -> AutonomousRecoveryPlan:
        selected = config or AutonomousRecoveryConfig()
        reports_dir = selected.reports_dir
        now = datetime.now(timezone.utc)
        reports = {name: _read_report(reports_dir / name) for name in REPORT_NAMES}
        causes: list[AutonomousRecoveryCause] = []

        for required in REQUIRED_REPORTS:
            state = reports[required]
            if state["missing"]:
                causes.append(_cause(AutonomousRecoveryCauseType.MISSING_EVIDENCE, required, "BLOCKING", f"required report is missing: {required}", reports_dir / required))

        for name, state in reports.items():
            path = reports_dir / name
            if state["missing"]:
                continue
            age_minutes = (now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds() / 60
            if age_minutes > selected.max_report_age_minutes:
                causes.append(
                    AutonomousRecoveryCause(
                        cause_type=AutonomousRecoveryCauseType.STALE_EVIDENCE,
                        source_report=name,
                        severity=AutonomousRecoverySeverity.BLOCKING,
                        reason=f"report is stale: {name} age_minutes={age_minutes:.1f} max={selected.max_report_age_minutes}",
                        evidence_path=str(path),
                        suggested_action_ids=[AutonomousRecoveryActionType.REVIEW_STALE_REPORTS, AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY],
                    )
                )

        readiness = reports["autonomous_readiness_report.json"]["payload"]
        if isinstance(readiness, dict):
            causes.extend(_causes_from_readiness(readiness, reports_dir / "autonomous_readiness_report.json"))
        evidence = reports["autonomous_evidence_summary.json"]["payload"]
        if isinstance(evidence, dict):
            if str(evidence.get("final_status") or "").upper() == "BLOCKED_EVIDENCE":
                for reason in evidence.get("blocking_failures") or ["evidence builder reported blocking failures"]:
                    causes.append(_cause(AutonomousRecoveryCauseType.MISSING_EVIDENCE, "autonomous_evidence_summary.json", "BLOCKING", str(reason), reports_dir / "autonomous_evidence_summary.json"))
        supervisor = reports["autonomous_supervisor_summary.json"]["payload"]
        if isinstance(supervisor, dict):
            causes.extend(_causes_from_supervisor(supervisor, reports_dir / "autonomous_supervisor_summary.json"))
        causes.extend(_causes_from_aux_reports(reports, reports_dir))

        causes = _dedupe_causes(causes)
        action_ids: list[AutonomousRecoveryActionType] = []
        for cause_item in causes:
            action_ids.extend(cause_item.suggested_action_ids)
        if causes:
            action_ids.append(AutonomousRecoveryActionType.KEEP_SUPERVISOR_BLOCKED)
        actions = [_action(action_id, [c.cause_type for c in causes if action_id in c.suggested_action_ids]) for action_id in _dedupe(action_ids)]
        if not selected.include_manual_actions:
            actions = [action for action in actions if action.execution_mode != AutonomousRecoveryExecutionMode.MANUAL_REVIEW]
        if selected.max_actions is not None:
            actions = actions[: selected.max_actions]

        safe = [action.action_id.value for action in actions if action.safe_to_execute_automatically]
        manual = [action.action_id.value for action in actions if not action.safe_to_execute_automatically]
        blocking = [cause_item.reason for cause_item in causes if cause_item.severity in {AutonomousRecoverySeverity.BLOCKING, AutonomousRecoverySeverity.CRITICAL}]
        status = AutonomousRecoveryFinalStatus.NO_RECOVERY_NEEDED if not causes else (AutonomousRecoveryFinalStatus.RECOVERY_BLOCKING if blocking else AutonomousRecoveryFinalStatus.RECOVERY_RECOMMENDED)
        return AutonomousRecoveryPlan(
            generated_at=now,
            final_status=status,
            causes=causes,
            actions=actions,
            safe_actions=safe,
            manual_actions=manual,
            blocking_reasons=blocking,
            safety_flags=_safety_flags(selected.execute_safe_actions, selected.dry_run),
            next_recommended_command=_next_command(actions),
        )

    def execute_plan(self, plan: AutonomousRecoveryPlan, config: AutonomousRecoveryConfig | None = None) -> AutonomousRecoveryPlan:
        selected = config or AutonomousRecoveryConfig()
        actions: list[AutonomousRecoveryAction] = []
        executed: list[str] = []
        skipped: list[str] = []
        for action in plan.actions:
            updated = action.model_copy(deep=True)
            if not selected.execute_safe_actions or not action.safe_to_execute_automatically or action.command_suggestion is None:
                updated.execution_status = AutonomousRecoveryExecutionStatus.SKIPPED
                skipped.append(action.action_id.value)
                actions.append(updated)
                continue
            if selected.dry_run:
                updated.execution_status = AutonomousRecoveryExecutionStatus.EXECUTED
                updated.execution_returncode = 0
                updated.execution_output = "dry-run: command not executed"
                executed.append(action.action_id.value)
                actions.append(updated)
                continue
            completed = self.command_runner(action.command_suggestion, PROJECT_ROOT)
            updated.execution_returncode = completed.returncode
            updated.execution_output = (completed.stdout + completed.stderr)[-4000:]
            if completed.returncode == 0:
                updated.execution_status = AutonomousRecoveryExecutionStatus.EXECUTED
                executed.append(action.action_id.value)
            else:
                updated.execution_status = AutonomousRecoveryExecutionStatus.FAILED
                skipped.append(action.action_id.value)
                if selected.fail_fast:
                    actions.append(updated)
                    break
            actions.append(updated)
        status = AutonomousRecoveryFinalStatus.RECOVERY_EXECUTED if executed and not skipped else (AutonomousRecoveryFinalStatus.RECOVERY_PARTIAL if executed else plan.final_status)
        return plan.model_copy(update={"actions": actions, "executed_actions": executed, "skipped_actions": skipped, "final_status": status, "safety_flags": _safety_flags(True, selected.dry_run)})


REPORT_NAMES = [
    "autonomous_evidence_summary.json",
    "autonomous_readiness_report.json",
    "autonomous_supervisor_summary.json",
    "session_health_summary.json",
    "data_health_report.json",
    "data_health_summary.json",
    "failure_diagnostics_summary.json",
    "signal_anomaly_summary.json",
    "signal_anomalies_summary.json",
    "mt5_symbol_mapping_audit.json",
]
REQUIRED_REPORTS = ["autonomous_evidence_summary.json", "autonomous_readiness_report.json"]


def build_recovery_plan(config: AutonomousRecoveryConfig | None = None) -> AutonomousRecoveryPlan:
    return AutonomousRecoveryPlannerService().build_plan(config)


def execute_recovery_plan(plan: AutonomousRecoveryPlan, config: AutonomousRecoveryConfig | None = None) -> AutonomousRecoveryPlan:
    return AutonomousRecoveryPlannerService().execute_plan(plan, config)


def export_autonomous_recovery_json(plan: AutonomousRecoveryPlan, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_RECOVERY_JSON_REPORT
    path.write_text(json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_autonomous_recovery_txt(plan: AutonomousRecoveryPlan, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_RECOVERY_TXT_REPORT
    path.write_text(format_autonomous_recovery_txt(plan), encoding="utf-8")
    return path


def format_autonomous_recovery_txt(plan: AutonomousRecoveryPlan) -> str:
    lines = [
        "Autonomous Recovery Plan",
        "This plan is paper/demo/read-only and does not bypass readiness gates.",
        f"generated_at: {plan.generated_at.isoformat()}",
        f"final_status: {plan.final_status.value}",
        f"next_recommended_command: {plan.next_recommended_command or '-'}",
        "causes:",
    ]
    lines.extend([f"- {c.cause_type.value} source={c.source_report} severity={c.severity.value} reason={c.reason}" for c in plan.causes] or ["- none"])
    lines.append("actions:")
    for action in plan.actions:
        command = " ".join(action.command_suggestion or []) or "manual review"
        lines.append(f"- {action.action_id.value}: mode={action.execution_mode.value} safe={str(action.safe_to_execute_automatically).lower()} command={command}")
    lines.append("safety_flags:")
    lines.extend([f"- {key}: {value}" for key, value in plan.safety_flags.items()])
    return "\n".join(lines) + "\n"


def _read_report(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"missing": True, "payload": None}
    try:
        return {"missing": False, "payload": json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError) as exc:
        return {"missing": False, "payload": {"_invalid_json": str(exc)}}


def _cause(cause_type: AutonomousRecoveryCauseType, source: str, severity: str, reason: str, path: Path) -> AutonomousRecoveryCause:
    return AutonomousRecoveryCause(
        cause_type=cause_type,
        source_report=source,
        severity=AutonomousRecoverySeverity(severity),
        reason=reason,
        evidence_path=str(path),
        suggested_action_ids=_suggestions(cause_type),
    )


def _causes_from_readiness(payload: dict[str, Any], path: Path) -> list[AutonomousRecoveryCause]:
    status = str(payload.get("final_status") or "").upper()
    reasons = [str(r) for r in payload.get("blocking_reasons") or payload.get("warning_reasons") or [status or "unknown readiness blocker"]]
    controls = payload.get("operator_controls") if isinstance(payload.get("operator_controls"), dict) else {}
    causes: list[AutonomousRecoveryCause] = []
    if controls.get("maintenance_mode") or any("maintenance" in r.lower() for r in reasons):
        causes.append(_cause(AutonomousRecoveryCauseType.OPERATOR_MAINTENANCE_MODE, path.name, "CRITICAL", "operator maintenance mode is active", path))
    if controls.get("degraded_mode") or any("degraded" in r.lower() for r in reasons):
        causes.append(_cause(AutonomousRecoveryCauseType.OPERATOR_DEGRADED_MODE, path.name, "BLOCKING", "operator degraded mode is active", path))
    mapping = {
        "BLOCKED_BY_SAFETY": AutonomousRecoveryCauseType.SAFETY_MODE_BLOCKED,
        "BLOCKED_BY_DATA_QUALITY": AutonomousRecoveryCauseType.DATA_QUALITY_BLOCKED,
        "BLOCKED_BY_SESSION_HEALTH": AutonomousRecoveryCauseType.SESSION_HEALTH_BLOCKED,
        "BLOCKED_BY_RISK": AutonomousRecoveryCauseType.RISK_LIMIT_BLOCKED,
        "BLOCKED_BY_STALE_REPORTS": AutonomousRecoveryCauseType.STALE_EVIDENCE,
        "BLOCKED_BY_NO_EVIDENCE": AutonomousRecoveryCauseType.MISSING_EVIDENCE,
    }
    if status in mapping:
        for reason in reasons:
            causes.append(_cause(mapping[status], path.name, "BLOCKING", reason, path))
    for check in payload.get("checks") or []:
        if not isinstance(check, dict) or str(check.get("status") or "").upper() not in {"FAIL", "WARN"}:
            continue
        name = str(check.get("name") or "")
        reason = str(check.get("reason") or name)
        ctype = {
            "session_health": AutonomousRecoveryCauseType.SESSION_HEALTH_BLOCKED,
            "data_health": AutonomousRecoveryCauseType.DATA_QUALITY_BLOCKED,
            "failure_diagnostics": AutonomousRecoveryCauseType.FAILURE_DIAGNOSTICS_BLOCKED,
            "signal_anomaly": AutonomousRecoveryCauseType.SIGNAL_ANOMALIES_BLOCKED,
            "mt5_symbol_mapping_audit": AutonomousRecoveryCauseType.SYMBOL_MAPPING_BLOCKED,
        }.get(name)
        if ctype:
            causes.append(_cause(ctype, path.name, "BLOCKING", reason, path))
    return causes


def _causes_from_supervisor(payload: dict[str, Any], path: Path) -> list[AutonomousRecoveryCause]:
    status = str(payload.get("final_status") or "").upper()
    reason = str(payload.get("stop_reason") or status or "recent supervisor report blocked")
    if status == "STOPPED_BY_FAILURES":
        return [_cause(AutonomousRecoveryCauseType.SUPERVISOR_FAILURE_STREAK, path.name, "BLOCKING", reason, path)]
    if status == "STOPPED_BY_RISK" or "zero" in reason.lower():
        return [_cause(AutonomousRecoveryCauseType.SUPERVISOR_ZERO_ORDER_STREAK, path.name, "WARN", reason, path)]
    if status == "BLOCKED_BY_SAFETY":
        return [_cause(AutonomousRecoveryCauseType.SAFETY_MODE_BLOCKED, path.name, "CRITICAL", reason, path)]
    return []


def _causes_from_aux_reports(reports: dict[str, dict[str, object]], reports_dir: Path) -> list[AutonomousRecoveryCause]:
    causes: list[AutonomousRecoveryCause] = []
    for name in ("data_health_report.json", "data_health_summary.json"):
        payload = reports[name]["payload"]
        if isinstance(payload, dict) and str(payload.get("data_quality_status") or "").upper() in {"BLOCKED", "DEGRADED"}:
            causes.append(_cause(AutonomousRecoveryCauseType.DATA_QUALITY_BLOCKED, name, "BLOCKING", f"data health status is {payload.get('data_quality_status')}", reports_dir / name))
    payload = reports["failure_diagnostics_summary.json"]["payload"]
    if isinstance(payload, dict) and str(payload.get("severity") or "").upper() not in {"", "CLEAN", "WARN"}:
        causes.append(_cause(AutonomousRecoveryCauseType.FAILURE_DIAGNOSTICS_BLOCKED, "failure_diagnostics_summary.json", "BLOCKING", f"failure diagnostics severity is {payload.get('severity')}", reports_dir / "failure_diagnostics_summary.json"))
    payload = reports["signal_anomaly_summary.json"]["payload"]
    if isinstance(payload, dict) and (int(payload.get("high_severity_anomalies") or 0) > 0 or str(payload.get("data_integrity_status") or "").upper() in {"DEGRADED", "BLOCKED"}):
        causes.append(_cause(AutonomousRecoveryCauseType.SIGNAL_ANOMALIES_BLOCKED, "signal_anomaly_summary.json", "BLOCKING", "signal anomalies exceed readiness limits", reports_dir / "signal_anomaly_summary.json"))
    payload = reports["mt5_symbol_mapping_audit.json"]["payload"]
    if isinstance(payload, dict) and str(payload.get("mapping_status") or "OK").upper() not in {"OK", "CLEAN", "HEALTHY", "WARN", "UNKNOWN"}:
        causes.append(_cause(AutonomousRecoveryCauseType.SYMBOL_MAPPING_BLOCKED, "mt5_symbol_mapping_audit.json", "BLOCKING", f"symbol mapping status is {payload.get('mapping_status')}", reports_dir / "mt5_symbol_mapping_audit.json"))
    return causes


def _suggestions(cause_type: AutonomousRecoveryCauseType) -> list[AutonomousRecoveryActionType]:
    table = {
        AutonomousRecoveryCauseType.MISSING_EVIDENCE: [AutonomousRecoveryActionType.REBUILD_EVIDENCE_DRY_RUN, AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY, AutonomousRecoveryActionType.RUN_READINESS_ONLY],
        AutonomousRecoveryCauseType.STALE_EVIDENCE: [AutonomousRecoveryActionType.REVIEW_STALE_REPORTS, AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY, AutonomousRecoveryActionType.RUN_READINESS_ONLY],
        AutonomousRecoveryCauseType.DATA_QUALITY_BLOCKED: [AutonomousRecoveryActionType.RUN_DATA_HEALTH_REPORT, AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY],
        AutonomousRecoveryCauseType.SESSION_HEALTH_BLOCKED: [AutonomousRecoveryActionType.RUN_SESSION_HEALTH_REPORT, AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY],
        AutonomousRecoveryCauseType.RISK_LIMIT_BLOCKED: [AutonomousRecoveryActionType.REVIEW_RISK_LIMITS, AutonomousRecoveryActionType.RUN_READINESS_ONLY],
        AutonomousRecoveryCauseType.OPERATOR_MAINTENANCE_MODE: [AutonomousRecoveryActionType.REVIEW_OPERATOR_CONTROLS],
        AutonomousRecoveryCauseType.OPERATOR_DEGRADED_MODE: [AutonomousRecoveryActionType.REVIEW_OPERATOR_CONTROLS],
        AutonomousRecoveryCauseType.FAILURE_DIAGNOSTICS_BLOCKED: [AutonomousRecoveryActionType.RUN_FAILURE_DIAGNOSTICS, AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY],
        AutonomousRecoveryCauseType.SIGNAL_ANOMALIES_BLOCKED: [AutonomousRecoveryActionType.RUN_SIGNAL_ANOMALY_DETECTOR, AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY],
        AutonomousRecoveryCauseType.SYMBOL_MAPPING_BLOCKED: [AutonomousRecoveryActionType.RUN_STATIC_SYMBOL_MAPPING_AUDIT],
        AutonomousRecoveryCauseType.SUPERVISOR_ZERO_ORDER_STREAK: [AutonomousRecoveryActionType.RUN_READINESS_ONLY, AutonomousRecoveryActionType.REVIEW_RISK_LIMITS],
        AutonomousRecoveryCauseType.SUPERVISOR_FAILURE_STREAK: [AutonomousRecoveryActionType.RUN_FAILURE_DIAGNOSTICS, AutonomousRecoveryActionType.RUN_READINESS_ONLY],
        AutonomousRecoveryCauseType.SAFETY_MODE_BLOCKED: [AutonomousRecoveryActionType.KEEP_SUPERVISOR_BLOCKED, AutonomousRecoveryActionType.REVIEW_OPERATOR_CONTROLS],
        AutonomousRecoveryCauseType.UNKNOWN_BLOCKER: [AutonomousRecoveryActionType.RUN_READINESS_ONLY, AutonomousRecoveryActionType.KEEP_SUPERVISOR_BLOCKED],
    }
    return table[cause_type]


def _action(action_id: AutonomousRecoveryActionType, related: list[AutonomousRecoveryCauseType]) -> AutonomousRecoveryAction:
    py = sys.executable
    specs = {
        AutonomousRecoveryActionType.REBUILD_EVIDENCE_DRY_RUN: ("Rebuild evidence dry-run", "Preview evidence builder tasks without report mutation.", True, "DRY_RUN", [py, "scripts/autonomous_evidence_builder.py", "--mode", "dry-run"], []),
        AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY: ("Rebuild evidence read-only", "Refresh local evidence reports using read-only builders.", True, "READ_ONLY", [py, "scripts/autonomous_evidence_builder.py", "--mode", "read-only", "--export-json", "--export-txt"], ["reports/autonomous_evidence_summary.json"]),
        AutonomousRecoveryActionType.RUN_READINESS_ONLY: ("Run readiness-only report", "Re-evaluate readiness without running supervisor cycles.", True, "READ_ONLY", [py, "scripts/autonomous_readiness_report.py", "--export-json", "--export-txt"], ["reports/autonomous_readiness_report.json"]),
        AutonomousRecoveryActionType.RUN_FAILURE_DIAGNOSTICS: ("Run failure diagnostics", "Regenerate local failure diagnostics summary.", True, "READ_ONLY", [py, "scripts/failure_diagnostics_report.py", "--export-json", "--export-txt"], ["reports/failure_diagnostics_summary.json"]),
        AutonomousRecoveryActionType.RUN_DATA_HEALTH_REPORT: ("Run data health report", "Regenerate local data health diagnostics.", True, "READ_ONLY", [py, "scripts/data_health_report.py", "--export-json", "--export-txt"], ["reports/data_health_report.json"]),
        AutonomousRecoveryActionType.RUN_SESSION_HEALTH_REPORT: ("Run session health report", "Regenerate local session health summary.", True, "READ_ONLY", [py, "scripts/session_health_summary.py", "--export-json"], ["reports/session_health_summary.json"]),
        AutonomousRecoveryActionType.RUN_SIGNAL_ANOMALY_DETECTOR: ("Run signal anomaly detector", "Regenerate local signal anomaly summary.", True, "READ_ONLY", [py, "scripts/signal_anomaly_detector.py", "--export-json"], ["reports/signal_anomaly_summary.json"]),
        AutonomousRecoveryActionType.RUN_STATIC_SYMBOL_MAPPING_AUDIT: ("Run static symbol mapping audit", "Audit symbol mappings without requiring a terminal.", True, "READ_ONLY", [py, "scripts/mt5_symbol_mapping_audit.py", "--check-static", "--export-json"], ["reports/mt5_symbol_mapping_audit.json"]),
        AutonomousRecoveryActionType.REVIEW_OPERATOR_CONTROLS: ("Review operator controls", "Manually clear maintenance/degraded mode only after verifying operator intent.", False, "MANUAL_REVIEW", None, []),
        AutonomousRecoveryActionType.REVIEW_RISK_LIMITS: ("Review risk limits", "Manually inspect paper risk state; do not loosen safety gates automatically.", False, "MANUAL_REVIEW", None, []),
        AutonomousRecoveryActionType.REVIEW_STALE_REPORTS: ("Review stale reports", "Manually confirm whether stale artifacts should be refreshed.", False, "MANUAL_REVIEW", None, []),
        AutonomousRecoveryActionType.KEEP_SUPERVISOR_BLOCKED: ("Keep supervisor blocked", "Do not run supervisor cycles until readiness passes again.", False, "MANUAL_REVIEW", None, []),
    }
    title, desc, safe, mode, command, outputs = specs[action_id]
    return AutonomousRecoveryAction(action_id=action_id, title=title, description=desc, safe_to_execute_automatically=safe, execution_mode=AutonomousRecoveryExecutionMode(mode), command_suggestion=command, expected_output_files=outputs, prerequisites=["paper/demo safety lock remains enabled"], related_causes=_dedupe(related))


def _dedupe(values: list[Any]) -> list[Any]:
    seen = set()
    out = []
    for value in values:
        key = str(value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _dedupe_causes(causes: list[AutonomousRecoveryCause]) -> list[AutonomousRecoveryCause]:
    seen = set()
    out = []
    for cause_item in causes:
        key = (cause_item.cause_type, cause_item.source_report, cause_item.reason)
        if key not in seen:
            seen.add(key)
            out.append(cause_item)
    return out


def _next_command(actions: list[AutonomousRecoveryAction]) -> str | None:
    for action in actions:
        if action.safe_to_execute_automatically and action.command_suggestion:
            return " ".join(action.command_suggestion)
    return None


def _safety_flags(execute_requested: bool, dry_run: bool) -> dict[str, object]:
    return {
        "paper_demo_only": True,
        "live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_order_execution_allowed": False,
        "mt5_terminal_required": False,
        "environment_mutation_allowed": False,
        "report_deletion_allowed": False,
        "strategy_threshold_mutation_allowed": False,
        "safety_gate_bypass_allowed": False,
        "safe_execution_requested": execute_requested,
        "dry_run": dry_run,
    }


def _run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
