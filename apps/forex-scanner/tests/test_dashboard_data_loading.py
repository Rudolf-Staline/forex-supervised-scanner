from __future__ import annotations

import json

from app.dashboard.data_loader import load_dashboard_data


def test_load_dashboard_data_handles_missing_files(tmp_path):
    data = load_dashboard_data(tmp_path)
    assert data.signal_journal == []
    assert data.signal_report_summary == {}
    assert data.backtest_summary == {}
    assert data.threshold_optimizer_summary == {}


def test_load_dashboard_data_reads_expected_reports(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    (reports / "signal_journal.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"symbol": "EURUSD", "score": 0.72, "status": "approved"}),
                json.dumps({"symbol": "XAUUSD", "score": 0.61, "status": "near_miss"}),
            ]
        ),
        encoding="utf-8",
    )
    (reports / "multi_asset_signal_report_summary.json").write_text(
        json.dumps({"current_mode": "paper", "ALLOW_LIVE_TRADING": False}),
        encoding="utf-8",
    )
    (reports / "backtest_multi_asset_summary.json").write_text(
        json.dumps({"trades": 15, "win_rate": 0.56}),
        encoding="utf-8",
    )
    (reports / "threshold_optimizer_summary.json").write_text(
        json.dumps({"best_threshold": 0.65}),
        encoding="utf-8",
    )

    data = load_dashboard_data(tmp_path)

    assert len(data.signal_journal) == 2
    assert data.signal_report_summary["current_mode"] == "paper"
    assert data.backtest_summary["trades"] == 15
    assert data.threshold_optimizer_summary["best_threshold"] == 0.65
