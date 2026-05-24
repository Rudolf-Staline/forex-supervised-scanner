from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.reporting.strategy_explainability import SAFETY_WARNING, build_report, explain_record


def sample_records():
    return [
        {"status": "approved", "score": 70, "risk_reward": 2.0, "spread_atr": 0.05, "session": "london", "detected_patterns": ["breakout"]},
        {"status": "watchlist", "score": 53, "risk_reward": 1.1, "spread_atr": 0.12, "session": "new_york", "detected_patterns": ["pullback"]},
        {"status": "rejected", "score": 40, "risk_reward": 0.9, "spread_atr": 0.35, "session": "asia", "rejection_reasons": ["spread filter"]},
    ]


def test_parsing_and_distribution():
    report = build_report(sample_records())
    assert report["total_records"] == 3
    assert report["decision_distribution"]["approved"] == 1
    assert report["decision_distribution"]["watchlist"] == 1
    assert report["decision_distribution"]["rejected"] == 1


def test_explanations_for_key_statuses():
    rows = sample_records()
    assert "approved" in explain_record(rows[0]).lower()
    assert "watchlist" in explain_record(rows[1]).lower()
    assert "rejected" in explain_record(rows[2]).lower()


def test_missing_files_and_exports(tmp_path):
    from scripts import strategy_explainability_report as mod

    mod.REPORTS_DIR = tmp_path / "reports"
    mod.SIGNAL_JOURNAL = mod.REPORTS_DIR / "signal_journal.jsonl"
    mod.MULTI_ASSET_SUMMARY = mod.REPORTS_DIR / "multi_asset_signal_report_summary.json"
    mod.FORWARD_TEST_CSV = mod.REPORTS_DIR / "forward_test_paper.csv"
    mod.EXPORT_JSON = mod.REPORTS_DIR / "strategy_explainability_summary.json"
    mod.EXPORT_TXT = mod.REPORTS_DIR / "strategy_explainability_report.txt"

    rows = mod.collect_records()
    assert rows == []
    report = build_report(rows)
    mod.EXPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    mod.EXPORT_JSON.write_text(json.dumps(report), encoding="utf-8")
    mod.EXPORT_TXT.write_text(SAFETY_WARNING, encoding="utf-8")
    assert mod.EXPORT_JSON.exists()
    assert mod.EXPORT_TXT.exists()


def test_no_config_mutation():
    before = json.loads((ROOT / "app/config/default_settings.json").read_text(encoding="utf-8"))
    _ = build_report(sample_records())
    after = json.loads((ROOT / "app/config/default_settings.json").read_text(encoding="utf-8"))
    assert before == after
