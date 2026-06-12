from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.reporting.paper_performance import (
    STATUS_BLOCKED_UNSAFE_FLAGS,
    STATUS_INCOMPLETE_DATA,
    STATUS_NO_TRADES,
    STATUS_WARN,
    PaperPerformanceConfig,
    PaperPerformanceService,
)

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _write_base_reports(reports: Path, *, trades: list[dict] | None = None, safety_flags: dict | None = None) -> None:
    reports.mkdir(parents=True, exist_ok=True)
    flags = {"paper_demo_only": True, "order_send_called": False, "live_execution_allowed": False} | (safety_flags or {})
    (reports / "realtime_paper_positions.json").write_text(
        json.dumps(
            {
                "completed_at": NOW.isoformat(),
                "orders": trades or [],
                "safety_flags": flags,
                "warnings": [],
                "blocking_reasons": [],
            }
        ),
        encoding="utf-8",
    )
    (reports / "realtime_command_center_summary.json").write_text(
        json.dumps({"generated_at": NOW.isoformat(), "safety_flags": flags}), encoding="utf-8"
    )
    (reports / "realtime_paper_supervisor_summary.json").write_text(
        json.dumps({"completed_at": NOW.isoformat(), "safety_flags": flags}), encoding="utf-8"
    )
    (reports / "operator_dashboard_summary.json").write_text(
        json.dumps({"generated_at": NOW.isoformat(), "safety_flags": flags}), encoding="utf-8"
    )
    (reports / "realtime_heartbeat.jsonl").write_text(
        json.dumps({"heartbeat_at": NOW.isoformat(), "safety_flags": flags}) + "\n", encoding="utf-8"
    )


def _summary(reports: Path, *, strict: bool = False, export_json: bool = False, export_txt: bool = False):
    return PaperPerformanceService(
        PaperPerformanceConfig(reports_dir=reports, strict=strict, export_json=export_json, export_txt=export_txt, now=NOW)
    ).build_summary()


def test_no_reports_present_is_incomplete(tmp_path: Path) -> None:
    summary = _summary(tmp_path / "reports")
    assert summary.status == STATUS_INCOMPLETE_DATA
    assert summary.total_paper_trades == 0
    assert summary.missing_input_files
    assert summary.data_completeness_score < 1


def test_no_trades_present(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_base_reports(reports, trades=[])
    summary = _summary(reports)
    assert summary.status == STATUS_NO_TRADES
    assert summary.total_paper_trades == 0
    assert "input artifacts were found but no paper trades/orders were available" in summary.warnings


def test_closed_winning_losing_breakeven_trades_and_r_metrics(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_base_reports(
        reports,
        trades=[
            {"order_id": "w", "status": "fully_closed_trade", "symbol": "EUR/USD", "realized_r": 2.0, "realized_pnl": 200},
            {"order_id": "l", "status": "fully_closed_trade", "symbol": "GBP/USD", "realized_r": -1.0, "realized_pnl": -100},
            {"order_id": "b", "status": "closed", "symbol": "EUR/USD", "realized_r": 0.0, "realized_pnl": 0},
        ],
    )
    summary = _summary(reports)
    assert summary.closed_count == 3
    assert summary.win_count == 1
    assert summary.loss_count == 1
    assert summary.breakeven_count == 1
    assert summary.win_rate == round(1 / 3, 8)
    assert summary.realized_r_total == 1.0
    assert summary.average_r == round(1 / 3, 8)
    assert summary.best_r == 2.0
    assert summary.worst_r == -1.0
    assert summary.realized_pnl_total == 100.0
    assert summary.average_realized_pnl == round(100 / 3, 8)
    assert summary.symbols_traded == ["EUR/USD", "GBP/USD"]


def test_pending_open_closed_cancelled_counts(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_base_reports(
        reports,
        trades=[
            {"order_id": "p", "status": "pending"},
            {"order_id": "o", "status": "open_trade"},
            {"order_id": "c", "status": "closed", "realized_r": 1},
            {"order_id": "x", "status": "cancelled_trade"},
        ],
    )
    summary = _summary(reports)
    assert summary.pending_count == 1
    assert summary.open_count == 1
    assert summary.closed_count == 1
    assert summary.cancelled_count == 1
    assert summary.total_paper_trades == 4


def test_partial_stop_breakeven_trailing_events_and_time_in_trade(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_base_reports(
        reports,
        trades=[
            {
                "order_id": "events",
                "status": "closed",
                "realized_r": 1,
                "entry_timestamp": "2026-06-12T10:00:00+00:00",
                "closed_at": "2026-06-12T10:30:00+00:00",
                "partial_exits": [{"target": "tp1"}],
                "stop_movements": [{"reason": "breakeven"}, {"reason": "trailing stop"}],
                "events": [
                    {"event_type": "trade_partially_closed"},
                    {"event_type": "stop_moved", "reason": "breakeven"},
                    {"event_type": "stop_moved", "reason": "trailing"},
                ],
            }
        ],
    )
    summary = _summary(reports)
    assert summary.partial_exit_count == 1
    assert summary.stop_moved_count == 2
    assert summary.breakeven_event_count >= 1
    assert summary.trailing_stop_event_count >= 1
    assert summary.average_time_in_trade_seconds == 1800


def test_missing_incomplete_warnings_and_strict_mode(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "realtime_paper_positions.json").write_text(json.dumps({"completed_at": NOW.isoformat(), "positions_seen": 1}), encoding="utf-8")
    summary = _summary(reports)
    assert summary.status == STATUS_WARN or summary.status == STATUS_INCOMPLETE_DATA
    assert summary.missing_input_files
    assert any("aggregate counts but no order/trade records" in warning for warning in summary.warnings)
    strict = _summary(reports, strict=True)
    assert strict.status == STATUS_INCOMPLETE_DATA
    assert any("strict mode" in warning for warning in strict.warnings)


def test_unsafe_safety_flags_block_status(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_base_reports(reports, trades=[{"order_id": "x", "status": "closed", "realized_r": 1}], safety_flags={"live_execution_allowed": True})
    summary = _summary(reports)
    assert summary.status == STATUS_BLOCKED_UNSAFE_FLAGS
    assert any("unsafe" in reason for reason in summary.blocking_reasons)


def test_json_txt_exports_and_cli(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write_base_reports(reports, trades=[{"order_id": "x", "status": "closed", "realized_r": 1}])
    summary = _summary(reports, export_json=True, export_txt=True)
    assert (reports / "paper_performance_summary.json").exists()
    assert (reports / "paper_performance_report.txt").exists()
    payload = json.loads((reports / "paper_performance_summary.json").read_text(encoding="utf-8"))
    assert payload["output_paths"]["json"].endswith("paper_performance_summary.json")
    assert summary.output_paths["txt"].endswith("paper_performance_report.txt")

    result = subprocess.run(
        [sys.executable, "scripts/paper_performance_report.py", "--reports-dir", str(reports), "--export-json", "--export-txt"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "paper_performance=diagnostic_only" in result.stdout


def test_no_mt5_import_no_order_submission_no_env_mutation() -> None:
    module_path = Path(__file__).resolve().parents[1] / "app" / "reporting" / "paper_performance.py"
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "paper_performance_report.py"
    source = module_path.read_text(encoding="utf-8") + script_path.read_text(encoding="utf-8")
    assert "MetaTrader5" not in source
    assert "order_send(" not in source
    assert ".order_send" not in source

    before = dict(os.environ)
    _summary(Path("/tmp/nonexistent-paper-performance-reports"))
    assert dict(os.environ) == before
