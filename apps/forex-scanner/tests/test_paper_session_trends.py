from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

import pytest

from app.reporting.paper_session_trends import (
    DEFAULT_HISTORY_JSONL,
    DEFAULT_TRENDS_JSON,
    DEFAULT_TRENDS_TXT,
    STATUS_BLOCKED,
    STATUS_EMPTY,
    STATUS_READY,
    STATUS_WARN,
    PaperSessionTrendsConfig,
    PaperSessionTrendsService,
    build_trends_summary,
    load_history_entries,
    render_trends_txt,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "paper_session_trends.py"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "reporting" / "paper_session_trends.py"
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def load_cli_module():
    spec = importlib.util.spec_from_file_location("paper_session_trends_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_entry(
    index: int,
    *,
    status: str = "PAPER_SESSION_REVIEW_READY",
    warnings: list[str] | None = None,
    blocking: list[str] | None = None,
    win_rate: float | None = 0.5,
    realized_r: float | None = 1.0,
    pnl: float | None = 100.0,
    symbols: list[str] | None = None,
    closed: int = 3,
    wins: int = 1,
    losses: int = 1,
    breakevens: int = 1,
    drawdown: float | None = -10.0,
    flags: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "recorded_at": f"2026-01-{index:02d}T00:00:00+00:00",
        "session_name": f"s{index}",
        "review_generated_at": f"2026-01-{index:02d}T00:01:00+00:00",
        "final_review_status": status,
        "closed_count": closed,
        "win_count": wins,
        "loss_count": losses,
        "breakeven_count": breakevens,
        "win_rate": win_rate,
        "realized_r_total": realized_r,
        "realized_pnl_total": pnl,
        "max_drawdown": drawdown,
        "symbols_traded": symbols or ["EURUSD"],
        "warnings": warnings or [],
        "blocking_reasons": blocking or [],
        "safety_flags": flags if flags is not None else {"paper_demo_only": True, "order_send_called": False},
    }


def write_history(reports_dir: Path, entries: list[dict[str, object]]) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_HISTORY_JSONL
    path.write_text("".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries), encoding="utf-8")


def run_service(reports_dir: Path, **kwargs: object) -> dict[str, object]:
    return PaperSessionTrendsService(PaperSessionTrendsConfig(reports_dir=reports_dir, now=NOW, **kwargs)).run()


def test_missing_history_file_returns_empty_status(tmp_path: Path) -> None:
    summary = run_service(tmp_path)

    assert summary["final_trends_status"] == STATUS_EMPTY
    assert summary["history_state"] == "missing"
    assert summary["total_sessions_analyzed"] == 0


def test_empty_history_file_returns_empty_status(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / DEFAULT_HISTORY_JSONL).write_text("\n", encoding="utf-8")

    summary = run_service(tmp_path)

    assert summary["final_trends_status"] == STATUS_EMPTY
    assert summary["history_state"] == "empty"


def test_corrupt_jsonl_line_handling(tmp_path: Path) -> None:
    write_history(tmp_path, [make_entry(1)])
    with (tmp_path / DEFAULT_HISTORY_JSONL).open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    entries, warnings, state = load_history_entries(tmp_path)
    summary = run_service(tmp_path)

    assert state == "loaded"
    assert len(entries) == 1
    assert any("unreadable history line" in warning for warning in warnings)
    assert summary["final_trends_status"] == STATUS_WARN
    assert summary["total_sessions_analyzed"] == 1


def test_valid_history_analysis_and_status_counts(tmp_path: Path) -> None:
    entries = [
        make_entry(1, status="PAPER_SESSION_REVIEW_WARN", warnings=["late report"], win_rate=0.4, realized_r=-1, pnl=-50, symbols=["EURUSD"]),
        make_entry(2, status="PAPER_SESSION_REVIEW_READY", win_rate=0.6, realized_r=2, pnl=150, symbols=["EURUSD", "GBPUSD"]),
    ]
    write_history(tmp_path, entries)

    summary = run_service(tmp_path)

    assert summary["final_trends_status"] == STATUS_READY
    assert summary["latest_final_review_status"] == "PAPER_SESSION_REVIEW_READY"
    assert summary["status_counts"] == {"PAPER_SESSION_REVIEW_READY": 1, "PAPER_SESSION_REVIEW_WARN": 1}
    assert summary["total_closed_trades"] == 6
    assert summary["win_total"] == 2
    assert summary["loss_total"] == 2
    assert summary["breakeven_total"] == 2
    assert summary["average_win_rate"] == 0.5
    assert summary["aggregate_realized_r"] == 1.0
    assert summary["aggregate_realized_pnl"] == 100.0
    assert summary["max_drawdown_worst"] == -10.0


