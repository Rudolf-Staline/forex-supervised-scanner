from __future__ import annotations

import json
from pathlib import Path

from app.reporting.symbol_health_report import SymbolHealthOptions, build_symbol_health_report
from scripts.symbol_health_report import main as cli_main


def _write_signal(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_missing_files_no_crash(tmp_path: Path) -> None:
    report = build_symbol_health_report(SymbolHealthOptions(reports_dir=tmp_path))
    assert report["symbol_health_status"] == "BLOCKED"
    assert report["input_files_missing_or_empty"]


def test_aggregation_by_symbol(tmp_path: Path) -> None:
    _write_signal(
        tmp_path / "signal_journal.jsonl",
        [
            {"logical_symbol": "EURUSD", "asset_class": "forex", "score": 80, "risk_reward": 1.4, "spread_atr": 0.1, "decision": "hold", "session": "london"},
            {"logical_symbol": "EURUSD", "asset_class": "forex", "score": 82, "risk_reward": 1.5, "spread_atr": 0.1, "decision": "hold", "session": "ny"},
            {"logical_symbol": "XAUUSD", "asset_class": "commodities", "score": 60, "risk_reward": 1.1, "spread_atr": 0.2, "decision": "reject", "session": "ny"},
        ],
    )
    report = build_symbol_health_report(SymbolHealthOptions(reports_dir=tmp_path, asset_class="all"))
    assert "EURUSD" in report["symbols_detected"]
    assert "XAUUSD" in report["symbols_detected"]
    assert "EURUSD" in report["symbols_by_asset_class"]["forex"]


def test_detect_high_spread_atr(tmp_path: Path) -> None:
    _write_signal(
        tmp_path / "signal_journal.jsonl",
        [{"logical_symbol": "GER40", "asset_class": "indices", "score": 50, "risk_reward": 1.5, "spread_atr": 0.55, "decision": "hold", "session": "london"}],
    )
    report = build_symbol_health_report(SymbolHealthOptions(reports_dir=tmp_path))
    assert "GER40" in report["symbols_with_high_spread_atr"]


def test_detect_no_data(tmp_path: Path) -> None:
    (tmp_path / "multi_asset_signal_report_summary.json").write_text(
        json.dumps({"symbols": {"USDJPY": {"asset_class": "forex"}}}), encoding="utf-8"
    )
    report = build_symbol_health_report(SymbolHealthOptions(reports_dir=tmp_path))
    assert "USDJPY" in report["symbols_with_no_data"]


def test_export_json_csv(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    _write_signal(
        reports / "signal_journal.jsonl",
        [{"logical_symbol": "EURUSD", "asset_class": "forex", "score": 80, "risk_reward": 1.3, "spread_atr": 0.1, "decision": "hold", "session": "london"}],
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["symbol_health_report.py", "--watchlist", "multi_asset_demo", "--asset-class", "all", "--export-json", "--export-csv"],
    )
    cli_main()
    assert (reports / "symbol_health_summary.json").exists()
    assert (reports / "symbol_health_report.csv").exists()


def test_no_mutation_of_config_or_watchlist(tmp_path: Path) -> None:
    watchlist_cfg = tmp_path / "watchlist.json"
    watchlist_cfg.write_text('{"watchlist": ["EURUSD"]}', encoding="utf-8")
    before = watchlist_cfg.read_text(encoding="utf-8")
    _ = build_symbol_health_report(SymbolHealthOptions(reports_dir=tmp_path))
    after = watchlist_cfg.read_text(encoding="utf-8")
    assert before == after
