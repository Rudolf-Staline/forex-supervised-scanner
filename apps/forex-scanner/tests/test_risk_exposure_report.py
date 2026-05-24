from __future__ import annotations

import json
from pathlib import Path

from app.config.settings import load_settings
from app.reporting.risk_exposure import analyze_risk_exposure, export_report_csv


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_missing_files_no_crash(tmp_path: Path) -> None:
    summary = analyze_risk_exposure(tmp_path)
    assert summary["total_candidates"] == 0
    assert summary["safety_status"] in {"ok", "warning", "blocked"}


def test_aggregation_by_asset_and_symbol(tmp_path: Path) -> None:
    write(
        tmp_path / "signal_journal.jsonl",
        "\n".join(
            [
                json.dumps({"symbol": "EURUSD", "asset_class": "forex", "status": "executable", "risk_reward": 1.8, "spread_atr": 0.05}),
                json.dumps({"symbol": "XAUUSD", "asset_class": "commodities", "status": "rejected", "reason": "spread_too_high", "risk_reward": 0.9, "spread_atr": 0.2}),
            ]
        ),
    )
    summary = analyze_risk_exposure(tmp_path)
    assert summary["exposure_by_asset_class"]["forex"] == 1
    assert summary["exposure_by_asset_class"]["commodities"] == 1
    assert summary["exposure_by_symbol"]["EURUSD"] == 1
    assert summary["exposure_by_symbol"]["XAUUSD"] == 1


def test_high_risk_detection(tmp_path: Path) -> None:
    lines = [
        {"symbol": "EURUSD", "asset_class": "forex", "status": "executable", "risk_reward": 1.0, "spread_atr": 0.15},
        {"symbol": "EURUSD", "asset_class": "forex", "status": "executable", "risk_reward": 1.1, "spread_atr": 0.13},
        {"symbol": "EURUSD", "asset_class": "forex", "status": "executable", "risk_reward": 1.05, "spread_atr": ""},
    ]
    write(tmp_path / "signal_journal.jsonl", "\n".join(json.dumps(x) for x in lines))
    summary = analyze_risk_exposure(tmp_path)
    assert summary["high_risk_candidates"]
    assert summary["safety_status"] == "warning"


def test_export_json_csv(tmp_path: Path) -> None:
    write(tmp_path / "signal_journal.jsonl", json.dumps({"symbol": "USDJPY", "asset_class": "forex", "status": "executable", "risk_reward": 1.5, "spread_atr": 0.05}))
    summary = analyze_risk_exposure(tmp_path)
    out_json = tmp_path / "risk_exposure_summary.json"
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    out_csv = tmp_path / "risk_exposure_report.csv"
    export_report_csv(summary, out_csv)
    assert out_json.exists()
    assert out_csv.exists()


def test_no_config_mutation(tmp_path: Path) -> None:
    before = load_settings().model_dump()
    analyze_risk_exposure(tmp_path)
    after = load_settings().model_dump()
    assert before == after
