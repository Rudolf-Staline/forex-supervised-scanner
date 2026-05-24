from __future__ import annotations

import json
from pathlib import Path

from app.reporting.asset_concentration import AssetConcentrationOptions, build_asset_concentration_report, write_asset_concentration_csv


def _write_signal_rows(path: Path) -> None:
    rows = [
        {"logical_symbol": "EUR/USD", "session": "london", "decision": "approved"},
        {"logical_symbol": "EUR/USD", "session": "london", "decision": "approved"},
        {"logical_symbol": "EUR/USD", "session": "new_york", "decision": "rejected"},
        {"logical_symbol": "GBP/USD", "session": "new_york", "decision": "approved"},
        {"logical_symbol": "XAU/USD", "session": "asia", "decision": "rejected"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_missing_files_no_crash(tmp_path: Path) -> None:
    report = build_asset_concentration_report(AssetConcentrationOptions(reports_dir=tmp_path))
    assert report["concentration_risk_status"] == "INSUFFICIENT_DATA"
    assert "signal_journal.jsonl" in report["input_files_missing_or_empty"]


def test_concentration_by_asset_class_and_symbol(tmp_path: Path) -> None:
    _write_signal_rows(tmp_path / "signal_journal.jsonl")
    report = build_asset_concentration_report(AssetConcentrationOptions(reports_dir=tmp_path, asset_class="all"))
    assert report["concentration_by_asset_class"]["forex"] >= 4
    assert report["concentration_by_asset_class"]["commodities"] >= 1
    assert report["concentration_by_symbol"]["EUR/USD"] == 3


def test_detect_overrepresented_symbols(tmp_path: Path) -> None:
    _write_signal_rows(tmp_path / "signal_journal.jsonl")
    report = build_asset_concentration_report(AssetConcentrationOptions(reports_dir=tmp_path, asset_class="forex"))
    assert "EUR/USD" in report["overrepresented_symbols"]


def test_export_json_csv(tmp_path: Path) -> None:
    _write_signal_rows(tmp_path / "signal_journal.jsonl")
    report = build_asset_concentration_report(AssetConcentrationOptions(reports_dir=tmp_path))
    out_json = tmp_path / "asset_concentration_summary.json"
    out_csv = tmp_path / "asset_concentration_report.csv"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_asset_concentration_csv(report, out_csv)
    assert out_json.exists()
    assert out_csv.exists()


def test_no_config_mutation() -> None:
    from app.config.watchlists import WATCHLISTS

    before = list(WATCHLISTS["multi_asset_demo"])
    _ = build_asset_concentration_report(AssetConcentrationOptions(reports_dir=Path(".")))
    after = list(WATCHLISTS["multi_asset_demo"])
    assert before == after
