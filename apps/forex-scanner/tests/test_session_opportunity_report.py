from __future__ import annotations

import json
from pathlib import Path

from app.reporting.session_opportunity import (
    SAFETY_WARNING,
    build_session_opportunity_report,
    collect_records,
    export_csv,
    filter_records,
    normalize_session,
)


def test_normalize_session_aliases() -> None:
    assert normalize_session("London_NewYork_Overlap") == "overlap"
    assert normalize_session("outside") == "off_hours"
    assert normalize_session("") == "off_hours"


def test_aggregation_and_off_hours_count() -> None:
    rows = [
        {"session": "london", "status": "approved", "score": 70, "risk_reward": 2.0, "spread_atr": 0.12, "asset_class": "forex"},
        {"session": "newyork", "status": "rejected", "score": 45, "risk_reward": 1.0, "spread_atr": 0.22, "asset_class": "forex"},
        {"session": "outside", "status": "detected", "score": 40, "risk_reward": 0.9, "spread_atr": 0.25, "asset_class": "indices"},
    ]
    report = build_session_opportunity_report(rows, top_n=5)
    assert report["total_records"] == 3
    assert report["signals_by_session"]["off_hours"] == 1
    assert report["off_hours_count"] == 1
    assert report["approved_by_session"]["london"] == 1
    assert report["rejected_by_session"]["newyork"] == 1
    assert report["safety_warning"] == SAFETY_WARNING


def test_missing_files_do_not_crash(tmp_path: Path) -> None:
    paths = {
        "signal_journal": tmp_path / "signal_journal.jsonl",
        "forward_test": tmp_path / "forward_test_paper.csv",
        "backtest_summary": tmp_path / "backtest_multi_asset_summary.json",
        "multi_asset_summary": tmp_path / "multi_asset_signal_report_summary.json",
    }
    records = collect_records(paths)
    assert records == []


def test_export_json_and_csv(tmp_path: Path) -> None:
    report = build_session_opportunity_report(
        [{"session": "london", "status": "approved", "score": 80, "risk_reward": 2.3, "spread_atr": 0.1, "asset_class": "forex"}],
        top_n=10,
    )
    json_path = tmp_path / "session_opportunity_summary.json"
    csv_path = tmp_path / "session_opportunity_report.csv"

    json_path.write_text(json.dumps(report), encoding="utf-8")
    export_csv(csv_path, report)

    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["total_records"] == 1
    assert csv_path.exists()
    assert "session,signals,approved,rejected" in csv_path.read_text(encoding="utf-8")


def test_filter_does_not_mutate_source_records() -> None:
    rows = [{"session": "london", "status": "approved", "symbol": "EURUSD", "asset_class": "forex"}]
    baseline = json.loads(json.dumps(rows))
    filtered = filter_records(rows, asset_class="forex", symbol="EURUSD")
    assert filtered
    assert rows == baseline