def test_window_limiting(tmp_path: Path) -> None:
    write_history(tmp_path, [make_entry(1), make_entry(2), make_entry(3)])

    summary = run_service(tmp_path, window=2)

    assert summary["analysis_window_size"] == 2
    assert summary["total_sessions_available"] == 3
    assert summary["total_sessions_analyzed"] == 2
    assert summary["latest_session"]["session_name"] == "s3"


@pytest.mark.parametrize(
    ("statuses", "direction"),
    [
        (["PAPER_SESSION_REVIEW_BLOCKED", "PAPER_SESSION_REVIEW_WARN", "PAPER_SESSION_REVIEW_READY"], "improving"),
        (["PAPER_SESSION_REVIEW_READY", "PAPER_SESSION_REVIEW_WARN", "PAPER_SESSION_REVIEW_BLOCKED"], "degrading"),
        (["PAPER_SESSION_REVIEW_WARN", "PAPER_SESSION_REVIEW_WARN"], "stable"),
        (["PAPER_SESSION_REVIEW_READY", "PAPER_SESSION_REVIEW_BLOCKED", "PAPER_SESSION_REVIEW_WARN"], "mixed"),
    ],
)
def test_status_trend_improving_degrading_stable_mixed(tmp_path: Path, statuses: list[str], direction: str) -> None:
    write_history(tmp_path, [make_entry(index + 1, status=status) for index, status in enumerate(statuses)])

    summary = run_service(tmp_path)

    assert summary["status_direction"] == direction
    assert summary["status_trend"]["direction"] == direction


def test_recurring_warnings_and_blocking_reasons(tmp_path: Path) -> None:
    entries = [
        make_entry(1, warnings=["late report", "thin sample"], blocking=["missing dashboard"]),
        make_entry(2, warnings=["late report"], blocking=["missing dashboard"]),
        make_entry(3, warnings=["other"], blocking=["different"]),
    ]
    write_history(tmp_path, entries)

    summary = run_service(tmp_path)

    assert {item["message"]: item["count"] for item in summary["recurring_warnings"]} == {"late report": 2}
    assert {item["message"]: item["count"] for item in summary["recurring_blocking_reasons"]} == {"missing dashboard": 2}


def test_latest_new_and_resolved_warnings_and_blocking_reasons(tmp_path: Path) -> None:
    entries = [
        make_entry(1, warnings=["old warning", "shared warning"], blocking=["old block", "shared block"]),
        make_entry(2, warnings=["shared warning", "new warning"], blocking=["shared block", "new block"]),
    ]
    write_history(tmp_path, entries)

    summary = run_service(tmp_path)

    assert summary["new_warnings_latest"] == ["new warning"]
    assert summary["new_blocking_reasons_latest"] == ["new block"]
    assert summary["resolved_warnings"] == ["old warning"]
    assert summary["resolved_blocking_reasons"] == ["old block"]


def test_win_rate_and_realized_r_trends(tmp_path: Path) -> None:
    write_history(
        tmp_path,
        [
            make_entry(1, win_rate=0.2, realized_r=3),
            make_entry(2, win_rate=0.4, realized_r=2),
            make_entry(3, win_rate=0.6, realized_r=1),
        ],
    )

    summary = run_service(tmp_path)

    assert summary["win_rate_trend"] == "improving"
    assert summary["realized_r_trend"] == "degrading"


def test_aggregate_realized_r_pnl_and_symbol_concentration(tmp_path: Path) -> None:
    write_history(
        tmp_path,
        [
            make_entry(1, realized_r=1.5, pnl=10, symbols=["EURUSD", "GBPUSD"]),
            make_entry(2, realized_r=2.5, pnl=20, symbols=["EURUSD", "USDJPY"]),
        ],
    )

    summary = run_service(tmp_path)

    assert summary["aggregate_realized_r"] == 4.0
    assert summary["aggregate_realized_pnl"] == 30.0
    assert summary["distinct_symbols_traded"] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert summary["symbol_concentration"][0] == {"symbol": "EURUSD", "count": 2, "ratio": 0.5}


