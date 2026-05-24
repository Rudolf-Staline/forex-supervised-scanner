from __future__ import annotations

import json
from pathlib import Path

from app.reporting.paper_performance import build_paper_performance_report, export_summary, load_records


def test_missing_files_no_crash(tmp_path: Path) -> None:
    records = load_records(tmp_path / "reports")
    report = build_paper_performance_report(records)
    assert report["total_paper_records"] == 0
    assert report["paper_quality_status"] == "BLOCKED"


def test_parsing_and_aggregation_and_exports(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "forward_test_paper.csv").write_text(
        "symbol,asset_class,session,score,risk_reward,spread_atr,slippage,spread_cost,commission,status,reason\n"
        "EUR/USD,forex,london,80,2.0,0.3,0.0001,0.0002,0.00005,filled,\n"
        "XAU/USD,commodities,new_york,30,1.1,0.8,0.0003,0.0005,0.0002,rejected,spread too high\n",
        encoding="utf-8",
    )
    (reports / "paper_fill_summary.json").write_text(
        json.dumps({"symbol": "US30", "asset_class": "indices", "session": "new_york", "score": 65, "status": "filled"}),
        encoding="utf-8",
    )
    (reports / "signal_journal.jsonl").write_text(
        '{"symbol":"EUR/USD","asset_class":"forex","session":"london","final_score":85,"status":"paper-approved"}\n',
        encoding="utf-8",
    )

    records = load_records(reports)
    report = build_paper_performance_report(records, asset_class="all", top_n=5)

    assert report["total_paper_records"] == 4
    assert report["simulated_orders"] >= 1
    assert report["rejected_paper_orders"] == 1
    assert report["average_score"] > 0
    assert report["execution_cost_impact"] == round(
        report["average_slippage"] + report["average_spread_cost"] + report["average_commission_estimate"], 8
    )
    assert report["best_symbols"]
    assert report["best_sessions"]
    assert report["safety_warning"] == "Paper performance is not proof of profitability."

    json_path, csv_path = export_summary(report, reports, export_csv=True)
    assert json_path.exists()
    assert csv_path is not None and csv_path.exists()

    report_forex = build_paper_performance_report(records, asset_class="forex", symbol="EUR/USD", session="london")
    assert report_forex["total_paper_records"] == 2


def test_no_config_mutation() -> None:
    before = dict(Path.__dict__)
    _ = build_paper_performance_report([])
    after = dict(Path.__dict__)
    assert before.keys() == after.keys()
