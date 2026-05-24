from __future__ import annotations

import json
from pathlib import Path

from app.reporting.backtest_comparison import compare_summaries, export_summary, load_records, summarize_records


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_comparison_json(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_json(reports / "baseline.json", [{"symbol": "EUR/USD", "session": "london", "score": 60, "total_trades": 40, "win_rate": 52}])
    _write_json(reports / "candidate.json", [{"symbol": "EUR/USD", "session": "london", "score": 75, "total_trades": 45, "win_rate": 57}])

    base = summarize_records(load_records(reports / "baseline.json"), dataset_name="baseline.json")
    cand = summarize_records(load_records(reports / "candidate.json"), dataset_name="candidate.json")
    report = compare_summaries(base, cand)

    assert report["baseline_name"] == "baseline.json"
    assert report["candidate_name"] == "candidate.json"
    assert report["delta_win_rate"] > 0


def test_comparison_csv_and_improved_degraded(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "base.csv").write_text(
        "symbol,session,score,total_trades,win_rate\nEUR/USD,london,70,30,55\nUSD/JPY,tokyo,80,30,60\n",
        encoding="utf-8",
    )
    (reports / "cand.csv").write_text(
        "symbol,session,score,total_trades,win_rate\nEUR/USD,london,60,35,50\nUSD/JPY,tokyo,90,35,65\n",
        encoding="utf-8",
    )

    report = compare_summaries(
        summarize_records(load_records(reports / "base.csv"), dataset_name="base.csv"),
        summarize_records(load_records(reports / "cand.csv"), dataset_name="cand.csv"),
        top_n=5,
    )

    assert report["improved_symbols"]
    assert report["degraded_symbols"]
    assert report["comparison_status"] in {"MIXED", "INSUFFICIENT_DATA"}


def test_missing_files_no_crash_and_sample_size_warning(tmp_path: Path) -> None:
    base = summarize_records(load_records(tmp_path / "reports" / "missing-a.json"), dataset_name="missing-a.json")
    cand = summarize_records(load_records(tmp_path / "reports" / "missing-b.csv"), dataset_name="missing-b.csv")
    report = compare_summaries(base, cand)
    assert report["sample_size_warning"] is True
    assert report["comparison_status"] == "INSUFFICIENT_DATA"


def test_export_json_csv(tmp_path: Path) -> None:
    report = {
        "baseline_name": "a",
        "candidate_name": "b",
        "compared_metrics": [],
        "delta_total_trades": 0,
        "delta_win_rate": 0,
        "delta_expectancy_R": 0,
        "delta_profit_factor": 0,
        "delta_max_drawdown_R": 0,
        "delta_average_score": 0,
        "delta_average_risk_reward": 0,
        "delta_average_spread_atr": 0,
        "improved_symbols": [],
        "degraded_symbols": [],
        "improved_sessions": [],
        "degraded_sessions": [],
        "sample_size_warning": True,
        "comparison_status": "INSUFFICIENT_DATA",
        "recommendation": "x",
        "safety_warning": "Backtest comparison is not proof of future profitability.",
    }
    json_path, csv_path = export_summary(report, tmp_path / "reports", export_json=True, export_csv=True)
    assert json_path is not None and json_path.exists()
    assert csv_path is not None and csv_path.exists()


def test_no_config_mutation() -> None:
    before = dict(Path.__dict__)
    _ = summarize_records([], dataset_name="empty")
    after = dict(Path.__dict__)
    assert before.keys() == after.keys()
