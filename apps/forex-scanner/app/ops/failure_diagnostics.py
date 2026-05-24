"""Failure diagnostics built from existing report artifacts only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

EXPECTED_REPORTS = [
    "local_validation_summary.json",
    "post_merge_audit.json",
    "report_index.json",
    "repository_maintenance_audit.json",
    "readiness_report.json",
]


@dataclass(frozen=True)
class FailureDiagnosticsOptions:
    reports_dir: Path
    show_suggestions: bool = False


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")


def _load_inputs(reports_dir: Path) -> tuple[list[str], list[str], list[str]]:
    found: list[str] = []
    missing: list[str] = []
    texts: list[str] = []

    for name in EXPECTED_REPORTS:
        file_path = reports_dir / name
        if file_path.exists():
            found.append(name)
            texts.append(_safe_read_text(file_path))
        else:
            missing.append(name)

    for txt_file in sorted(reports_dir.glob("*.txt")):
        found.append(txt_file.name)
        texts.append(_safe_read_text(txt_file))

    return found, missing, texts


def _collect_json_failures(payload: Any) -> list[str]:
    failures: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_l = str(key).lower()
            if isinstance(value, str) and any(token in value.lower() for token in ["fail", "error", "timeout"]):
                failures.append(f"{key}={value}")
            if key_l in {"failed_commands", "failed_tests", "errors", "failures"} and value:
                failures.append(f"{key}={value}")
            failures.extend(_collect_json_failures(value))
    elif isinstance(payload, list):
        for item in payload:
            failures.extend(_collect_json_failures(item))
    return failures


def build_failure_diagnostics_summary(options: FailureDiagnosticsOptions) -> dict[str, Any]:
    found, missing, texts = _load_inputs(options.reports_dir)

    failed_commands: list[str] = []
    failed_tests: list[str] = []
    mt5_unavailable: list[str] = []
    safety_blockers: list[str] = []
    missing_file_errors: list[str] = []
    import_errors: list[str] = []
    timeout_indicators: list[str] = []

    for report_name in found:
        if report_name.endswith(".json") and (options.reports_dir / report_name).exists():
            try:
                payload = json.loads(_safe_read_text(options.reports_dir / report_name))
                json_failures = _collect_json_failures(payload)
                for item in json_failures:
                    item_l = item.lower()
                    if "failed_command" in item_l or "command" in item_l and "fail" in item_l:
                        failed_commands.append(f"{report_name}: {item}")
                    if "test" in item_l and "fail" in item_l:
                        failed_tests.append(f"{report_name}: {item}")
            except json.JSONDecodeError:
                pass

    for blob in texts:
        for raw_line in blob.splitlines():
            line = raw_line.strip()
            line_l = line.lower()
            if not line:
                continue
            if "failed" in line_l and "command" in line_l:
                failed_commands.append(line)
            if "failed" in line_l and "test" in line_l:
                failed_tests.append(line)
            if "mt5" in line_l and any(token in line_l for token in ["unavailable", "not available", "disabled", "missing"]):
                mt5_unavailable.append(line)
            if any(token in line_l for token in ["safety blocker", "blocked by safety", "execution blocked"]):
                safety_blockers.append(line)
            if any(token in line_l for token in ["no such file", "file not found", "missing file"]):
                missing_file_errors.append(line)
            if "importerror" in line_l or "modulenotfounderror" in line_l:
                import_errors.append(line)
            if "timeout" in line_l or "timed out" in line_l:
                timeout_indicators.append(line)

    likely_root_causes: list[str] = []
    if mt5_unavailable:
        likely_root_causes.append("MT5 environment unavailable or intentionally disabled.")
    if import_errors:
        likely_root_causes.append("Python dependencies or module paths are incomplete.")
    if missing_file_errors:
        likely_root_causes.append("Expected report artifacts are missing.")
    if timeout_indicators:
        likely_root_causes.append("Slow or hanging commands caused timeouts.")
    if failed_commands and not likely_root_causes:
        likely_root_causes.append("One or more commands exited with non-zero status.")

    suggestions: list[str] = []
    if options.show_suggestions:
        suggestions = [
            "Collect all required reports before diagnostics review.",
            "Fix missing imports/dependencies before retrying tests.",
            "Validate MT5 availability only in safe read-only mode.",
            "Investigate failed commands individually with short, targeted reruns.",
        ]

    severity = "CLEAN"
    if safety_blockers:
        severity = "BLOCKED"
    elif failed_commands or failed_tests or mt5_unavailable or import_errors or missing_file_errors or timeout_indicators:
        severity = "NEEDS_REVIEW"
    elif missing:
        severity = "WARN"

    return {
        "diagnostics_generated_at": datetime.now(timezone.utc).isoformat(),
        "input_reports_found": sorted(set(found)),
        "input_reports_missing": missing,
        "failed_commands_detected": sorted(set(failed_commands)),
        "failed_tests_detected": sorted(set(failed_tests)),
        "mt5_unavailable_warnings": sorted(set(mt5_unavailable)),
        "safety_blockers": sorted(set(safety_blockers)),
        "missing_file_errors": sorted(set(missing_file_errors)),
        "import_errors": sorted(set(import_errors)),
        "timeout_indicators": sorted(set(timeout_indicators)),
        "likely_root_causes": likely_root_causes,
        "suggested_next_steps": suggestions,
        "severity": severity,
        "execution_authorization": "This report is diagnostic-only and never authorizes live trading.",
        "mt5_called": False,
        "orders_sent": False,
    }


def export_failure_diagnostics_json(summary: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / "failure_diagnostics_summary.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return out


def export_failure_diagnostics_txt(summary: dict[str, Any], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / "failure_diagnostics_report.txt"
    lines = [
        "Failure Diagnostics Report",
        f"generated_at={summary['diagnostics_generated_at']}",
        f"severity={summary['severity']}",
        summary["execution_authorization"],
        "",
    ]
    for key in [
        "input_reports_found",
        "input_reports_missing",
        "failed_commands_detected",
        "failed_tests_detected",
        "mt5_unavailable_warnings",
        "safety_blockers",
        "missing_file_errors",
        "import_errors",
        "timeout_indicators",
        "likely_root_causes",
        "suggested_next_steps",
    ]:
        lines.append(f"{key}:")
        values = summary.get(key) or []
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- none")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
