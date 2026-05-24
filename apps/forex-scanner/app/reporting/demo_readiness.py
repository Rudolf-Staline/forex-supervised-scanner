"""Aggregate existing reports into a conservative demo-readiness summary.

Read-only aggregator: it never calls MT5, never places orders, never mutates
environment variables, and never authorizes execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

INPUT_FILES = {
    "readiness": "readiness_report.json",
    "safety_env": "safety_env_doctor.json",
    "config_profile": "config_profile_validation.json",
    "mt5_readonly": "mt5_readonly_validation.json",
    "data_health": "data_health_report.json",
    "report_index": "report_index.json",
    "local_validation": "local_validation_summary.json",
    "risk_exposure": "risk_exposure_summary.json",
    "symbol_health": "symbol_health_summary.json",
}

FINAL_STATUS_ORDER = ["BLOCKED", "NOT_READY", "PAPER_READY", "MT5_READONLY_READY", "DEMO_PRECHECK_ONLY"]


@dataclass(frozen=True)
class DemoReadinessOptions:
    reports_dir: Path
    strict: bool = False


def build_demo_readiness_summary(options: DemoReadinessOptions) -> dict[str, object]:
    payloads = _load_payloads(options.reports_dir)
    found = sorted([name for name, data in payloads.items() if data is not None])
    missing = sorted([name for name, data in payloads.items() if data is None])

    safety_env_status = _extract_status(payloads["safety_env"], ("status", "doctor_status", "safety_status"))
    config_profile_status = _extract_status(payloads["config_profile"], ("status", "validation_status"))
    mt5_readonly_status = _extract_status(payloads["mt5_readonly"], ("status", "readonly_status", "validation_status"))
    data_health_status = _extract_status(payloads["data_health"], ("status", "health_status"))
    risk_exposure_status = _extract_status(payloads["risk_exposure"], ("status", "risk_status", "exposure_status"))
    symbol_health_status = _extract_status(payloads["symbol_health"], ("status", "health_status"))

    blocking_reasons: list[str] = []
    warnings: list[str] = []
    manual_checks: list[str] = [
        "Verify MT5 terminal login manually in read-only mode.",
        "Confirm ENABLE_DEMO_EXECUTION remains disabled.",
        "Confirm no strategy/seuil changes were introduced by this report.",
    ]

    if missing:
        blocking_reasons.append(f"Missing required inputs: {', '.join(missing)}")

    for label, value in [
        ("safety_env_status", safety_env_status),
        ("config_profile_status", config_profile_status),
        ("mt5_readonly_status", mt5_readonly_status),
        ("data_health_status", data_health_status),
        ("risk_exposure_status", risk_exposure_status),
        ("symbol_health_status", symbol_health_status),
    ]:
        if value in {"FAIL", "ERROR", "BLOCKED", "INVALID", "NOT_READY"}:
            blocking_reasons.append(f"{label}={value}")
        elif value in {"WARN", "WARNING", "UNKNOWN", "MISSING"}:
            warnings.append(f"{label}={value}")

    if options.strict and (missing or warnings):
        blocking_reasons.append("Strict mode blocks readiness when inputs are missing or warnings exist.")

    final_status = _classify_final_status(
        strict=options.strict,
        missing=missing,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        mt5_readonly_status=mt5_readonly_status,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_inputs_found": found,
        "readiness_inputs_missing": missing,
        "safety_env_status": safety_env_status,
        "config_profile_status": config_profile_status,
        "mt5_readonly_status": mt5_readonly_status,
        "data_health_status": data_health_status,
        "risk_exposure_status": risk_exposure_status,
        "symbol_health_status": symbol_health_status,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "required_manual_checks": manual_checks,
        "final_status": final_status,
        "next_safe_steps": _next_safe_steps(final_status),
        "execution_authorization": "This report does not authorize order execution.",
        "demo_execution_enabled": False,
        "mt5_called": False,
        "orders_sent": False,
        "env_mutation_performed": False,
    }


def export_demo_readiness_json(summary: dict[str, object], reports_dir: Path) -> Path:
    path = reports_dir / "demo_readiness_summary.json"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_demo_readiness_txt(summary: dict[str, object], reports_dir: Path) -> Path:
    path = reports_dir / "demo_readiness_report.txt"
    reports_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Demo Readiness Summary",
        f"generated_at: {summary['generated_at']}",
        f"final_status: {summary['final_status']}",
        summary["execution_authorization"],
        "",
        f"readiness_inputs_found ({len(summary['readiness_inputs_found'])}):",
    ]
    lines.extend([f"- {item}" for item in summary["readiness_inputs_found"]])
    lines.append("")
    lines.append(f"readiness_inputs_missing ({len(summary['readiness_inputs_missing'])}):")
    lines.extend([f"- {item}" for item in summary["readiness_inputs_missing"]])
    lines.append("")
    lines.append("blocking_reasons:")
    lines.extend([f"- {item}" for item in summary["blocking_reasons"]])
    lines.append("")
    lines.append("warnings:")
    lines.extend([f"- {item}" for item in summary["warnings"]])
    lines.append("")
    lines.append("next_safe_steps:")
    lines.extend([f"- {item}" for item in summary["next_safe_steps"]])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _load_payloads(reports_dir: Path) -> dict[str, dict[str, object] | None]:
    result: dict[str, dict[str, object] | None] = {}
    for key, filename in INPUT_FILES.items():
        path = reports_dir / filename
        if not path.exists():
            result[key] = None
            continue
        try:
            result[key] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            result[key] = {"status": "INVALID"}
    return result


def _extract_status(payload: dict[str, object] | None, keys: tuple[str, ...]) -> str:
    if payload is None:
        return "MISSING"
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return "UNKNOWN"


def _classify_final_status(*, strict: bool, missing: list[str], blocking_reasons: list[str], warnings: list[str], mt5_readonly_status: str) -> str:
    if blocking_reasons:
        return "BLOCKED" if strict else "NOT_READY"
    if mt5_readonly_status == "OK":
        return "DEMO_PRECHECK_ONLY"
    if warnings or missing:
        return "NOT_READY" if strict else "PAPER_READY"
    return "PAPER_READY"


def _next_safe_steps(final_status: str) -> list[str]:
    if final_status in {"BLOCKED", "NOT_READY"}:
        return [
            "Regenerate missing/failed source reports.",
            "Keep trading disabled and stay in paper/read-only validation.",
        ]
    return [
        "Run manual MT5 demo read-only verification checklist.",
        "Do not enable demo execution; keep ENABLE_DEMO_EXECUTION=false.",
    ]
