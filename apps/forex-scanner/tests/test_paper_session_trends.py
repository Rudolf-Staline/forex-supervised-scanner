"""Tests for paper session trend analysis."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.reporting.paper_session_history import DEFAULT_HISTORY_JSONL
from app.reporting.paper_session_trends import (
    DEFAULT_TRENDS_JSON,
    DEFAULT_TRENDS_TXT,
    STATUS_BLOCKED,
    STATUS_EMPTY,
    STATUS_READY,
    STATUS_WARN,
    PaperSessionTrendsConfig,
    PaperSessionTrendsService,
    build_trends_summary,
    render_trends_txt,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "paper_session_trends.py"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "reporting" / "paper_session_trends.py"
NOW = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)


def load_cli_module():
    spec = importlib.util.spec_from_file_location("paper_session_trends_cli", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def entry(
    idx: int,
    *,
    status: str = "PAPER_SESSION_REVIEW_READY",
    win_rate: float | None = 0.5,
    realized_r: float | None = 1.0,
    realized_pnl: float | None = 10.0,
    symbol: str = "EUR/USD",
    warning: str | None = None,
    blocking: str | None = None,
    unsafe: bool = False,
) -> dict[str, object]:
    return {
        "recorded_at": f"2026-06-13T12:0{idx}:00+00:00",
        "session_name": f"s{idx}",
        "review_generated_at": f"2026-06-13T12:0{idx}:00+00:00",
        "final_review_status": status,
        "operator_status": "OPERATOR_READY_FOR_PAPER_REVIEW",
        "performance_status": "PAPER_PERFORMANCE_READY",
        "bundle_status": "EXPORTED",
        "total_paper_trades": 3,
        "closed_count": 2,
        "win_count": 1,
        "loss_count": 1,
        "breakeven_count": 0,
        "win_rate": win_rate,
        "realized_r_total": realized_r,
        "average_r": None if realized_r is None else realized_r / 2,
        "realized_pnl_total": realized_pnl,
        "max_drawdown": -float(idx),
        "symbols_traded": [symbol],
        "blocking_reasons": [] if blocking is None else [blocking],
        "warnings": [] if warning is None else [warning],
        "safety_flags": {"order_send_called": unsafe, "paper_demo_only": True},
        "source_paths": {"history": "reports/paper_session_history.jsonl"},
    }


def write_history(reports_dir: Path, entries: list[dict[str, object]], *, corrupt: bool = False) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(item, sort_keys=True) for item in entries]
    if corrupt:
        lines.insert(1, "{not-json")
    (reports_dir / DEFAULT_HISTORY_JSONL).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_missing_history_returns_empty_status(tmp_path: Path) -> None:
    summary = PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=tmp_path / "reports", now=NOW)).run()

    assert summary["final_trends_status"] == STATUS_EMPTY
    assert summary["total_sessions_analyzed"] == 0
    assert any("history ledger not found" in warning for warning in summary["warnings"])


def test_empty_history_file_returns_empty_status(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / DEFAULT_HISTORY_JSONL).write_text("", encoding="utf-8")

    summary = PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, now=NOW)).run()

    assert summary["final_trends_status"] == STATUS_EMPTY
    assert summary["status_trend"] == "insufficient_data"


def test_corrupt_jsonl_line_is_warning_not_crash(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(reports_dir, [entry(1), entry(2)], corrupt=True)

    summary = PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, now=NOW)).run()

    assert summary["total_sessions_analyzed"] == 2
    assert summary["final_trends_status"] == STATUS_WARN
    assert any("unreadable history line" in warning for warning in summary["warnings"])


def test_valid_history_analysis_and_exports(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(reports_dir, [entry(1, symbol="EUR/USD"), entry(2, symbol="GBP/USD")])

    summary = PaperSessionTrendsService(
        PaperSessionTrendsConfig(reports_dir=reports_dir, export_json=True, export_txt=True, now=NOW)
    ).run()

    assert summary["final_trends_status"] == STATUS_READY
    assert summary["aggregate_closed_trades"] == 4
    assert summary["aggregate_wins"] == 2
    assert summary["aggregate_losses"] == 2
    assert summary["average_win_rate"] == 0.5
    assert summary["aggregate_realized_r"] == 2.0
    assert summary["aggregate_realized_pnl"] == 20.0
    assert summary["worst_max_drawdown"] == -2.0
    assert summary["distinct_symbols_traded"] == ["EUR/USD", "GBP/USD"]
    assert (reports_dir / DEFAULT_TRENDS_JSON).is_file()
    assert (reports_dir / DEFAULT_TRENDS_TXT).is_file()
    assert "PAPER SESSION TRENDS" in render_trends_txt(summary)


def test_window_limiting_and_status_counts(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(
        reports_dir,
        [
            entry(1, status="PAPER_SESSION_REVIEW_BLOCKED"),
            entry(2, status="PAPER_SESSION_REVIEW_WARN"),
            entry(3, status="PAPER_SESSION_REVIEW_READY"),
        ],
    )

    summary = PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, window=2, now=NOW)).run()

    assert summary["total_available_sessions"] == 3
    assert summary["total_sessions_analyzed"] == 2
    assert summary["status_counts"] == {"PAPER_SESSION_REVIEW_READY": 1, "PAPER_SESSION_REVIEW_WARN": 1}
    assert summary["latest_final_review_status"] == "PAPER_SESSION_REVIEW_READY"


def test_status_trend_labels() -> None:
    assert build_trends_summary(
        [entry(1, status="PAPER_SESSION_REVIEW_INCOMPLETE"), entry(2, status="PAPER_SESSION_REVIEW_WARN"), entry(3)],
        reports_dir=Path("reports"),
        now=NOW,
    )["status_trend"] == "improving"
    assert build_trends_summary(
        [entry(1), entry(2, status="PAPER_SESSION_REVIEW_WARN"), entry(3, status="PAPER_SESSION_REVIEW_BLOCKED")],
        reports_dir=Path("reports"),
        now=NOW,
    )["status_trend"] == "degrading"
    assert build_trends_summary([entry(1), entry(2)], reports_dir=Path("reports"), now=NOW)["status_trend"] == "stable"


def test_recurring_and_latest_warning_deltas(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(
        reports_dir,
        [
            entry(1, warning="spread high", blocking="data stale"),
            entry(2, warning="spread high", blocking="data stale"),
            entry(3, warning="latency high", blocking="policy blocked"),
        ],
    )

    summary = PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, now=NOW)).run()

    assert summary["recurring_warnings"] == [{"message": "spread high", "count": 2}]
    assert summary["recurring_blocking_reasons"] == [{"message": "data stale", "count": 2}]
    assert summary["new_warnings_latest"] == ["latency high"]
    assert summary["new_blocking_reasons_latest"] == ["policy blocked"]
    assert "spread high" in summary["resolved_warnings_latest"]
    assert "data stale" in summary["resolved_blocking_reasons_latest"]


def test_numeric_trends_and_symbol_concentration(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(
        reports_dir,
        [
            entry(1, win_rate=0.3, realized_r=-1.0, symbol="EUR/USD"),
            entry(2, win_rate=0.5, realized_r=0.0, symbol="EUR/USD"),
            entry(3, win_rate=0.7, realized_r=2.0, symbol="GBP/USD"),
        ],
    )

    summary = PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, now=NOW)).run()

    assert summary["win_rate_trend"] == "improving"
    assert summary["realized_r_trend"] == "improving"
    assert summary["symbol_concentration"][0] == {"symbol": "EUR/USD", "count": 2, "ratio": 0.66666667}


def test_unsafe_safety_flags_block_trends(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(reports_dir, [entry(1), entry(2, unsafe=True)])

    summary = PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, now=NOW)).run()

    assert summary["final_trends_status"] == STATUS_BLOCKED
    assert summary["unsafe_flag_detections"] == ["order_send_called"]
    assert any("unsafe safety flags" in reason for reason in summary["blocking_reasons"])


def test_strict_cli_returns_non_zero_for_empty_history(tmp_path: Path) -> None:
    cli = load_cli_module()

    result = cli.main(["--reports-dir", str(tmp_path / "reports"), "--strict"])

    assert result == 1


def test_cli_smoke_exports_from_temp_history(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(reports_dir, [entry(1), entry(2)])
    cli = load_cli_module()

    result = cli.main(["--reports-dir", str(reports_dir), "--window", "2", "--export-json", "--export-txt"])

    assert result == 0
    assert (reports_dir / DEFAULT_TRENDS_JSON).is_file()
    assert (reports_dir / DEFAULT_TRENDS_TXT).is_file()


def test_invalid_window_returns_cli_error(tmp_path: Path) -> None:
    cli = load_cli_module()

    assert cli.main(["--reports-dir", str(tmp_path / "reports"), "--window", "0"]) == 2


def test_no_order_send_no_terminal_import_no_env_mutation_in_sources() -> None:
    for path in (MODULE_PATH, SCRIPT_PATH):
        source = path.read_text(encoding="utf-8").lower()
        assert "order_send(" not in source
        assert "import metatrader" not in source
        assert "import mt5" not in source
        assert "load_dotenv" not in source
        assert "set_key" not in source
        assert "while true" not in source


def test_no_env_mutation_and_source_history_not_modified(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    write_history(reports_dir, [entry(1), entry(2)])
    history_path = reports_dir / DEFAULT_HISTORY_JSONL
    before_history = history_path.read_bytes()
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_MODE=paper\n", encoding="utf-8")
    before_env = dict(os.environ)
    before_env_file = env_file.read_text(encoding="utf-8")

    PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, export_json=True, export_txt=True, now=NOW)).run()

    assert history_path.read_bytes() == before_history
    assert dict(os.environ) == before_env
    assert env_file.read_text(encoding="utf-8") == before_env_file
