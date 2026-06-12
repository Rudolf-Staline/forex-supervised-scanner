"""Tests for the read-only paper session review orchestrator."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.reporting.paper_session_review import (
    DEFAULT_REVIEW_JSON,
    DEFAULT_REVIEW_TXT,
    STATUS_BLOCKED,
    STATUS_INCOMPLETE,
    PaperSessionReviewConfig,
    PaperSessionReviewService,
    build_paper_session_review,
    render_paper_session_review_txt,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "paper_session_review.py"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "reporting" / "paper_session_review.py"
NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def load_cli_module():
    spec = importlib.util.spec_from_file_location("paper_session_review_cli", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_minimal_ready_reports(reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = NOW.isoformat()
    write_json(
        reports_dir / "local_mt5_realtime_validation.json",
        {"generated_at": timestamp, "final_status": "MT5_REALTIME_READY", "safety_flags": {"order_send_called": False}},
    )
    write_json(reports_dir / "realtime_command_center_summary.json", {"generated_at": timestamp, "final_status": "COMPLETED"})
    write_json(
        reports_dir / "realtime_paper_supervisor_summary.json",
        {"generated_at": timestamp, "final_status": "COMPLETED_MAX_CYCLES"},
    )
    write_json(
        reports_dir / "realtime_paper_positions.json",
        {
            "generated_at": timestamp,
            "final_status": "COMPLETED",
            "orders": [
                {
                    "id": "paper-1",
                    "status": "closed",
                    "symbol": "EUR/USD",
                    "timeframe": "M1",
                    "strategy": "test-strategy",
                    "realized_r": 1.5,
                    "realized_pnl": 15.0,
                    "opened_at": timestamp,
                    "closed_at": timestamp,
                }
            ],
        },
    )
    (reports_dir / "realtime_heartbeat.jsonl").write_text(json.dumps({"heartbeat_at": timestamp, "status": "OK"}) + "\n", encoding="utf-8")
    write_json(reports_dir / "autonomous_scenario_suite.json", {"generated_at": timestamp, "final_status": "PASS"})



def test_review_exports_summary_dashboard_and_performance(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_minimal_ready_reports(reports_dir)

    summary = build_paper_session_review(reports_dir, export_json=True, export_txt=True, now=NOW)

    assert summary["generated_at"] == NOW.isoformat()
    assert summary["operator_status"] == "OPERATOR_READY_FOR_PAPER_REVIEW"
    assert summary["performance_status"] in {"PAPER_PERFORMANCE_READY", "PAPER_PERFORMANCE_WARN"}
    assert (reports_dir / DEFAULT_REVIEW_JSON).is_file()
    assert (reports_dir / DEFAULT_REVIEW_TXT).is_file()
    assert (reports_dir / "operator_dashboard_summary.json").is_file()
    assert (reports_dir / "paper_performance_summary.json").is_file()
    text = render_paper_session_review_txt(summary)
    assert "PAPER SESSION REVIEW" in text
    assert "final_review_status" in text


def test_review_marks_missing_reports_as_incomplete(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"

    summary = build_paper_session_review(reports_dir, export_json=True, now=NOW)

    assert summary["final_review_status"] == STATUS_INCOMPLETE
    assert "local_mt5_realtime_validation.json" in summary["missing_reports"]
    assert any("required report missing" in warning for warning in summary["warnings"])


def test_review_exports_bundle_when_requested(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    output_dir = reports_dir / "bundles"
    write_minimal_ready_reports(reports_dir)

    summary = build_paper_session_review(
        reports_dir,
        export_json=True,
        export_txt=True,
        export_bundle=True,
        bundle_output_dir=output_dir,
        session_name="review-smoke",
        now=NOW,
    )

    assert summary["bundle_status"] == "EXPORTED"
    assert (output_dir / "review-smoke.zip").is_file()
    assert (output_dir / "review-smoke_manifest.json").is_file()
    assert summary["output_paths"]["bundle_zip"] == str(output_dir / "review-smoke.zip")


def test_unsafe_source_flags_block_review(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_minimal_ready_reports(reports_dir)
    write_json(
        reports_dir / "local_mt5_realtime_validation.json",
        {"generated_at": NOW.isoformat(), "final_status": "MT5_REALTIME_READY", "safety_flags": {"order_send_called": True}},
    )

    summary = build_paper_session_review(reports_dir, export_json=True, now=NOW)

    assert summary["final_review_status"] == STATUS_BLOCKED
    assert any("unsafe safety flags" in reason for reason in summary["blocking_reasons"])


def test_strict_cli_returns_non_zero_for_incomplete_review(tmp_path: Path) -> None:
    cli = load_cli_module()

    result = cli.main(["--reports-dir", str(tmp_path / "reports"), "--strict"])

    assert result == 1


def test_no_order_send_no_terminal_import_no_env_mutation_in_sources() -> None:
    for path in (MODULE_PATH, SCRIPT_PATH):
        source = path.read_text(encoding="utf-8").lower()
        assert "order_send(" not in source
        assert "import metatrader" not in source
        assert "import mt5" not in source
        assert "load_dotenv" not in source
        assert "set_key" not in source
        assert "while true" not in source


def test_no_env_mutation_during_review(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_minimal_ready_reports(reports_dir)
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_MODE=paper\n", encoding="utf-8")
    before_env = dict(os.environ)
    before_file = env_file.read_text(encoding="utf-8")

    PaperSessionReviewService(PaperSessionReviewConfig(reports_dir=reports_dir, export_json=True, now=NOW)).build_summary()

    assert dict(os.environ) == before_env
    assert env_file.read_text(encoding="utf-8") == before_file
