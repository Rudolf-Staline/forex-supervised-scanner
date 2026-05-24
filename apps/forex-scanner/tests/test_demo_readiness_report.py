from __future__ import annotations

import json
import os
from pathlib import Path

from app.reporting.demo_readiness import (
    DemoReadinessOptions,
    build_demo_readiness_summary,
    export_demo_readiness_json,
    export_demo_readiness_txt,
)


def _write_json(base: Path, name: str, payload: dict[str, object]) -> None:
    (base / name).write_text(json.dumps(payload), encoding="utf-8")


def test_missing_inputs_and_blocking_reasons(tmp_path: Path) -> None:
    _write_json(tmp_path, "safety_env_doctor.json", {"status": "OK"})
    summary = build_demo_readiness_summary(DemoReadinessOptions(reports_dir=tmp_path, strict=False))
    assert summary["readiness_inputs_missing"]
    assert any("Missing required inputs" in reason for reason in summary["blocking_reasons"])
    assert summary["final_status"] == "NOT_READY"


def test_strict_mode_blocks_on_warning(tmp_path: Path) -> None:
    _write_json(tmp_path, "safety_env_doctor.json", {"status": "OK"})
    _write_json(tmp_path, "config_profile_validation.json", {"status": "WARN"})
    _write_json(tmp_path, "mt5_readonly_validation.json", {"status": "OK"})
    _write_json(tmp_path, "data_health_report.json", {"status": "OK"})
    _write_json(tmp_path, "readiness_report.json", {"readiness_status": "PAPER_READY"})
    _write_json(tmp_path, "report_index.json", {"status": "OK"})
    _write_json(tmp_path, "local_validation_summary.json", {"status": "OK"})
    _write_json(tmp_path, "risk_exposure_summary.json", {"status": "OK"})
    _write_json(tmp_path, "symbol_health_summary.json", {"status": "OK"})

    summary = build_demo_readiness_summary(DemoReadinessOptions(reports_dir=tmp_path, strict=True))
    assert summary["final_status"] == "BLOCKED"


def test_final_status_demo_precheck_only(tmp_path: Path) -> None:
    for filename in [
        "readiness_report.json",
        "safety_env_doctor.json",
        "config_profile_validation.json",
        "mt5_readonly_validation.json",
        "data_health_report.json",
        "report_index.json",
        "local_validation_summary.json",
        "risk_exposure_summary.json",
        "symbol_health_summary.json",
    ]:
        _write_json(tmp_path, filename, {"status": "OK"})

    summary = build_demo_readiness_summary(DemoReadinessOptions(reports_dir=tmp_path, strict=False))
    assert summary["final_status"] == "DEMO_PRECHECK_ONLY"
    assert summary["execution_authorization"] == "This report does not authorize order execution."


def test_export_json_and_txt(tmp_path: Path) -> None:
    summary = build_demo_readiness_summary(DemoReadinessOptions(reports_dir=tmp_path, strict=False))
    json_path = export_demo_readiness_json(summary, tmp_path)
    txt_path = export_demo_readiness_txt(summary, tmp_path)
    assert json_path.exists()
    assert txt_path.exists()
    assert "This report does not authorize order execution." in txt_path.read_text(encoding="utf-8")


def test_no_mt5_call_and_no_environ_mutation(tmp_path: Path) -> None:
    before = dict(os.environ)
    summary = build_demo_readiness_summary(DemoReadinessOptions(reports_dir=tmp_path, strict=False))
    after = dict(os.environ)
    assert summary["mt5_called"] is False
    assert summary["env_mutation_performed"] is False
    assert before == after
