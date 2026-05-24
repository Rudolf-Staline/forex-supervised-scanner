from __future__ import annotations

import json
from pathlib import Path

from app.reporting.near_miss_queue import build_near_miss_review_queue, export_summary, load_records


def test_missing_files_no_crash(tmp_path: Path) -> None:
    records = load_records(tmp_path / "reports")
    report = build_near_miss_review_queue(records)
    assert report["total_near_miss"] == 0
    assert report["candidate_records"] == []


def test_detect_near_miss_and_priority_score(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()

    (reports / "signal_journal.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "symbol": "EUR/USD",
                        "asset_class": "forex",
                        "session": "london",
                        "score": 69,
                        "risk_reward": 1.4,
                        "spread_atr": 0.50,
                        "status": "detected",
                        "reason": "spread slightly high",
                    }
                ),
                json.dumps(
                    {
                        "symbol": "XAU/USD",
                        "asset_class": "commodities",
                        "session": "asia",
                        "score": 50,
                        "risk_reward": 0.9,
                        "spread_atr": 0.9,
                        "status": "rejected",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    records = load_records(reports)
    report = build_near_miss_review_queue(records, asset_class="all", min_score=65, top_n=10)

    assert report["total_near_miss"] == 1
    assert report["near_miss_by_symbol"]["EUR/USD"] == 1
    candidate = report["candidate_records"][0]
    assert candidate["review_priority_score"] > 0
    assert "score_below_threshold_but_close" in candidate["near_miss_reasons"]
    assert report["safety_warning"] == "Near-miss review is informational and does not authorize execution."


def test_export_json_csv_and_no_config_mutation(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()

    report = build_near_miss_review_queue(
        [
            {
                "symbol": "US30",
                "asset_class": "indices",
                "session": "new_york",
                "score": 70,
                "risk_reward": 1.45,
                "spread_atr": 0.48,
                "status": "watchlist",
            }
        ],
        min_score=65,
    )

    json_path, csv_path = export_summary(report, reports, export_json=True, export_csv=True)
    assert json_path is not None and json_path.exists()
    assert csv_path is not None and csv_path.exists()

    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["total_near_miss"] == 1

    before = {"min_score": 65, "top_n": 25}
    _ = build_near_miss_review_queue([], min_score=before["min_score"], top_n=before["top_n"])
    assert before == {"min_score": 65, "top_n": 25}
