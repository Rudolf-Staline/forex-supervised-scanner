from __future__ import annotations

import json
from pathlib import Path

from app.reporting.watchlist_coverage import WatchlistCoverageOptions, build_watchlist_coverage_report, write_watchlist_coverage_csv


def test_missing_files_no_crash(tmp_path: Path) -> None:
    report = build_watchlist_coverage_report(WatchlistCoverageOptions(reports_dir=tmp_path))
    assert report["coverage_status"] == "NO_DATA"
    assert "signal_journal.jsonl" in report["input_files_missing_or_empty"]


def test_expected_vs_observed_and_coverage(tmp_path: Path) -> None:
    (tmp_path / "signal_journal.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"logical_symbol": "EUR/USD", "session": "london", "decision": "approved", "timestamp": "2026-05-20T10:00:00Z"}),
                json.dumps({"logical_symbol": "GBP/USD", "session": "new_york", "decision": "rejected", "timestamp": "2026-05-20T11:00:00Z"}),
                json.dumps({"logical_symbol": "GBP/USD", "session": "new_york", "decision": "rejected", "timestamp": "2026-05-20T12:00:00Z"}),
            ]
        ),
        encoding="utf-8",
    )
    report = build_watchlist_coverage_report(WatchlistCoverageOptions(reports_dir=tmp_path, asset_class="forex"))
    assert "EUR/USD" in report["observed_symbols"]
    assert "GBP/USD" in report["observed_symbols"]
    assert report["coverage_percentage"] > 0
    assert "GBP/USD" in report["symbols_with_repeated_rejections"]


def test_coverage_by_asset_class_and_exports(tmp_path: Path) -> None:
    (tmp_path / "signal_journal.jsonl").write_text(
        json.dumps({"logical_symbol": "EUR/USD", "session": "london", "decision": "approved", "timestamp": "2026-05-20T10:00:00Z"}),
        encoding="utf-8",
    )
    report = build_watchlist_coverage_report(WatchlistCoverageOptions(reports_dir=tmp_path, asset_class="all"))
    assert "forex" in report["coverage_by_asset_class"]
    out_json = tmp_path / "watchlist_coverage_summary.json"
    out_csv = tmp_path / "watchlist_coverage_report.csv"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_watchlist_coverage_csv(report, out_csv)
    assert out_json.exists()
    assert out_csv.exists()


def test_no_watchlist_mutation() -> None:
    from app.config.watchlists import WATCHLISTS

    before = list(WATCHLISTS["multi_asset_demo"])
    _ = build_watchlist_coverage_report(WatchlistCoverageOptions(reports_dir=Path(".")))
    after = list(WATCHLISTS["multi_asset_demo"])
    assert before == after
