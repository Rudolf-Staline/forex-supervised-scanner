"""Tests for the read-only paper session bundle exporter."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.reporting.session_bundle import (
    DEFAULT_BUNDLE_FILES,
    OPTIONAL_PAPER_SESSION_REPORTS,
    REQUIRED_PAPER_SESSION_REPORTS,
    PaperSessionBundleConfig,
    PaperSessionBundleService,
    PaperSessionBundleStrictError,
    build_paper_session_bundle,
    render_manifest_txt,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "export_paper_session_bundle.py"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "reporting" / "session_bundle.py"

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def load_cli_module():
    spec = importlib.util.spec_from_file_location("export_paper_session_bundle_cli", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_reports(reports_dir: Path, *, filenames: tuple[str, ...] = DEFAULT_BUNDLE_FILES, with_dashboard: bool = True) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        if filename == "operator_dashboard_summary.json" and with_dashboard:
            payload = {
                "final_operator_status": "OPERATOR_READY_FOR_PAPER_REVIEW",
                "blocking_reasons": ["manual review required"],
                "warnings": ["example warning"],
                "safety_flags": {"paper_demo_only": True, "order_send_called": False},
            }
            (reports_dir / filename).write_text(json.dumps(payload), encoding="utf-8")
        elif filename.endswith(".json"):
            (reports_dir / filename).write_text(json.dumps({"final_status": "OK", "file": filename}), encoding="utf-8")
        elif filename.endswith(".jsonl"):
            (reports_dir / filename).write_text('{"cycle": 1, "blocking_reasons": []}\n', encoding="utf-8")
        elif filename.endswith(".csv"):
            (reports_dir / filename).write_text("timestamp,bid,ask\n2026-06-12T12:00:00Z,1.0,1.1\n", encoding="utf-8")
        else:
            (reports_dir / filename).write_text(f"report: {filename}\n", encoding="utf-8")
    if not with_dashboard:
        (reports_dir / "operator_dashboard_summary.json").unlink(missing_ok=True)


def test_bundle_creation_with_all_reports_present(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    output_dir = reports_dir / "bundles"
    write_reports(reports_dir)

    manifest = build_paper_session_bundle(reports_dir, output_dir, "paper-session-smoke", now=NOW)

    assert manifest["generated_at"] == NOW.isoformat()
    assert manifest["session_name"] == "paper-session-smoke"
    assert manifest["reports_dir"] == str(reports_dir)
    assert manifest["output_dir"] == str(output_dir)
    assert manifest["bundle_path"] == str(output_dir / "paper-session-smoke.zip")
    assert manifest["manifest_json_path"] == str(output_dir / "paper-session-smoke_manifest.json")
    assert manifest["manifest_txt_path"] == str(output_dir / "paper-session-smoke_manifest.txt")
    assert manifest["missing_files"] == []
    assert manifest["optional_missing_files"] == []
    assert {entry["path"] for entry in manifest["included_files"]} == set(DEFAULT_BUNDLE_FILES)


def test_missing_required_reports_are_recorded(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    (reports_dir / "autonomous_scenario_suite.json").unlink()

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s1", now=NOW)

    assert manifest["missing_files"] == ["autonomous_scenario_suite.json"]
    assert "autonomous_scenario_suite.json" not in manifest["optional_missing_files"]
    assert any("required reports missing" in warning for warning in manifest["warnings"])


def test_optional_missing_reports_are_recorded_separately(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir, filenames=REQUIRED_PAPER_SESSION_REPORTS)

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s2", now=NOW, include_optional=True)

    assert manifest["missing_files"] == []
    assert set(manifest["optional_missing_files"]) == set(OPTIONAL_PAPER_SESSION_REPORTS)
    assert any("optional reports missing" in warning for warning in manifest["warnings"])


def test_sha256_checksums_are_stable_and_correct(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "operator_dashboard_summary.json").write_text('{"final_operator_status":"READY"}', encoding="utf-8")

    first = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s3", filenames=("operator_dashboard_summary.json",), now=NOW)
    second = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s4", filenames=("operator_dashboard_summary.json",), now=NOW)

    expected = hashlib.sha256((reports_dir / "operator_dashboard_summary.json").read_bytes()).hexdigest()
    assert first["included_files"][0]["sha256"] == expected
    assert second["included_files"][0]["sha256"] == expected
    assert first["included_files"][0]["file_size_bytes"] == len((reports_dir / "operator_dashboard_summary.json").read_bytes())


def test_zip_contains_included_files_and_manifest(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    output_dir = reports_dir / "bundles"
    write_reports(reports_dir)
    manifest = build_paper_session_bundle(reports_dir, output_dir, "paper-session-smoke", now=NOW)

    with zipfile.ZipFile(output_dir / "paper-session-smoke.zip") as archive:
        names = set(archive.namelist())
        assert "paper-session-smoke/manifest.json" in names
        assert "paper-session-smoke/manifest.txt" in names
        zipped_manifest = json.loads(archive.read("paper-session-smoke/manifest.json").decode("utf-8"))
        assert zipped_manifest["session_name"] == "paper-session-smoke"
        for entry in manifest["included_files"]:
            assert entry["archive_path"] in names
            data = archive.read(entry["archive_path"])
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            assert len(data) == entry["file_size_bytes"]


def test_manifest_json_and_txt_are_written(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s5", now=NOW)

    json_path = reports_dir / "bundles" / "s5_manifest.json"
    txt_path = reports_dir / "bundles" / "s5_manifest.txt"
    assert json.loads(json_path.read_text(encoding="utf-8"))["session_name"] == "s5"
    text = txt_path.read_text(encoding="utf-8")
    assert text == render_manifest_txt(manifest)
    for section in ("included files", "missing required files:", "optional missing files:", "blocking reasons:", "warnings:", "safety flags:"):
        assert section in text


def test_strict_mode_blocks_when_required_reports_are_missing(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    (reports_dir / "realtime_heartbeat.jsonl").unlink()

    with pytest.raises(PaperSessionBundleStrictError):
        build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s6", now=NOW, strict=True)

    cli = load_cli_module()
    assert cli.main(["--reports-dir", str(reports_dir), "--output-dir", str(reports_dir / "bundles"), "--session-name", "strict", "--strict"]) == 1


def test_operator_dashboard_final_status_blocking_and_warnings_are_propagated(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir, with_dashboard=True)

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s7", now=NOW)

    assert manifest["final_operator_status"] == "OPERATOR_READY_FOR_PAPER_REVIEW"
    assert manifest["blocking_reasons"] == ["manual review required"]
    assert "example warning" in manifest["warnings"]


def test_safety_flags_are_propagated_and_paper_only(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir, with_dashboard=True)
    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s8", now=NOW)
    flags = manifest["safety_flags"]

    assert flags["read_only_bundle"] is True
    assert flags["paper_demo_only"] is True
    assert flags["live_trading_enabled"] is False
    assert flags["live_execution_allowed"] is False
    assert flags["broker_live_execution_allowed"] is False
    assert flags["broker_order_submission_allowed"] is False
    assert flags["order_send_called"] is False
    assert flags["env_mutation_performed"] is False
    assert flags["mt5_required"] is False
    assert flags["operator_dashboard_safety_flags"]["paper_demo_only"] is True


def test_no_order_send_no_terminal_import_no_env_mutation_in_sources() -> None:
    for path in (MODULE_PATH, SCRIPT_PATH):
        source = path.read_text(encoding="utf-8").lower()
        assert "order_send(" not in source
        assert "import metatrader" not in source
        assert "import mt5" not in source
        assert "load_dotenv" not in source
        assert "set_key" not in source
        assert "while true" not in source


def test_no_env_mutation_during_build(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_MODE=paper\n", encoding="utf-8")
    before_env = dict(os.environ)
    before_file = env_file.read_text(encoding="utf-8")

    build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s9", now=NOW)

    assert dict(os.environ) == before_env
    assert env_file.read_text(encoding="utf-8") == before_file


def test_source_reports_are_not_modified(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    before = {path.name: path.read_bytes() for path in reports_dir.iterdir() if path.is_file()}

    build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s10", now=NOW)

    after = {path.name: path.read_bytes() for path in reports_dir.iterdir() if path.is_file()}
    assert before == after


def test_generated_bundle_paths_are_under_configured_output_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    config = PaperSessionBundleConfig(reports_dir=tmp_path / "reports", output_dir=output_dir, session_name="safe", generated_at=NOW)
    manifest = PaperSessionBundleService(config).export().to_dict()

    output_root = output_dir.resolve()
    for key in ("bundle_path", "manifest_json_path", "manifest_txt_path"):
        assert Path(manifest[key]).resolve().is_relative_to(output_root)
        assert Path(manifest[key]).is_file()


def test_missing_dashboard_summary_warns(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir, with_dashboard=False)

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s11", now=NOW)

    assert manifest["final_operator_status"] is None
    assert any("operator dashboard summary not found" in warning for warning in manifest["warnings"])


def test_invalid_session_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_paper_session_bundle(tmp_path, tmp_path / "bundles", "../escape", now=NOW)
    with pytest.raises(ValueError):
        build_paper_session_bundle(tmp_path, tmp_path / "bundles", "bad name", now=NOW)


def test_cli_creates_bundle_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reports_dir = tmp_path / "reports"
    output_dir = tmp_path / "out"
    write_reports(reports_dir)
    cli = load_cli_module()

    exit_code = cli.main(["--reports-dir", str(reports_dir), "--output-dir", str(output_dir), "--session-name", "paper-session-smoke"])

    assert exit_code == 0
    assert (output_dir / "paper-session-smoke.zip").is_file()
    assert (output_dir / "paper-session-smoke_manifest.json").is_file()
    assert (output_dir / "paper-session-smoke_manifest.txt").is_file()
    output = capsys.readouterr().out
    assert "SAFETY" in output
    assert "bundle_path=" in output


def test_cli_invalid_session_name_exit_code(tmp_path: Path) -> None:
    cli = load_cli_module()
    assert cli.main(["--reports-dir", str(tmp_path), "--output-dir", str(tmp_path / "out"), "--session-name", "../bad"]) == 2
