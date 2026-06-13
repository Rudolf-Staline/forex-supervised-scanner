"""Tests for the read-only paper session history ledger."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.reporting.paper_session_history import (
    DEFAULT_HISTORY_JSON,
    DEFAULT_HISTORY_JSONL,
    DEFAULT_HISTORY_TXT,
    STATUS_BLOCKED,
    STATUS_EMPTY,
    STATUS_INCOMPLETE,
    STATUS_READY,
    STATUS_WARN,
    PaperSessionHistoryConfig,
    PaperSessionHistoryService,
    append_history_entry,
    build_history_entry,
    build_history_summary,
    load_history_entries,
    render_history_txt,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "paper_session_history.py"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "reporting" / "paper_session_history.py"
NOW = datetime(2026, 6, 13, 0, 0, tzinfo=timezone.utc)


def load_cli_module():
    spec = importlib.util.spec_from_file_location("paper_session_history_cli", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_review_artifacts(
    reports_dir: Path,
    *,
    review_status: str = "PAPER_SESSION_REVIEW_READY",
    review_generated_at: str = NOW.isoformat(),
    review_warnings: list[str] | None = None,
    review_blocking: list[str] | None = None,
    review_safety_flags: dict | None = None,
    with_performance: bool = True,
) -> None:
    write_json(
        reports_dir / "paper_session_review_summary.json",
        {
            "generated_at": review_generated_at,
            "final_review_status": review_status,
            "operator_status": "OPERATOR_READY_FOR_PAPER_REVIEW",
            "performance_status": "PAPER_PERFORMANCE_READY",
            "bundle_status": "EXPORTED",
            "blocking_reasons": review_blocking or [],
            "warnings": review_warnings or [],
            "safety_flags": review_safety_flags or {"paper_demo_only": True, "order_send_called": False},
        },
    )
    if with_performance:
        write_json(
            reports_dir / "paper_performance_summary.json",
            {
                "total_paper_trades": 5,
                "closed_count": 4,
                "win_count": 2,
                "loss_count": 1,
                "breakeven_count": 1,
                "win_rate": 0.5,
                "realized_r_total": 1.8,
                "average_r": 0.45,
                "realized_pnl_total": 42.0,
                "max_drawdown": 0.7,
                "symbols_traded": ["EUR/USD", "XAU/USD"],
            },
        )
    write_json(reports_dir / "operator_dashboard_summary.json", {"final_operator_status": "OPERATOR_READY_FOR_PAPER_REVIEW"})


def run_service(reports_dir: Path, **kwargs) -> dict:
    config = PaperSessionHistoryConfig(reports_dir=reports_dir, now=kwargs.pop("now", NOW), **kwargs)
    return PaperSessionHistoryService(config).run()


def test_append_latest_review_creates_entry(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)

    summary = run_service(tmp_path, append_latest=True, export_json=True, export_txt=True)

    assert summary["append_result"] == "APPENDED"
    assert summary["total_sessions"] == 1
    assert summary["final_history_status"] == STATUS_READY
    entries, _ = load_history_entries(tmp_path)
    entry = entries[0]
    assert entry["session_name"] == "paper-session-review"
    assert entry["review_generated_at"] == NOW.isoformat()
    assert entry["final_review_status"] == "PAPER_SESSION_REVIEW_READY"
    assert entry["total_paper_trades"] == 5
    assert entry["closed_count"] == 4
    assert entry["win_rate"] == 0.5
    assert entry["realized_r_total"] == 1.8
    assert entry["realized_pnl_total"] == 42.0
    assert entry["max_drawdown"] == 0.7
    assert entry["symbols_traded"] == ["EUR/USD", "XAU/USD"]
    assert "review" in entry["source_paths"]
    assert "performance" in entry["source_paths"]


def test_missing_review_file_is_safe_without_strict(tmp_path: Path) -> None:
    summary = run_service(tmp_path, append_latest=True)

    assert summary["append_result"] == "REVIEW_MISSING"
    assert summary["final_history_status"] == STATUS_EMPTY
    assert any("review summary not found" in warning for warning in summary["warnings"])
    assert not (tmp_path / DEFAULT_HISTORY_JSONL).exists()


def test_missing_review_with_existing_history_is_incomplete(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    run_service(tmp_path, append_latest=True)
    (tmp_path / "paper_session_review_summary.json").unlink()

    summary = run_service(tmp_path, append_latest=True)

    assert summary["append_result"] == "REVIEW_MISSING"
    assert summary["final_history_status"] == STATUS_INCOMPLETE
    assert summary["total_sessions"] == 1


def test_strict_cli_fails_when_review_missing(tmp_path: Path) -> None:
    cli = load_cli_module()
    assert cli.main(["--reports-dir", str(tmp_path), "--append-latest", "--strict"]) == 1
    assert cli.main(["--reports-dir", str(tmp_path), "--append-latest"]) == 0


def test_duplicate_session_is_skipped(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    first = run_service(tmp_path, append_latest=True)
    second = run_service(tmp_path, append_latest=True, now=NOW + timedelta(hours=1))

    assert first["append_result"] == "APPENDED"
    assert second["append_result"] == "DUPLICATE_SKIPPED"
    assert second["total_sessions"] == 1
    assert any("duplicate history entry skipped" in warning for warning in second["warnings"])
    entries, _ = load_history_entries(tmp_path)
    assert entries[0]["recorded_at"] == NOW.isoformat()


def test_new_review_timestamp_is_not_a_duplicate(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    run_service(tmp_path, append_latest=True)
    write_review_artifacts(tmp_path, review_generated_at=(NOW + timedelta(hours=2)).isoformat())

    summary = run_service(tmp_path, append_latest=True, now=NOW + timedelta(hours=2))

    assert summary["append_result"] == "APPENDED"
    assert summary["total_sessions"] == 2


def test_rebuild_summary_from_existing_jsonl_without_append(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    run_service(tmp_path, append_latest=True)

    summary = run_service(tmp_path)

    assert summary["append_result"] is None
    assert summary["total_sessions"] == 1
    assert summary["status_counts"] == {"PAPER_SESSION_REVIEW_READY": 1}


def test_status_counts_and_latest_sessions(tmp_path: Path) -> None:
    statuses = [
        ("PAPER_SESSION_REVIEW_READY", NOW),
        ("PAPER_SESSION_REVIEW_WARN", NOW + timedelta(hours=1)),
        ("PAPER_SESSION_REVIEW_INCOMPLETE", NOW + timedelta(hours=2)),
        ("PAPER_SESSION_REVIEW_READY", NOW + timedelta(hours=3)),
    ]
    for status, at in statuses:
        write_review_artifacts(tmp_path, review_status=status, review_generated_at=at.isoformat())
        run_service(tmp_path, append_latest=True, now=at)

    summary = run_service(tmp_path)

    assert summary["total_sessions"] == 4
    assert summary["status_counts"] == {"PAPER_SESSION_REVIEW_READY": 2, "PAPER_SESSION_REVIEW_WARN": 1, "PAPER_SESSION_REVIEW_INCOMPLETE": 1}
    assert summary["latest_session"]["final_review_status"] == "PAPER_SESSION_REVIEW_READY"
    assert summary["latest_ready_session"]["recorded_at"] == (NOW + timedelta(hours=3)).isoformat()
    assert summary["latest_warn_session"]["recorded_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert summary["latest_incomplete_session"]["recorded_at"] == (NOW + timedelta(hours=2)).isoformat()
    assert summary["latest_blocked_session"] is None


def test_aggregate_trade_and_outcome_metrics(tmp_path: Path) -> None:
    for hour in (0, 1):
        write_review_artifacts(tmp_path, review_generated_at=(NOW + timedelta(hours=hour)).isoformat())
        run_service(tmp_path, append_latest=True, now=NOW + timedelta(hours=hour))

    summary = run_service(tmp_path)

    assert summary["aggregate_closed_trades"] == 8
    assert summary["aggregate_wins"] == 4
    assert summary["aggregate_losses"] == 2
    assert summary["aggregate_breakevens"] == 2
    assert summary["average_win_rate"] == 0.5
    assert summary["aggregate_realized_r"] == 3.6
    assert summary["aggregate_realized_pnl"] == 84.0
    assert summary["distinct_symbols_traded"] == ["EUR/USD", "XAU/USD"]


def test_metrics_null_when_performance_missing(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path, with_performance=False)
    (tmp_path / "paper_performance_summary.json").unlink(missing_ok=True)

    summary = run_service(tmp_path, append_latest=True)

    entries, _ = load_history_entries(tmp_path)
    assert entries[0]["total_paper_trades"] is None
    assert entries[0]["realized_r_total"] is None
    assert summary["average_win_rate"] is None
    assert summary["aggregate_realized_r"] is None
    assert summary["aggregate_realized_pnl"] is None
    assert any("performance summary not found" in warning for warning in summary["warnings"])


def test_recurring_warnings_and_blocking_reasons(tmp_path: Path) -> None:
    for hour in (0, 1, 2):
        write_review_artifacts(
            tmp_path,
            review_generated_at=(NOW + timedelta(hours=hour)).isoformat(),
            review_warnings=["spread wide on XAU/USD"],
            review_blocking=["data quality below threshold"] if hour < 2 else [],
        )
        run_service(tmp_path, append_latest=True, now=NOW + timedelta(hours=hour))

    summary = run_service(tmp_path)

    assert {"message": "spread wide on XAU/USD", "count": 3} in summary["recurring_warnings"]
    assert {"message": "data quality below threshold", "count": 2} in summary["recurring_blocking_reasons"]


def test_unsafe_safety_flags_block_history(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path, review_safety_flags={"paper_demo_only": True, "order_send_called": True})

    summary = run_service(tmp_path, append_latest=True)

    assert summary["final_history_status"] == STATUS_BLOCKED
    assert any("unsafe safety flags detected" in reason for reason in summary["blocking_reasons"])
    assert summary["safety_flags_summary"]["unsafe_source_flags_detected"] == ["order_send_called"]


def test_blocked_latest_review_blocks_history(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path, review_status="PAPER_SESSION_REVIEW_BLOCKED")

    summary = run_service(tmp_path, append_latest=True)

    assert summary["final_history_status"] == STATUS_BLOCKED
    assert summary["latest_blocked_session"] is not None


def test_warn_latest_review_warns_history(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path, review_status="PAPER_SESSION_REVIEW_WARN")

    summary = run_service(tmp_path, append_latest=True)

    assert summary["final_history_status"] == STATUS_WARN


def test_json_and_txt_exports(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)

    summary = run_service(tmp_path, append_latest=True, export_json=True, export_txt=True)

    json_path = tmp_path / DEFAULT_HISTORY_JSON
    txt_path = tmp_path / DEFAULT_HISTORY_TXT
    assert json_path.is_file()
    assert txt_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["final_history_status"] == STATUS_READY
    assert payload["output_paths"]["summary_json"] == str(json_path)
    assert payload["output_paths"]["summary_txt"] == str(txt_path)
    text = txt_path.read_text(encoding="utf-8")
    assert text == render_history_txt(summary)
    for section in ("status counts:", "latest sessions:", "aggregates:", "recurring warnings:", "safety flags summary:", "output paths:"):
        assert section in text


def test_unreadable_jsonl_lines_are_skipped_with_warning(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    run_service(tmp_path, append_latest=True)
    with (tmp_path / DEFAULT_HISTORY_JSONL).open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    summary = run_service(tmp_path)

    assert summary["total_sessions"] == 1
    assert any("unreadable history line" in warning for warning in summary["warnings"])
    assert summary["final_history_status"] == STATUS_WARN


def test_invalid_session_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PaperSessionHistoryConfig(reports_dir=tmp_path, session_name="../escape")
    cli = load_cli_module()
    assert cli.main(["--reports-dir", str(tmp_path), "--append-latest", "--session-name", "../escape"]) == 2


def test_all_writes_stay_under_reports_dir(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_review_artifacts(reports_dir)
    before_outside = {path for path in tmp_path.iterdir()}

    run_service(reports_dir, append_latest=True, export_json=True, export_txt=True)

    assert {path for path in tmp_path.iterdir()} == before_outside
    written = {path.name for path in reports_dir.iterdir() if path.is_file()}
    assert {DEFAULT_HISTORY_JSONL, DEFAULT_HISTORY_JSON, DEFAULT_HISTORY_TXT} <= written


def test_source_reports_are_not_modified(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    sources = ["paper_session_review_summary.json", "paper_performance_summary.json", "operator_dashboard_summary.json"]
    before = {name: (tmp_path / name).read_bytes() for name in sources}

    run_service(tmp_path, append_latest=True, export_json=True, export_txt=True)

    after = {name: (tmp_path / name).read_bytes() for name in sources}
    assert before == after


def test_no_env_mutation_during_run(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_MODE=paper\n", encoding="utf-8")
    before_env = dict(os.environ)
    before_file = env_file.read_text(encoding="utf-8")

    run_service(tmp_path, append_latest=True, export_json=True, export_txt=True)

    assert dict(os.environ) == before_env
    assert env_file.read_text(encoding="utf-8") == before_file


def test_no_order_send_no_mt5_no_daemon_in_sources() -> None:
    for path in (MODULE_PATH, SCRIPT_PATH):
        source = path.read_text(encoding="utf-8").lower()
        assert "order_send(" not in source
        assert "import metatrader" not in source
        assert "import mt5" not in source
        assert "load_dotenv" not in source
        assert "set_key" not in source
        assert "while true" not in source


def test_cli_smoke_in_temp_reports_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reports_dir = tmp_path / "reports"
    write_review_artifacts(reports_dir)
    cli = load_cli_module()

    exit_code = cli.main(
        ["--reports-dir", str(reports_dir), "--append-latest", "--session-name", "paper-session-review", "--export-json", "--export-txt"]
    )

    assert exit_code == 0
    assert (reports_dir / DEFAULT_HISTORY_JSONL).is_file()
    assert (reports_dir / DEFAULT_HISTORY_JSON).is_file()
    assert (reports_dir / DEFAULT_HISTORY_TXT).is_file()
    output = capsys.readouterr().out
    assert "SAFETY" in output
    assert "final_history_status=" in output


def test_entry_safety_flags_recorded_from_review(tmp_path: Path) -> None:
    write_review_artifacts(tmp_path)
    entry, warnings = build_history_entry(tmp_path, now=NOW)

    assert entry is not None
    assert warnings == []
    assert entry["safety_flags"] == {"paper_demo_only": True, "order_send_called": False}
    appended, _ = append_history_entry(tmp_path, entry)
    assert appended is True


def test_summary_for_empty_history(tmp_path: Path) -> None:
    summary = build_history_summary([], reports_dir=tmp_path, now=NOW)

    assert summary["final_history_status"] == STATUS_EMPTY
    assert summary["total_sessions"] == 0
    assert summary["latest_session"] is None
    assert summary["aggregate_closed_trades"] == 0
    assert summary["average_win_rate"] is None
