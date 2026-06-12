"""Tests for the read-only operator dashboard."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.reporting.operator_dashboard import (
    DEFAULT_OPERATOR_DASHBOARD_JSON,
    DEFAULT_OPERATOR_DASHBOARD_TXT,
    OPTIONAL_REPORTS,
    REQUIRED_REPORTS,
    STATUS_BLOCKED,
    STATUS_READY,
    STATUS_REPORTS_MISSING,
    STATUS_REPORTS_STALE,
    STATUS_WARN,
    build_operator_dashboard,
    export_operator_dashboard_json,
    export_operator_dashboard_txt,
    render_operator_dashboard_txt,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "operator_dashboard.py"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "reporting" / "operator_dashboard.py"

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
FRESH = NOW - timedelta(minutes=10)


def load_cli_module():
    spec = importlib.util.spec_from_file_location("operator_dashboard_cli", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(reports_dir: Path, filename: str, payload: dict) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def _write_heartbeat(reports_dir: Path, records: list[dict]) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    (reports_dir / "realtime_heartbeat.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def healthy_heartbeat_record(*, at: datetime = FRESH) -> dict:
    return {
        "cycle": 1,
        "heartbeat_at": at.isoformat(),
        "heartbeat_sequence": 1,
        "runtime_safety_heartbeat": True,
        "paper_demo_only": True,
        "live_execution_allowed": False,
        "stop_reason": None,
        "blocking_reasons": [],
        "safety_flags": {"live_execution_allowed": False, "order_send_called": False},
    }


def write_healthy_reports(reports_dir: Path, *, at: datetime = FRESH) -> None:
    stamp = at.isoformat()
    _write_json(reports_dir, "local_mt5_realtime_validation.json", {"completed_at": stamp, "final_status": "MT5_REALTIME_READY", "blocking_reasons": [], "warnings": [], "safety_flags": {"order_send_called": False}})
    _write_json(reports_dir, "realtime_command_center_summary.json", {"completed_at": stamp, "final_status": "COMPLETED", "blocking_reasons": [], "warnings": [], "safety_flags": {"live_execution_allowed": False}})
    _write_json(reports_dir, "realtime_paper_supervisor_summary.json", {"completed_at": stamp, "stop_reason": "COMPLETED_MAX_CYCLES", "blocking_reasons": [], "safety_flags": {"live_execution_allowed": False}})
    _write_json(reports_dir, "realtime_paper_positions.json", {"completed_at": stamp, "blocking_reasons": [], "warnings": [], "safety_flags": {"order_send_called": False}})
    _write_json(reports_dir, "autonomous_scenario_suite.json", {"generated_at": stamp, "final_status": "PASS"})
    _write_heartbeat(reports_dir, [healthy_heartbeat_record(at=at)])


def write_optional_reports(reports_dir: Path, *, at: datetime = FRESH) -> None:
    stamp = at.isoformat()
    _write_json(reports_dir, "autonomous_policy_report.json", {"timestamp": stamp, "decision": "ALLOW"})
    _write_json(reports_dir, "autonomous_readiness_report.json", {"generated_at": stamp, "final_status": "READY"})
    _write_json(reports_dir, "autonomous_evidence_report.json", {"generated_at": stamp, "final_status": "READY_EVIDENCE"})
    _write_json(reports_dir, "autonomous_recovery_plan.json", {"generated_at": stamp, "final_status": "NO_RECOVERY_NEEDED", "safe_actions": [], "manual_actions": []})


def test_all_reports_present_and_healthy(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    write_optional_reports(tmp_path)

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_READY
    assert summary["mt5_validation_status"] == "MT5_REALTIME_READY"
    assert summary["command_center_status"] == "COMPLETED"
    assert summary["supervisor_status"] == "COMPLETED_MAX_CYCLES"
    assert summary["position_manager_status"] == "COMPLETED"
    assert summary["heartbeat_status"] == "HEALTHY"
    assert summary["readiness_status"] == "READY"
    assert summary["evidence_status"] == "READY_EVIDENCE"
    assert summary["policy_decision"] == "ALLOW"
    assert summary["recovery_status"] == "NO_RECOVERY_NEEDED"
    assert summary["scenario_status"] == "PASS"
    assert summary["missing_reports"] == []
    assert summary["stale_reports"] == []
    assert summary["blocking_reasons"] == []


def test_missing_reports_produce_missing_status_and_warnings(tmp_path: Path) -> None:
    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_REPORTS_MISSING
    assert sorted(summary["missing_reports"]) == sorted(REQUIRED_REPORTS.values())
    assert any("required report missing" in warning for warning in summary["warnings"])
    assert all(summary["latest_report_times"][name] is None for name in REQUIRED_REPORTS.values())
    assert any("generate" in action for action in summary["recommended_next_actions"])


def test_single_missing_required_report_detected(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    (tmp_path / "autonomous_scenario_suite.json").unlink()

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_REPORTS_MISSING
    assert summary["missing_reports"] == ["autonomous_scenario_suite.json"]
    assert summary["scenario_status"] == "NOT_AVAILABLE"


def test_optional_reports_absent_do_not_block(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_READY
    assert summary["readiness_status"] == "NOT_AVAILABLE"
    assert summary["recovery_status"] == "NOT_AVAILABLE"
    assert not any(name in summary["missing_reports"] for name in OPTIONAL_REPORTS.values())


def test_stale_reports_detected(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path, at=NOW - timedelta(hours=48))

    summary = build_operator_dashboard(tmp_path, now=NOW, max_age_hours=24.0)

    assert summary["final_operator_status"] == STATUS_REPORTS_STALE
    assert "local_mt5_realtime_validation.json" in summary["stale_reports"]
    assert "realtime_heartbeat.jsonl" in summary["stale_reports"]
    assert any("stale" in warning for warning in summary["warnings"])


def test_heartbeat_safety_drift_blocks(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    record = healthy_heartbeat_record()
    record["stop_reason"] = "BLOCKED_BY_SAFETY_DRIFT"
    record["blocking_reasons"] = ["EXECUTION_MODE drifted; expected one of ['paper'], got live"]
    _write_heartbeat(tmp_path, [record])

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_BLOCKED
    assert summary["heartbeat_status"] == "SAFETY_DRIFT"
    assert any("safety drift" in reason for reason in summary["blocking_reasons"])


def test_heartbeat_live_execution_flag_blocks(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    record = healthy_heartbeat_record()
    record["live_execution_allowed"] = True
    _write_heartbeat(tmp_path, [record])

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_BLOCKED
    assert summary["heartbeat_status"] == "SAFETY_DRIFT"
    assert any("live trading is not authorized" in reason for reason in summary["blocking_reasons"])


def test_synthetic_fallback_block_is_surfaced(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    _write_json(
        tmp_path,
        "realtime_paper_supervisor_summary.json",
        {
            "completed_at": FRESH.isoformat(),
            "stop_reason": "BLOCKED_SYNTHETIC_FALLBACK",
            "blocking_reasons": ["synthetic fallback is not accepted for realtime paper mode"],
            "data_health_report": {"synthetic_fallback_used": True},
            "safety_flags": {"live_execution_allowed": False},
        },
    )

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_BLOCKED
    assert summary["supervisor_status"] == "BLOCKED_SYNTHETIC_FALLBACK"
    assert any("synthetic fallback" in reason for reason in summary["blocking_reasons"])


def test_blocked_command_center_blocks(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    _write_json(tmp_path, "realtime_command_center_summary.json", {"completed_at": FRESH.isoformat(), "final_status": "BLOCKED", "blocking_reasons": ["data quality below threshold"]})

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_BLOCKED
    assert "command_center: data quality below threshold" in summary["blocking_reasons"]


def test_warn_statuses_require_review(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    _write_json(tmp_path, "local_mt5_realtime_validation.json", {"completed_at": FRESH.isoformat(), "final_status": "MT5_REALTIME_WARN", "blocking_reasons": [], "warnings": ["spread wide"]})

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_WARN
    assert summary["mt5_validation_status"] == "MT5_REALTIME_WARN"


def test_unsafe_safety_flags_in_source_reports_block(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    _write_json(tmp_path, "realtime_paper_positions.json", {"completed_at": FRESH.isoformat(), "blocking_reasons": [], "warnings": [], "safety_flags": {"order_send_called": True}})

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["final_operator_status"] == STATUS_BLOCKED
    assert any("order_send_called" in reason for reason in summary["blocking_reasons"])


def test_recovery_recommendations_are_included(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    _write_json(
        tmp_path,
        "autonomous_recovery_plan.json",
        {
            "generated_at": FRESH.isoformat(),
            "final_status": "RECOVERY_RECOMMENDED",
            "next_recommended_command": "python scripts/autonomous_evidence_builder.py --export-json",
            "safe_actions": ["rebuild evidence"],
            "manual_actions": ["review operator controls"],
        },
    )

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["recovery_status"] == "RECOVERY_RECOMMENDED"
    actions = summary["recommended_next_actions"]
    assert any("autonomous_evidence_builder" in action for action in actions)
    assert any("rebuild evidence" in action for action in actions)
    assert any("review operator controls" in action for action in actions)


def test_unreadable_report_is_flagged(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    (tmp_path / "autonomous_scenario_suite.json").write_text("{not json", encoding="utf-8")

    summary = build_operator_dashboard(tmp_path, now=NOW)

    assert summary["scenario_status"] == "UNREADABLE"
    assert any("could not be parsed" in warning for warning in summary["warnings"])
    assert summary["final_operator_status"] == STATUS_REPORTS_MISSING


def test_exports_json_and_txt(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    summary = build_operator_dashboard(tmp_path, now=NOW)

    json_path = export_operator_dashboard_json(summary, tmp_path)
    txt_path = export_operator_dashboard_txt(summary, tmp_path)

    assert json_path == tmp_path / DEFAULT_OPERATOR_DASHBOARD_JSON
    assert txt_path == tmp_path / DEFAULT_OPERATOR_DASHBOARD_TXT
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["final_operator_status"] == STATUS_READY
    assert parsed["output_paths"]["json"] == str(json_path)
    text = txt_path.read_text(encoding="utf-8")
    assert "final_operator_status=OPERATOR_READY_FOR_PAPER_REVIEW" in text
    assert "read-only" in text


def test_txt_render_contains_all_sections(tmp_path: Path) -> None:
    summary = build_operator_dashboard(tmp_path, now=NOW)
    text = render_operator_dashboard_txt(summary)
    for section in ("component statuses:", "latest report times:", "missing reports:", "stale reports:", "blocking reasons:", "warnings:", "recommended next actions:", "safety flags:"):
        assert section in text


def test_safety_flags_assert_paper_only(tmp_path: Path) -> None:
    summary = build_operator_dashboard(tmp_path, now=NOW)
    flags = summary["safety_flags"]
    assert flags["paper_demo_only"] is True
    assert flags["read_only_dashboard"] is True
    assert flags["live_trading_enabled"] is False
    assert flags["live_execution_allowed"] is False
    assert flags["broker_live_execution_allowed"] is False
    assert flags["order_send_called"] is False
    assert flags["env_mutation_performed"] is False
    assert flags["mt5_required"] is False


def test_no_order_send_no_mt5_no_trading_in_sources() -> None:
    for path in (MODULE_PATH, SCRIPT_PATH):
        source = path.read_text(encoding="utf-8")
        assert "order_send(" not in source
        assert "MetaTrader5" not in source
        assert "import mt5" not in source.lower()
        assert "load_dotenv" not in source
        assert "set_key" not in source
        assert "while True" not in source


def test_no_env_mutation_during_build(tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_MODE=paper\n", encoding="utf-8")
    before_env = dict(os.environ)
    before_file = env_file.read_text(encoding="utf-8")

    build_operator_dashboard(tmp_path, now=NOW)

    assert dict(os.environ) == before_env
    assert env_file.read_text(encoding="utf-8") == before_file


def test_cli_exports_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_healthy_reports(tmp_path, at=datetime.now(timezone.utc))
    cli = load_cli_module()

    exit_code = cli.main(["--reports-dir", str(tmp_path), "--export-json", "--export-txt"])

    assert exit_code == 0
    assert (tmp_path / DEFAULT_OPERATOR_DASHBOARD_JSON).is_file()
    assert (tmp_path / DEFAULT_OPERATOR_DASHBOARD_TXT).is_file()
    output = capsys.readouterr().out
    assert "SAFETY" in output
    assert "final_operator_status=" in output


def test_cli_strict_fails_on_missing_reports(tmp_path: Path) -> None:
    cli = load_cli_module()
    assert cli.main(["--reports-dir", str(tmp_path), "--strict"]) == 1
    assert cli.main(["--reports-dir", str(tmp_path)]) == 0
