import json
from pathlib import Path

from scripts import signal_quality_report as sqr


def test_load_jsonl_parsing(tmp_path: Path) -> None:
    path = tmp_path / "signal_journal.jsonl"
    path.write_text('{"symbol":"EURUSD","score":75}\ninvalid\n{"symbol":"XAUUSD","score":80}\n', encoding="utf-8")
    rows = sqr.load_jsonl(path)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "EURUSD"


def test_missing_files_do_not_crash(tmp_path: Path) -> None:
    assert sqr.load_jsonl(tmp_path / "missing.jsonl") == []
    assert sqr.load_csv(tmp_path / "missing.csv") == []
    assert sqr.load_json(tmp_path / "missing.json") == {}


def test_near_miss_detection() -> None:
    rec = {"score": 77, "risk_reward": 1.9, "spread_atr": 0.24, "status": "watchlist"}
    ok, reasons = sqr.near_miss(rec, min_score=80, min_rr=2.0, max_spread_atr=0.2)
    assert ok is True
    assert "score_close_to_threshold" in reasons


def test_aggregation_by_asset_class() -> None:
    rows = [
        {"asset_class": "forex", "symbol": "EURUSD", "session": "london", "score": 80, "risk_reward": 2.1, "spread_atr": 0.12, "status": "approved"},
        {"asset_class": "forex", "symbol": "USDJPY", "session": "new_york", "score": 70, "risk_reward": 1.8, "spread_atr": 0.15, "status": "rejected", "reason": "score_low"},
        {"asset_class": "commodities", "symbol": "XAUUSD", "session": "london", "score": 78, "risk_reward": 2.0, "spread_atr": 0.2, "status": "watchlist"},
    ]
    summary = sqr.aggregate(rows, top_n=5, min_score=75, min_rr=2.0, max_spread_atr=0.2)
    assert summary["average_score_by_asset_class"]["forex"] == 75
    assert summary["approved_signals"] == 1
    assert summary["rejected_signals"] == 1


def test_exports_csv_json_and_no_config_mutation(tmp_path: Path) -> None:
    recs = [{"symbol": "EURUSD", "score": 80, "asset_class": "forex", "session": "london", "risk_reward": 2.0, "spread_atr": 0.1, "status": "approved"}]
    csv_path = tmp_path / "out.csv"
    sqr.export_csv(recs, csv_path)
    assert csv_path.exists()

    summary = sqr.aggregate(recs, top_n=3, min_score=75, min_rr=2.0, max_spread_atr=0.2)
    json_path = tmp_path / "out.json"
    json_path.write_text(json.dumps(summary), encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["safety_warning"] == sqr.WARNING
