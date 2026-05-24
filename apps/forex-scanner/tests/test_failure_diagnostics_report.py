from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.ops.failure_diagnostics import (
    FailureDiagnosticsOptions,
    build_failure_diagnostics_summary,
    export_failure_diagnostics_json,
    export_failure_diagnostics_txt,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_reports_without_crash(tmp_path: Path) -> None:
    summary = build_failure_diagnostics_summary(FailureDiagnosticsOptions(reports_dir=tmp_path))
    assert summary["input_reports_missing"]
    assert summary["severity"] in {"WARN", "CLEAN", "NEEDS_REVIEW", "BLOCKED"}


def test_detect_failed_commands_import_errors_and_mt5(tmp_path: Path) -> None:
    (tmp_path / "report_index.json").write_text(
        json.dumps({"failed_commands": ["pytest -q"], "status": "FAIL"}), encoding="utf-8"
    )
    (tmp_path / "local_validation_summary.json").write_text(
        "FAILED command: pytest -q\nImportError: missing numpy\nMT5 unavailable in CI\n", encoding="utf-8"
    )
    summary = build_failure_diagnostics_summary(
        FailureDiagnosticsOptions(reports_dir=tmp_path, show_suggestions=True)
    )
    assert summary["failed_commands_detected"]
    assert summary["import_errors"]
    assert summary["mt5_unavailable_warnings"]
    assert summary["suggested_next_steps"]


def test_export_json_and_txt(tmp_path: Path) -> None:
    summary = build_failure_diagnostics_summary(FailureDiagnosticsOptions(reports_dir=tmp_path))
    json_path = export_failure_diagnostics_json(summary, tmp_path)
    txt_path = export_failure_diagnostics_txt(summary, tmp_path)
    assert json_path.exists()
    assert txt_path.exists()


def test_no_input_mutation(tmp_path: Path) -> None:
    src = tmp_path / "readiness_report.json"
    src.write_text(json.dumps({"status": "OK"}), encoding="utf-8")
    before = _digest(src)
    _ = build_failure_diagnostics_summary(FailureDiagnosticsOptions(reports_dir=tmp_path))
    after = _digest(src)
    assert before == after
