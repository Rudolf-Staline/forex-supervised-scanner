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

from app.reporting.operator_dashboard import REQUIRED_REPORTS
from app.reporting.session_bundle import (
    DEFAULT_BUNDLE_FILES,
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


def write_reports(reports_dir: Path, *, with_dashboard: bool = True) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    for filename in REQUIRED_REPORTS.values():
        if filename.endswith(".jsonl"):
            (reports_dir / filename).write_text('{"cycle": 1, "blocking_reasons": []}\n', encoding="utf-8")
        else:
            (reports_dir / filename).write_text(json.dumps({"final_status": "OK"}), encoding="utf-8")
    if with_dashboard:
        (reports_dir / "operator_dashboard_summary.json").write_text(
            json.dumps(
                {
                    "final_operator_status": "OPERATOR_READY_FOR_PAPER_REVIEW",
                    "blocking_reasons": [],
                    "warnings": ["example warning"],
                }
            ),
            encoding="utf-8",
        )


def test_bundle_includes_files_with_checksums(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    output_dir = reports_dir / "bundles"
    write_reports(reports_dir)

    manifest = build_paper_session_bundle(reports_dir, output_dir, "paper-session-smoke", now=NOW)

    zip_path = output_dir / "paper-session-smoke.zip"
    assert zip_path.is_file()
    assert (output_dir / "paper-session-smoke_manifest.json").is_file()
    assert (output_dir / "paper-session-smoke_manifest.txt").is_file()

    included = {entry["name"]: entry for entry in manifest["included_files"]}
    assert set(REQUIRED_REPORTS.values()) <= set(included)
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        for filename, entry in included.items():
            member = f"paper-session-smoke/{filename}"
            assert member in names
            data = archive.read(member)
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            assert len(data) == entry["size_bytes"]

    assert manifest["generated_at"] == NOW.isoformat()
    assert manifest["session_name"] == "paper-session-smoke"
    assert manifest["zip_sha256"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()


def test_missing_files_listed_in_manifest(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    (reports_dir / "autonomous_scenario_suite.json").unlink()

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s1", now=NOW)

    assert "autonomous_scenario_suite.json" in manifest["missing_files"]
    assert any("required reports missing" in warning for warning in manifest["warnings"])
    optional_missing = [name for name in DEFAULT_BUNDLE_FILES if name not in {e["name"] for e in manifest["included_files"]}]
    assert set(manifest["missing_files"]) == set(optional_missing)


def test_dashboard_status_and_summary_propagated(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir, with_dashboard=True)

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s2", now=NOW)

    assert manifest["final_operator_status"] == "OPERATOR_READY_FOR_PAPER_REVIEW"
    assert manifest["blocking_reasons"] == []
    assert "example warning" in manifest["warnings"]


def test_missing_dashboard_summary_warns(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir, with_dashboard=False)

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s3", now=NOW)

    assert manifest["final_operator_status"] is None
    assert any("operator dashboard summary not found" in warning for warning in manifest["warnings"])


def test_empty_reports_dir_yields_empty_bundle_warning(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s4", now=NOW)

    assert manifest["included_files"] == []
    assert any("bundle is empty" in warning for warning in manifest["warnings"])


def test_invalid_session_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_paper_session_bundle(tmp_path, tmp_path / "bundles", "../escape", now=NOW)
    with pytest.raises(ValueError):
        build_paper_session_bundle(tmp_path, tmp_path / "bundles", "bad name", now=NOW)


def test_manifest_txt_render_contains_sections(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s5", now=NOW)

    text = render_manifest_txt(manifest)
    for section in ("included files", "missing files:", "blocking reasons:", "warnings:", "safety flags:", "zip_sha256="):
        assert section in text
    assert (reports_dir / "bundles" / "s5_manifest.txt").read_text(encoding="utf-8") == text


def test_safety_flags_assert_paper_only(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    manifest = build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s6", now=NOW)
    flags = manifest["safety_flags"]
    assert flags["read_only_bundle"] is True
    assert flags["paper_demo_only"] is True
    assert flags["live_trading_enabled"] is False
    assert flags["live_execution_allowed"] is False
    assert flags["broker_live_execution_allowed"] is False
    assert flags["order_send_called"] is False
    assert flags["env_mutation_performed"] is False
    assert flags["mt5_required"] is False


def test_source_reports_are_not_modified(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    before = {path.name: path.read_bytes() for path in reports_dir.iterdir() if path.is_file()}

    build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s7", now=NOW)

    after = {path.name: path.read_bytes() for path in reports_dir.iterdir() if path.is_file()}
    assert before == after


def test_no_order_send_no_mt5_no_env_mutation_in_sources() -> None:
    for path in (MODULE_PATH, SCRIPT_PATH):
        source = path.read_text(encoding="utf-8")
        assert "order_send(" not in source
        assert "MetaTrader5" not in source
        assert "import mt5" not in source.lower()
        assert "load_dotenv" not in source
        assert "set_key" not in source
        assert "while True" not in source


def test_no_env_mutation_during_build(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_reports(reports_dir)
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_MODE=paper\n", encoding="utf-8")
    before_env = dict(os.environ)
    before_file = env_file.read_text(encoding="utf-8")

    build_paper_session_bundle(reports_dir, reports_dir / "bundles", "s8", now=NOW)

    assert dict(os.environ) == before_env
    assert env_file.read_text(encoding="utf-8") == before_file


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
    assert "zip_sha256=" in output


def test_cli_strict_fails_on_empty_bundle(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    cli = load_cli_module()

    assert cli.main(["--reports-dir", str(reports_dir), "--output-dir", str(tmp_path / "out"), "--session-name", "empty", "--strict"]) == 1
    assert cli.main(["--reports-dir", str(reports_dir), "--output-dir", str(tmp_path / "out"), "--session-name", "empty"]) == 0


def test_cli_invalid_session_name_exit_code(tmp_path: Path) -> None:
    cli = load_cli_module()
    assert cli.main(["--reports-dir", str(tmp_path), "--output-dir", str(tmp_path / "out"), "--session-name", "../bad"]) == 2
