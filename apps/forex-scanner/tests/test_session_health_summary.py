from __future__ import annotations

import os
from pathlib import Path

from app.reporting.session_health import (
    SAFETY_WARNING,
    BLOCKED_REASON,
    build_session_health_summary,
    collect_session_health_records,
    export_session_health_csv,
    export_session_health_json,
)


def test_builds_health_summary_with_blocked_off_hours() -> None:
    rows = [
        {"session": "london", "status": "approved", "score": 82, "spread_atr": 0.10, "asset_class": "forex"},
        {"session": "london", "status": "approved", "score": 74, "spread_atr": 0.12, "asset_class": "forex"},
        {"session": "newyork", "status": "rejected", "score": 44, "spread_atr": 0.24, "asset_class": "indices"},
        {"session": "outside", "status": "detected", "score": 39, "spread_atr": 0.30, "asset_class": "commodities"},
    ]

    summary = build_session_health_summary(rows, top_n=3)

    assert summary["total_records"] == 4
    assert summary["safety_warning"] == SAFETY_WARNING
    assert summary["subprocess_used"] is False
    assert summary["mt5_called"] is False
    assert summary["env_mutation_performed"] is False
    assert summary["recommended_observation_windows"] == ["london"]
    assert "off_hours" in summary["blocked_sessions"]

    by_session = {item["session"]: item for item in summary["sessions"]}
    assert by_session["london"]["status"] == "HEALTHY"
    assert by_session["off_hours"]["status"] == "BLOCKED"
    assert by_session["off_hours"]["recommendation"] == BLOCKED_REASON


def test_empty_inputs_block_instead_of_becoming_permissive() -> None:
    summary = build_session_health_summary([])

    assert summary["overall_status"] == "BLOCKED"
    assert summary["recommended_observation_windows"] == []
    assert summary["blocked_sessions"] == []


def test_collect_records_filters_without_mutating_environment(tmp_path: Path) -> None:
    paths = {
        "signal_journal": tmp_path / "signal_journal.jsonl",
        "forward_test": tmp_path / "forward_test_paper.csv",
        "backtest_summary": tmp_path / "backtest_multi_asset_summary.json",
        "multi_asset_summary": tmp_path / "multi_asset_signal_report_summary.json",
    }
    paths["signal_journal"].write_text(
        '{"session":"london","status":"approved","symbol":"EURUSD","asset_class":"forex"}\n'
        '{"session":"newyork","status":"approved","symbol":"US30","asset_class":"indices"}\n',
        encoding="utf-8",
    )
    before = dict(os.environ)

    records = collect_session_health_records(paths, asset_class="forex", symbol="EURUSD")

    assert len(records) == 1
    assert records[0]["symbol"] == "EURUSD"
    assert dict(os.environ) == before


def test_exports_json_and_csv(tmp_path: Path) -> None:
    summary = build_session_health_summary(
        [{"session": "london", "status": "approved", "score": 80, "spread_atr": 0.1, "asset_class": "forex"}]
    )

    json_path = export_session_health_json(summary, tmp_path / "session_health_summary.json")
    csv_path = export_session_health_csv(summary, tmp_path / "session_health_summary.csv")

    assert json_path.exists()
    assert csv_path.exists()
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "session,status,signals" in csv_text
    assert "london,HEALTHY" in csv_text
