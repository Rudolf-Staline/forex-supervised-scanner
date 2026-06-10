"""Autonomous readiness gate for paper/demo supervisor runs.

The gate is read-only: it inspects local settings, operator controls, paper risk,
and existing report artifacts. It never calls MT5, never submits orders, and never
mutates environment files.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config.safety import DemoSafetyError, demo_safety_status, ensure_demo_bot_safe_mode
from app.config.settings import AppSettings, PROJECT_ROOT
from app.execution.autonomous_policy import (
    AutonomousPolicyConfig,
    AutonomousPolicyContext,
    AutonomousPolicyEngine,
    AutonomousPolicyMode,
)
from app.risk.daily_limits import DailyRiskConfig, summarize_daily_risk
from app.storage.database import Database

DEFAULT_AUTONOMOUS_READINESS_MAX_REPORT_AGE_MINUTES = 1440
DEFAULT_AUTONOMOUS_READINESS_REQUIRE_SESSION_HEALTH = True
DEFAULT_AUTONOMOUS_READINESS_REQUIRE_DATA_HEALTH = True
DEFAULT_AUTONOMOUS_READINESS_REQUIRE_FAILURE_DIAGNOSTICS = True
DEFAULT_AUTONOMOUS_READINESS_MIN_DATA_QUALITY = 70
DEFAULT_AUTONOMOUS_READINESS_ALLOW_WARN_READY_FOR_DRY_RUN = True
DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_ANOMALIES = True
DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_MAINTENANCE = True
DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_DEGRADED = True
DEFAULT_AUTONOMOUS_READINESS_REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_AUTONOMOUS_READINESS_JSON_REPORT = "autonomous_readiness_report.json"
DEFAULT_AUTONOMOUS_READINESS_TXT_REPORT = "autonomous_readiness_report.txt"


class AutonomousReadinessFinalStatus(StrEnum):
    READY = "READY"
    WARN_READY = "WARN_READY"
    BLOCKED_BY_SAFETY = "BLOCKED_BY_SAFETY"
    BLOCKED_BY_OPERATOR_CONTROL = "BLOCKED_BY_OPERATOR_CONTROL"
    BLOCKED_BY_DATA_QUALITY = "BLOCKED_BY_DATA_QUALITY"
    BLOCKED_BY_SESSION_HEALTH = "BLOCKED_BY_SESSION_HEALTH"
    BLOCKED_BY_RISK = "BLOCKED_BY_RISK"
    BLOCKED_BY_STALE_REPORTS = "BLOCKED_BY_STALE_REPORTS"
    BLOCKED_BY_NO_EVIDENCE = "BLOCKED_BY_NO_EVIDENCE"


BLOCKING_STATUSES = {
    AutonomousReadinessFinalStatus.BLOCKED_BY_SAFETY,
    AutonomousReadinessFinalStatus.BLOCKED_BY_OPERATOR_CONTROL,
    AutonomousReadinessFinalStatus.BLOCKED_BY_DATA_QUALITY,
    AutonomousReadinessFinalStatus.BLOCKED_BY_SESSION_HEALTH,
    AutonomousReadinessFinalStatus.BLOCKED_BY_RISK,
    AutonomousReadinessFinalStatus.BLOCKED_BY_STALE_REPORTS,
    AutonomousReadinessFinalStatus.BLOCKED_BY_NO_EVIDENCE,
}


class AutonomousReadinessCheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class AutonomousReadinessConfig(BaseModel):
    reports_dir: Path = DEFAULT_AUTONOMOUS_READINESS_REPORTS_DIR
    dry_run: bool = True
    max_report_age_minutes: int = Field(default=DEFAULT_AUTONOMOUS_READINESS_MAX_REPORT_AGE_MINUTES, ge=1)
    require_session_health: bool = DEFAULT_AUTONOMOUS_READINESS_REQUIRE_SESSION_HEALTH
    require_data_health: bool = DEFAULT_AUTONOMOUS_READINESS_REQUIRE_DATA_HEALTH
    require_failure_diagnostics: bool = DEFAULT_AUTONOMOUS_READINESS_REQUIRE_FAILURE_DIAGNOSTICS
    min_data_quality: int = Field(default=DEFAULT_AUTONOMOUS_READINESS_MIN_DATA_QUALITY, ge=0, le=100)
    allow_warn_ready_for_dry_run: bool = DEFAULT_AUTONOMOUS_READINESS_ALLOW_WARN_READY_FOR_DRY_RUN
    block_on_anomalies: bool = DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_ANOMALIES
    block_on_maintenance: bool = DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_MAINTENANCE
    block_on_degraded: bool = DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_DEGRADED

    @classmethod
    def from_environment(cls, **overrides: object) -> "AutonomousReadinessConfig":
        values: dict[str, object] = {
            "max_report_age_minutes": _env_int("AUTONOMOUS_READINESS_MAX_REPORT_AGE_MINUTES", DEFAULT_AUTONOMOUS_READINESS_MAX_REPORT_AGE_MINUTES),
            "require_session_health": _env_bool("AUTONOMOUS_READINESS_REQUIRE_SESSION_HEALTH", DEFAULT_AUTONOMOUS_READINESS_REQUIRE_SESSION_HEALTH),
            "require_data_health": _env_bool("AUTONOMOUS_READINESS_REQUIRE_DATA_HEALTH", DEFAULT_AUTONOMOUS_READINESS_REQUIRE_DATA_HEALTH),
            "require_failure_diagnostics": _env_bool("AUTONOMOUS_READINESS_REQUIRE_FAILURE_DIAGNOSTICS", DEFAULT_AUTONOMOUS_READINESS_REQUIRE_FAILURE_DIAGNOSTICS),
            "min_data_quality": _env_int("AUTONOMOUS_READINESS_MIN_DATA_QUALITY", DEFAULT_AUTONOMOUS_READINESS_MIN_DATA_QUALITY),
            "allow_warn_ready_for_dry_run": _env_bool("AUTONOMOUS_READINESS_ALLOW_WARN_READY_FOR_DRY_RUN", DEFAULT_AUTONOMOUS_READINESS_ALLOW_WARN_READY_FOR_DRY_RUN),
            "block_on_anomalies": _env_bool("AUTONOMOUS_READINESS_BLOCK_ON_ANOMALIES", DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_ANOMALIES),
            "block_on_maintenance": _env_bool("AUTONOMOUS_READINESS_BLOCK_ON_MAINTENANCE", DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_MAINTENANCE),
            "block_on_degraded": _env_bool("AUTONOMOUS_READINESS_BLOCK_ON_DEGRADED", DEFAULT_AUTONOMOUS_READINESS_BLOCK_ON_DEGRADED),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls.model_validate(values)


class AutonomousReadinessCheck(BaseModel):
    name: str
    status: AutonomousReadinessCheckStatus
    severity: str
    reason: str
    evidence: str | None = None


class AutonomousReadinessReport(BaseModel):
    generated_at: datetime
    final_status: AutonomousReadinessFinalStatus
    ready: bool
    dry_run_allowed: bool
    paper_run_allowed: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    warning_reasons: list[str] = Field(default_factory=list)
    checks: list[AutonomousReadinessCheck] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)
    stale_reports: list[str] = Field(default_factory=list)
    missing_reports: list[str] = Field(default_factory=list)
    operator_controls: dict[str, Any] = Field(default_factory=dict)
    risk_snapshot: dict[str, Any] = Field(default_factory=dict)
    policy_decision: dict[str, Any] | None = None
    safety_flags: dict[str, Any] = Field(default_factory=dict)


class AutonomousReadinessError(RuntimeError):
    def __init__(self, report: AutonomousReadinessReport) -> None:
        self.report = report
        super().__init__("; ".join(report.blocking_reasons) or report.final_status.value)


class AutonomousReadinessService:
    def __init__(self, settings: AppSettings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def build_report(self, config: AutonomousReadinessConfig | None = None) -> AutonomousReadinessReport:
        selected = config or AutonomousReadinessConfig.from_environment()
        generated_at = datetime.now(timezone.utc)
        checks: list[AutonomousReadinessCheck] = []
        blocking: list[str] = []
        warnings: list[str] = []
        missing: list[str] = []
        stale: list[str] = []
        evidence: list[str] = []

        safety_flags = demo_safety_status(self.settings) | {
            "paper_demo_only": True,
            "live_execution_allowed": False,
            "broker_order_submission_allowed": False,
            "mt5_called": False,
            "orders_sent": False,
        }
        try:
            ensure_demo_bot_safe_mode(self.settings, context="autonomous readiness gate")
            checks.append(_check("central_safety_mode", "PASS", "critical", "central demo/paper safety lock passed"))
        except DemoSafetyError as exc:
            reason = str(exc)
            blocking.append(reason)
            checks.append(_check("central_safety_mode", "FAIL", "critical", reason))

        controls = self.database.load_operator_controls()
        operator_controls = controls.model_dump(mode="json")
        op_reasons: list[str] = []
        if selected.block_on_maintenance and controls.maintenance_mode:
            op_reasons.append("operator maintenance mode is active")
        if selected.block_on_degraded and controls.degraded_mode:
            op_reasons.append("operator degraded mode is active")
        if op_reasons:
            blocking.extend(op_reasons)
            checks.append(_check("operator_controls", "FAIL", "critical", "; ".join(op_reasons)))
        else:
            checks.append(_check("operator_controls", "PASS", "critical", "maintenance/degraded operator controls are clear"))

        risk_summary = summarize_daily_risk(
            self.database.load_paper_orders(),
            now=generated_at,
            config=DailyRiskConfig.from_env(),
        )
        risk_snapshot = risk_summary.model_dump(mode="json")
        if str(risk_summary.bot_risk_status).lower() != "ok":
            reason = f"daily paper risk status is {risk_summary.bot_risk_status}"
            blocking.append(reason)
            checks.append(_check("daily_risk", "FAIL", "critical", reason))
        else:
            checks.append(_check("daily_risk", "PASS", "critical", "daily paper risk limits are not exhausted"))

        reports = _report_specs(selected)
        found_required = 0
        for name, spec in reports.items():
            path = selected.reports_dir / spec["filename"]
            required = bool(spec["required"])
            if not path.exists():
                if required:
                    missing.append(path.name)
                    status = "WARN" if selected.dry_run and selected.allow_warn_ready_for_dry_run else "FAIL"
                    reason = f"required evidence report is missing: {path.name}"
                    (warnings if status == "WARN" else blocking).append(reason)
                    checks.append(_check(name, status, spec["severity"], reason, path))
                else:
                    checks.append(_check(name, "SKIP", spec["severity"], f"optional evidence report not present: {path.name}", path))
                continue

            evidence.append(str(path))
            if required:
                found_required += 1
            age_minutes = (generated_at - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds() / 60
            if age_minutes > selected.max_report_age_minutes:
                stale.append(path.name)
                reason = f"evidence report is stale: {path.name} age_minutes={age_minutes:.1f} max={selected.max_report_age_minutes}"
                blocking.append(reason)
                checks.append(_check(name, "FAIL", spec["severity"], reason, path))
                continue

            payload = _load_json(path)
            status, reason = _evaluate_report_payload(name, payload, selected)
            if status == "FAIL":
                blocking.append(reason)
            elif status == "WARN":
                warnings.append(reason)
            checks.append(_check(name, status, spec["severity"], reason, path))

        if found_required == 0:
            reason = "no local readiness evidence files were found under reports/"
            if selected.dry_run and selected.allow_warn_ready_for_dry_run:
                warnings.append(reason)
                checks.append(_check("local_evidence", "WARN", "critical", reason))
            else:
                blocking.append(reason)
                checks.append(_check("local_evidence", "FAIL", "critical", reason))
        else:
            checks.append(_check("local_evidence", "PASS", "critical", f"found {found_required} required evidence report(s)"))

        final_status = _final_status(blocking, warnings, checks)
        dry_run_allowed = final_status == AutonomousReadinessFinalStatus.READY or (
            final_status == AutonomousReadinessFinalStatus.WARN_READY and selected.allow_warn_ready_for_dry_run
        )
        paper_run_allowed = final_status == AutonomousReadinessFinalStatus.READY

        # --- Policy engine check ---
        _readiness_mode = AutonomousPolicyMode.DRY_RUN if selected.dry_run else AutonomousPolicyMode.READ_ONLY
        policy_engine = AutonomousPolicyEngine(AutonomousPolicyConfig(
            mode=_readiness_mode,
            dry_run=selected.dry_run,
            readiness_status=final_status.value,
        ))
        policy_ctx = AutonomousPolicyContext(
            mode=_readiness_mode,
            dry_run=selected.dry_run,
            readiness_status=final_status.value,
        )
        policy_decision_result = policy_engine.can_run_readiness(policy_ctx)

        return AutonomousReadinessReport(
            generated_at=generated_at,
            final_status=final_status,
            ready=final_status == AutonomousReadinessFinalStatus.READY,
            dry_run_allowed=dry_run_allowed,
            paper_run_allowed=paper_run_allowed,
            blocking_reasons=sorted(set(blocking)),
            warning_reasons=sorted(set(warnings)),
            checks=checks,
            evidence_files=sorted(set(evidence)),
            stale_reports=sorted(set(stale)),
            missing_reports=sorted(set(missing)),
            operator_controls=operator_controls,
            risk_snapshot=risk_snapshot,
            policy_decision=policy_decision_result.model_dump(mode="json"),
            safety_flags=safety_flags,
        )

    def assert_ready_or_block(self, config: AutonomousReadinessConfig | None = None) -> AutonomousReadinessReport:
        report = self.build_report(config)
        if report.final_status in BLOCKING_STATUSES:
            raise AutonomousReadinessError(report)
        return report


def build_readiness_report(settings: AppSettings, database: Database, config: AutonomousReadinessConfig | None = None) -> AutonomousReadinessReport:
    return AutonomousReadinessService(settings, database).build_report(config)


def assert_ready_or_block(settings: AppSettings, database: Database, config: AutonomousReadinessConfig | None = None) -> AutonomousReadinessReport:
    return AutonomousReadinessService(settings, database).assert_ready_or_block(config)


def export_autonomous_readiness_json(report: AutonomousReadinessReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_READINESS_JSON_REPORT
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_autonomous_readiness_txt(report: AutonomousReadinessReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_READINESS_TXT_REPORT
    path.write_text(format_autonomous_readiness_txt(report), encoding="utf-8")
    return path


def format_autonomous_readiness_txt(report: AutonomousReadinessReport) -> str:
    lines = [
        "Autonomous Readiness Report",
        f"generated_at: {report.generated_at.isoformat()}",
        f"final_status: {report.final_status.value}",
        f"ready: {str(report.ready).lower()}",
        f"dry_run_allowed: {str(report.dry_run_allowed).lower()}",
        f"paper_run_allowed: {str(report.paper_run_allowed).lower()}",
        "blocking_reasons:",
    ]
    lines.extend([f"- {reason}" for reason in report.blocking_reasons] or ["- none"])
    lines.append("warning_reasons:")
    lines.extend([f"- {reason}" for reason in report.warning_reasons] or ["- none"])
    lines.append("checks:")
    for check in report.checks:
        lines.append(f"- {check.name}: {check.status.value} severity={check.severity} reason={check.reason}")
    return "\n".join(lines) + "\n"


def _report_specs(config: AutonomousReadinessConfig) -> dict[str, dict[str, object]]:
    return {
        "session_health": {"filename": "session_health_summary.json", "required": config.require_session_health, "severity": "critical"},
        "data_health": {"filename": "data_health_report.json", "required": config.require_data_health, "severity": "critical"},
        "failure_diagnostics": {"filename": "failure_diagnostics_summary.json", "required": config.require_failure_diagnostics, "severity": "critical"},
        "signal_anomaly": {"filename": "signal_anomaly_summary.json", "required": False, "severity": "high"},
        "autonomous_supervisor": {"filename": "autonomous_supervisor_summary.json", "required": False, "severity": "medium"},
        "mt5_symbol_mapping_audit": {"filename": "mt5_symbol_mapping_audit.json", "required": False, "severity": "medium"},
    }


def _evaluate_report_payload(name: str, payload: Any, config: AutonomousReadinessConfig) -> tuple[str, str]:
    if payload is None:
        return "FAIL", f"{name} report is not valid JSON"
    if not isinstance(payload, dict):
        return "FAIL", f"{name} report has unexpected schema"
    if name == "session_health":
        status = str(payload.get("overall_status") or "BLOCKED").upper()
        if status in {"HEALTHY", "WARN"}:
            return ("PASS" if status == "HEALTHY" else "WARN"), f"session health status is {status}"
        return "FAIL", f"session health status is {status}"
    if name == "data_health":
        status = str(payload.get("data_quality_status") or "BLOCKED").upper()
        score = _data_quality_score(payload, status)
        if status == "HEALTHY" and score >= config.min_data_quality:
            return "PASS", f"data health status is {status} score={score}"
        if status == "WARN" and score >= config.min_data_quality:
            return "WARN", f"data health status is {status} score={score}"
        return "FAIL", f"data health status is {status} score={score} min={config.min_data_quality}"
    if name == "failure_diagnostics":
        severity = str(payload.get("severity") or "BLOCKED").upper()
        if severity == "CLEAN":
            return "PASS", "failure diagnostics severity is CLEAN"
        if severity == "WARN":
            return "WARN", "failure diagnostics severity is WARN"
        return "FAIL", f"failure diagnostics severity is {severity}"
    if name == "signal_anomaly":
        integrity = str(payload.get("data_integrity_status") or "BLOCKED").upper()
        high = int(payload.get("high_severity_anomalies") or 0)
        if config.block_on_anomalies and (high > 0 or integrity in {"DEGRADED", "BLOCKED"}):
            return "FAIL", f"signal anomalies block readiness: integrity={integrity} high={high}"
        if integrity == "WARN":
            return "WARN", "signal anomaly integrity is WARN"
        return "PASS", f"signal anomaly integrity is {integrity}"
    if name == "autonomous_supervisor":
        status = str(payload.get("final_status") or "UNKNOWN").upper()
        if status in {"BLOCKED_BY_SAFETY", "STOPPED_BY_FAILURES"}:
            return "FAIL", f"recent autonomous supervisor report ended with {status}"
        if status.startswith("STOPPED"):
            return "WARN", f"recent autonomous supervisor report ended with {status}"
        return "PASS", f"recent autonomous supervisor report status is {status}"
    if name == "mt5_symbol_mapping_audit":
        status = str(payload.get("mapping_status") or "UNKNOWN").upper()
        if status in {"OK", "CLEAN", "HEALTHY"}:
            return "PASS", f"MT5 symbol mapping audit status is {status}"
        if status in {"WARN", "UNKNOWN"}:
            return "WARN", f"MT5 symbol mapping audit status is {status}"
        return "FAIL", f"MT5 symbol mapping audit status is {status}"
    return "PASS", f"{name} report was readable"


def _data_quality_score(payload: dict[str, Any], status: str) -> int:
    if "data_quality_score" in payload:
        try:
            return int(payload["data_quality_score"])
        except (TypeError, ValueError):
            return 0
    base = {"HEALTHY": 100, "WARN": 75, "DEGRADED": 50, "BLOCKED": 0}.get(status, 0)
    penalties = 5 * len(payload.get("files_stale") or []) + 10 * len(payload.get("invalid_json_lines") or [])
    return max(base - penalties, 0)


def _final_status(blocking: list[str], warnings: list[str], checks: list[AutonomousReadinessCheck]) -> AutonomousReadinessFinalStatus:
    if not blocking:
        return AutonomousReadinessFinalStatus.WARN_READY if warnings else AutonomousReadinessFinalStatus.READY
    failed = {check.name: check.reason.lower() for check in checks if check.status == AutonomousReadinessCheckStatus.FAIL}
    if "central_safety_mode" in failed:
        return AutonomousReadinessFinalStatus.BLOCKED_BY_SAFETY
    if "operator_controls" in failed:
        return AutonomousReadinessFinalStatus.BLOCKED_BY_OPERATOR_CONTROL
    if "daily_risk" in failed:
        return AutonomousReadinessFinalStatus.BLOCKED_BY_RISK
    if any("stale" in check.reason.lower() for check in checks if check.status == AutonomousReadinessCheckStatus.FAIL):
        return AutonomousReadinessFinalStatus.BLOCKED_BY_STALE_REPORTS
    if any("missing" in check.reason.lower() or "no local" in check.reason.lower() for check in checks if check.status == AutonomousReadinessCheckStatus.FAIL):
        return AutonomousReadinessFinalStatus.BLOCKED_BY_NO_EVIDENCE
    if any(check.name == "session_health" and check.status == AutonomousReadinessCheckStatus.FAIL for check in checks):
        return AutonomousReadinessFinalStatus.BLOCKED_BY_SESSION_HEALTH
    if any(check.name in {"data_health", "signal_anomaly"} and check.status == AutonomousReadinessCheckStatus.FAIL for check in checks):
        return AutonomousReadinessFinalStatus.BLOCKED_BY_DATA_QUALITY
    return AutonomousReadinessFinalStatus.BLOCKED_BY_SAFETY


def _check(name: str, status: str, severity: str, reason: str, evidence: Path | None = None) -> AutonomousReadinessCheck:
    return AutonomousReadinessCheck(name=name, status=AutonomousReadinessCheckStatus(status), severity=severity, reason=reason, evidence=str(evidence) if evidence else None)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else int(raw)