def test_unsafe_safety_flags_cause_blocked_status(tmp_path: Path) -> None:
    write_history(tmp_path, [make_entry(1, flags={"paper_demo_only": True, "order_send_called": True})])

    summary = run_service(tmp_path)

    assert summary["final_trends_status"] == STATUS_BLOCKED
    assert summary["unsafe_flag_detections"] == ["order_send_called"]
    assert any("unsafe safety flags" in reason for reason in summary["blocking_reasons"])


def test_json_and_txt_exports(tmp_path: Path) -> None:
    write_history(tmp_path, [make_entry(1)])

    summary = run_service(tmp_path, export_json=True, export_txt=True)

    json_path = tmp_path / DEFAULT_TRENDS_JSON
    txt_path = tmp_path / DEFAULT_TRENDS_TXT
    assert json_path.is_file()
    assert txt_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["final_trends_status"] == STATUS_READY
    assert payload["output_paths"]["summary_json"] == str(json_path)
    assert payload["output_paths"]["summary_txt"] == str(txt_path)
    assert txt_path.read_text(encoding="utf-8") == render_trends_txt(summary)


def test_strict_mode_behavior(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli = load_cli_module()

    assert cli.main(["--reports-dir", str(tmp_path), "--strict"]) == 1
    output = capsys.readouterr().out
    assert "PAPER_SESSION_TRENDS_EMPTY" in output

    write_history(tmp_path, [make_entry(1)])
    assert cli.main(["--reports-dir", str(tmp_path), "--strict"]) == 0


def test_cli_smoke_in_temp_reports_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reports_dir = tmp_path / "reports"
    write_history(reports_dir, [make_entry(1)])
    cli = load_cli_module()

    exit_code = cli.main(["--reports-dir", str(reports_dir), "--window", "5", "--export-json", "--export-txt"])

    assert exit_code == 0
    assert (reports_dir / DEFAULT_TRENDS_JSON).is_file()
    assert (reports_dir / DEFAULT_TRENDS_TXT).is_file()
    output = capsys.readouterr().out
    assert "SAFETY" in output
    assert "final_trends_status=" in output


def test_no_mt5_import_no_order_send_no_env_mutation_no_daemon() -> None:
    for path in (MODULE_PATH, SCRIPT_PATH):
        source = path.read_text(encoding="utf-8").lower()
        assert "order_send(" not in source
        assert "import metatrader" not in source
        assert "import mt5" not in source
        assert "load_dotenv" not in source
        assert "set_key" not in source
        assert "while true" not in source
        assert "schedule.every" not in source


def test_no_env_mutation_during_run(tmp_path: Path) -> None:
    write_history(tmp_path, [make_entry(1)])
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_MODE=paper\n", encoding="utf-8")
    before_env = dict(os.environ)
    before_file = env_file.read_text(encoding="utf-8")

    run_service(tmp_path, export_json=True, export_txt=True)

    assert dict(os.environ) == before_env
    assert env_file.read_text(encoding="utf-8") == before_file


def test_source_history_is_not_modified(tmp_path: Path) -> None:
    write_history(tmp_path, [make_entry(1)])
    path = tmp_path / DEFAULT_HISTORY_JSONL
    before = path.read_bytes()

    run_service(tmp_path, export_json=True, export_txt=True)

    assert path.read_bytes() == before


def test_invalid_window_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PaperSessionTrendsConfig(reports_dir=tmp_path, window=0)
    cli = load_cli_module()
    assert cli.main(["--reports-dir", str(tmp_path), "--window", "0"]) == 2


def test_build_summary_direct_for_empty_history(tmp_path: Path) -> None:
    summary = build_trends_summary([], reports_dir=tmp_path, now=NOW)

    assert summary["final_trends_status"] == STATUS_EMPTY
    assert summary["latest_session"] is None
    assert summary["win_rate_trend"] == "insufficient_data"
    assert summary["realized_r_trend"] == "insufficient_data"
