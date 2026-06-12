"""Read-only operator dashboard summarizing local paper/demo report artifacts.

The dashboard never runs trading logic, never imports the MT5 terminal API,
never calls ``order_send``, never mutates ``.env``, and works fully offline
from files in ``reports/``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_OPERATOR_DASHBOARD_JSON = "operator_dashboard_summary.json"
DEFAULT_OPERATOR_DASHBOARD_TXT = "operator_dashboard_report.txt"

STATUS_READY = "OPERATOR_READY_FOR_PAPER_REVIEW"
STATUS_WARN = "OPERATOR_WARN_REVIEW_REQUIRED"
STATUS_BLOCKED = "OPERATOR_BLOCKED"
STATUS_REPORTS_MISSING = "OPERATOR_REPORTS_MISSING"
STATUS_REPORTS_STALE = "OPERATOR_REPORTS_STALE"

REPORT_NOT_AVAILABLE = "NOT_AVAILABLE"

REQUIRED_REPORTS: dict[str, str] = {
    "mt5_validation": "local_mt5_realtime_validation.json",
    "command_center": "realtime_command_center_summary.json",
    "supervisor": "realtime_paper_supervisor_summary.json",
    "position_manager": "realtime_paper_positions.json",
    "heartbeat": "realtime_heartbeat.jsonl",
    "scenarios": "autonomous_scenario_suite.json",
}

OPTIONAL_REPORTS: dict[str, str] = {
    "policy": "autonomous_policy_report.json",
    "readiness": "autonomous_readiness_report.json",
    "evidence": "autonomous_evidence_report.json",
    "recovery": "autonomous_recovery_plan.json",
}

REGENERATE_COMMANDS: dict[str, str] = {
    "mt5_validation": "python scripts/local_mt5_realtime_validation.py --duration-minutes 2 --export-json",
    "command_center": "python scripts/realtime_command_center.py --watchlist forex_majors --max-cycles 1 --export-json",
    "supervisor": "python scripts/realtime_paper_supervisor.py --watchlist forex_majors --max-cycles 1 --export-json",
    "position_manager": "python scripts/realtime_paper_positions.py --watchlist forex_majors --export-json",
    "heartbeat": "python scripts/realtime_paper_supervisor.py --watchlist forex_majors --max-cycles 1 --export-json",
    "scenarios": "python scripts/autonomous_scenario_runner.py --export-json",
}

SAFETY_FLAGS: dict[str, object] = {
    "read_only_dashboard": True,
    "paper_demo_only": True,
    "live_trading_enabled": False,
    "live_execution_allowed": False,
    "broker_live_execution_allowed": False,
    "broker_order_submission_allowed": False,
    "order_send_called": False,
    "env_mutation_performed": False,
    "mt5_required": False,
}

_MT5_READY = "MT5_REALTIME_READY"
_MT5_WARN = "MT5_REALTIME_WARN"

_OK_STATUSES = {
    "command_center": {"COMPLETED"},
    "supervisor": {"COMPLETED_MAX_CYCLES", "COMPLETED_MAX_RUNTIME"},
    "scenarios": {"PASS"},
    "readiness": {"READY"},
    "evidence": {"READY_EVIDENCE"},
    "policy": {"ALLOW"},
    "recovery": {"NO_RECOVERY_NEEDED", "RECOVERY_EXECUTED"},
}
_WARN_STATUSES = {
    "command_center": {"WARN"},
    "supervisor": set(),
    "scenarios": {"WARN", "SKIP"},
    "readiness": {"WARN_READY"},
    "evidence": {"WARN_EVIDENCE", "DRY_RUN_PLAN"},
    "policy": {"WARN_ALLOW"},
    "recovery": {"RECOVERY_RECOMMENDED", "RECOVERY_PARTIAL"},
}

_TIMESTAMP_KEYS = ("completed_at", "generated_at", "heartbeat_at", "timestamp", "started_at")


def build_operator_dashboard(reports_dir: Path, *, now: datetime | None = None, max_age_hours: float = 24.0) -> dict[str, Any]:
    """Aggregate existing report files into a single operator summary."""
    now = now or datetime.now(timezone.utc)
    reports_dir = Path(reports_dir)

    blocking: list[str] = []
    warnings: list[str] = []
    missing_reports: list[str] = []
    stale_reports: list[str] = []
    latest_report_times: dict[str, str | None] = {}
    recommended: list[str] = []
    statuses: dict[str, str] = {}

    payloads: dict[str, Any] = {}
    for name, filename in {**REQUIRED_REPORTS, **OPTIONAL_REPORTS}.items():
        path = reports_dir / filename
        required = name in REQUIRED_REPORTS
        if not path.is_file():
            payloads[name] = None
            latest_report_times[filename] = None
            statuses[name] = REPORT_NOT_AVAILABLE
            if required:
                missing_reports.append(filename)
                warnings.append(f"required report missing: {filename}")
                command = REGENERATE_COMMANDS.get(name)
                if command:
                    recommended.append(f"generate {filename}: {command}")
            continue
        payload = _read_heartbeat(path) if name == "heartbeat" else _read_json(path)
        payloads[name] = payload
        if payload is None:
            statuses[name] = "UNREADABLE"
            if required:
                missing_reports.append(filename)
            warnings.append(f"report could not be parsed: {filename}")
            latest_report_times[filename] = None
            continue
        timestamp = _report_timestamp(payload)
        latest_report_times[filename] = timestamp.isoformat() if timestamp else None
        if timestamp is None:
            warnings.append(f"report has no parseable timestamp: {filename}")
        elif (now - timestamp).total_seconds() > max_age_hours * 3600:
            stale_reports.append(filename)
            warnings.append(f"report is stale (older than {max_age_hours:g}h): {filename}")

    _evaluate_mt5_validation(payloads.get("mt5_validation"), statuses, blocking, warnings)
    _evaluate_status_report("command_center", payloads.get("command_center"), "final_status", statuses, blocking, warnings)
    _evaluate_supervisor(payloads.get("supervisor"), statuses, blocking, warnings)
    _evaluate_position_manager(payloads.get("position_manager"), statuses, blocking, warnings)
    _evaluate_heartbeat(payloads.get("heartbeat"), statuses, blocking, warnings)
    _evaluate_status_report("scenarios", payloads.get("scenarios"), "final_status", statuses, blocking, warnings)
    _evaluate_status_report("readiness", payloads.get("readiness"), "final_status", statuses, blocking, warnings)
    _evaluate_status_report("evidence", payloads.get("evidence"), "final_status", statuses, blocking, warnings)
    _evaluate_status_report("policy", payloads.get("policy"), "decision", statuses, blocking, warnings)
    _evaluate_recovery(payloads.get("recovery"), statuses, blocking, warnings, recommended)

    _check_source_safety_flags(payloads, blocking)

    final_status = _final_status(blocking, missing_reports, stale_reports, warnings)
    recommended.extend(_status_recommendations(final_status, blocking))

    summary: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "reports_dir": str(reports_dir),
        "max_age_hours": max_age_hours,
        "final_operator_status": final_status,
        "mt5_validation_status": statuses["mt5_validation"],
        "command_center_status": statuses["command_center"],
        "supervisor_status": statuses["supervisor"],
        "position_manager_status": statuses["position_manager"],
        "heartbeat_status": statuses["heartbeat"],
        "readiness_status": statuses["readiness"],
        "evidence_status": statuses["evidence"],
        "policy_decision": statuses["policy"],
        "recovery_status": statuses["recovery"],
        "scenario_status": statuses["scenarios"],
        "latest_report_times": latest_report_times,
        "stale_reports": stale_reports,
        "missing_reports": missing_reports,
        "blocking_reasons": _dedupe(blocking),
        "warnings": _dedupe(warnings),
        "safety_flags": dict(SAFETY_FLAGS),
        "recommended_next_actions": _dedupe(recommended),
        "output_paths": {},
    }
    return summary


def export_operator_dashboard_json(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = Path(reports_dir) / DEFAULT_OPERATOR_DASHBOARD_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    summary["output_paths"]["json"] = str(path)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_operator_dashboard_txt(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = Path(reports_dir) / DEFAULT_OPERATOR_DASHBOARD_TXT
    path.parent.mkdir(parents=True, exist_ok=True)
    summary["output_paths"]["txt"] = str(path)
    path.write_text(render_operator_dashboard_txt(summary), encoding="utf-8")
    return path


def render_operator_dashboard_txt(summary: dict[str, Any]) -> str:
    lines = [
        "OPERATOR DASHBOARD (read-only, paper/demo only)",
        f"generated_at={summary['generated_at']}",
        f"reports_dir={summary['reports_dir']}",
        f"final_operator_status={summary['final_operator_status']}",
        "",
        "component statuses:",
        f"  mt5_validation_status={summary['mt5_validation_status']}",
        f"  command_center_status={summary['command_center_status']}",
        f"  supervisor_status={summary['supervisor_status']}",
        f"  position_manager_status={summary['position_manager_status']}",
        f"  heartbeat_status={summary['heartbeat_status']}",
        f"  readiness_status={summary['readiness_status']}",
        f"  evidence_status={summary['evidence_status']}",
        f"  policy_decision={summary['policy_decision']}",
        f"  recovery_status={summary['recovery_status']}",
        f"  scenario_status={summary['scenario_status']}",
        "",
        "latest report times:",
    ]
    for filename, value in sorted(summary["latest_report_times"].items()):
        lines.append(f"  {filename}: {value or 'missing'}")
    for label, key in (("missing reports", "missing_reports"), ("stale reports", "stale_reports"), ("blocking reasons", "blocking_reasons"), ("warnings", "warnings"), ("recommended next actions", "recommended_next_actions")):
        lines.append("")
        lines.append(f"{label}:")
        values = summary[key]
        if values:
            lines.extend(f"  - {value}" for value in values)
        else:
            lines.append("  (none)")
    lines.append("")
    lines.append("safety flags:")
    for name, value in sorted(summary["safety_flags"].items()):
        lines.append(f"  {name}={str(value).lower()}")
    lines.append("")
    return "\n".join(lines)


def _evaluate_mt5_validation(payload: Any, statuses: dict[str, str], blocking: list[str], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        statuses.setdefault("mt5_validation", REPORT_NOT_AVAILABLE)
        return
    status = str(payload.get("final_status", "UNKNOWN"))
    statuses["mt5_validation"] = status
    if status == _MT5_READY:
        return
    if status == _MT5_WARN:
        warnings.append("mt5 validation reported warnings")
        return
    blocking.append(f"mt5 validation blocked: {status}")
    blocking.extend(f"mt5 validation: {reason}" for reason in _str_list(payload.get("blocking_reasons")))


def _evaluate_status_report(name: str, payload: Any, status_key: str, statuses: dict[str, str], blocking: list[str], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        statuses.setdefault(name, REPORT_NOT_AVAILABLE)
        return
    status = str(payload.get(status_key) or "UNKNOWN")
    statuses[name] = status
    _flag_synthetic_fallback(name, payload, blocking)
    if status in _OK_STATUSES.get(name, set()):
        return
    if status in _WARN_STATUSES.get(name, set()) or status == "UNKNOWN":
        warnings.append(f"{name} status requires review: {status}")
        warnings.extend(f"{name}: {reason}" for reason in _str_list(payload.get("warnings") or payload.get("warning_reasons")))
        return
    blocking.append(f"{name} blocked: {status}")
    blocking.extend(f"{name}: {reason}" for reason in _str_list(payload.get("blocking_reasons")))


def _evaluate_supervisor(payload: Any, statuses: dict[str, str], blocking: list[str], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        statuses.setdefault("supervisor", REPORT_NOT_AVAILABLE)
        return
    status = str(payload.get("stop_reason") or "UNKNOWN")
    statuses["supervisor"] = status
    _flag_synthetic_fallback("supervisor", payload, blocking)
    if status in _OK_STATUSES["supervisor"]:
        return
    blocking.append(f"supervisor blocked: {status}")
    blocking.extend(f"supervisor: {reason}" for reason in _str_list(payload.get("blocking_reasons")))


def _evaluate_position_manager(payload: Any, statuses: dict[str, str], blocking: list[str], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        statuses.setdefault("position_manager", REPORT_NOT_AVAILABLE)
        return
    reasons = _str_list(payload.get("blocking_reasons"))
    _flag_synthetic_fallback("position manager", payload, blocking)
    if reasons:
        statuses["position_manager"] = "BLOCKED"
        blocking.extend(f"position manager: {reason}" for reason in reasons)
        return
    position_warnings = _str_list(payload.get("warnings"))
    if position_warnings:
        statuses["position_manager"] = "WARN"
        warnings.extend(f"position manager: {warning}" for warning in position_warnings)
        return
    statuses["position_manager"] = "COMPLETED"


def _evaluate_heartbeat(records: Any, statuses: dict[str, str], blocking: list[str], warnings: list[str]) -> None:
    if not isinstance(records, list):
        statuses.setdefault("heartbeat", REPORT_NOT_AVAILABLE)
        return
    if not records:
        statuses["heartbeat"] = "EMPTY"
        warnings.append("heartbeat file exists but contains no records")
        return
    last = records[-1]
    reasons = _str_list(last.get("blocking_reasons"))
    drift_reasons = [reason for reason in reasons if "drift" in reason.lower()]
    if str(last.get("stop_reason") or "") == "BLOCKED_BY_SAFETY_DRIFT" or drift_reasons:
        statuses["heartbeat"] = "SAFETY_DRIFT"
        blocking.append("heartbeat reports runtime safety drift")
        blocking.extend(f"heartbeat: {reason}" for reason in drift_reasons)
        return
    if last.get("live_execution_allowed") is True:
        statuses["heartbeat"] = "SAFETY_DRIFT"
        blocking.append("heartbeat: live_execution_allowed flag is true; live trading is not authorized")
        return
    if reasons:
        statuses["heartbeat"] = "BLOCKED"
        blocking.extend(f"heartbeat: {reason}" for reason in reasons)
        return
    statuses["heartbeat"] = "HEALTHY"


def _evaluate_recovery(payload: Any, statuses: dict[str, str], blocking: list[str], warnings: list[str], recommended: list[str]) -> None:
    _evaluate_status_report("recovery", payload, "final_status", statuses, blocking, warnings)
    if not isinstance(payload, dict):
        return
    command = payload.get("next_recommended_command")
    if isinstance(command, str) and command.strip():
        recommended.append(f"recovery plan: {command.strip()}")
    recommended.extend(f"recovery safe action: {action}" for action in _str_list(payload.get("safe_actions")))
    recommended.extend(f"recovery manual action: {action}" for action in _str_list(payload.get("manual_actions")))


def _flag_synthetic_fallback(name: str, payload: dict[str, Any], blocking: list[str]) -> None:
    texts = _str_list(payload.get("blocking_reasons")) + [str(payload.get("stop_reason") or "")]
    data_health = payload.get("data_health_report")
    synthetic_used = isinstance(data_health, dict) and data_health.get("synthetic_fallback_used") is True
    if synthetic_used or any("synthetic" in text.lower() for text in texts):
        blocking.append(f"{name}: synthetic fallback detected; not accepted for paper review")


def _check_source_safety_flags(payloads: dict[str, Any], blocking: list[str]) -> None:
    unsafe_flags = ("live_execution_allowed", "live_trading_enabled", "broker_live_execution_allowed", "order_send_called")
    for name, payload in payloads.items():
        if not isinstance(payload, dict):
            continue
        flags = payload.get("safety_flags")
        if not isinstance(flags, dict):
            continue
        for flag in unsafe_flags:
            if flags.get(flag) is True:
                blocking.append(f"{name}: unsafe safety flag {flag}=true; paper/demo only operation violated")


def _final_status(blocking: list[str], missing_reports: list[str], stale_reports: list[str], warnings: list[str]) -> str:
    if blocking:
        return STATUS_BLOCKED
    if missing_reports:
        return STATUS_REPORTS_MISSING
    if stale_reports:
        return STATUS_REPORTS_STALE
    if warnings:
        return STATUS_WARN
    return STATUS_READY


def _status_recommendations(final_status: str, blocking: list[str]) -> list[str]:
    if final_status == STATUS_READY:
        return ["review the TXT report and archive the session artifacts"]
    if final_status == STATUS_WARN:
        return ["review warnings before continuing paper/demo operation"]
    if final_status == STATUS_REPORTS_STALE:
        return ["re-run the stale report generators before the next paper session"]
    if final_status == STATUS_REPORTS_MISSING:
        return ["generate the missing reports listed above (paper/demo commands only)"]
    return ["resolve blocking reasons before any further paper/demo operation", "run python scripts/autonomous_recovery_planner.py --export-json for a bounded recovery plan"]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_heartbeat(path: Path) -> list[dict[str, Any]] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _report_timestamp(payload: Any) -> datetime | None:
    record = payload[-1] if isinstance(payload, list) and payload else payload
    if not isinstance(record, dict):
        return None
    for key in _TIMESTAMP_KEYS:
        parsed = _parse_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
