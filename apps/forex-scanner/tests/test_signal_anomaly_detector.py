import json
from pathlib import Path

from app.reporting import signal_anomaly_detector as sad


def test_missing_files_no_crash(tmp_path: Path) -> None:
    assert sad.collect_records(tmp_path) == []


def test_detect_invalid_score_and_risk_reward() -> None:
    rows = [{"score": 120, "risk_reward": 0, "asset_class": "forex", "symbol": "EURUSD", "timestamp": "2026-01-01T00:00:00+00:00"}]
    anomalies = sad.detect_anomalies(rows)
    types = {a["anomaly_type"] for a in anomalies}
    assert "invalid_score" in types
    assert "invalid_risk_reward" in types


def test_detect_incomplete_executable_candidate() -> None:
    rows = [{"executable_candidate": True, "asset_class": "forex", "symbol": "EURUSD", "score": 80, "risk_reward": 2.0, "timestamp": "2026-01-01T00:00:00+00:00"}]
    anomalies = sad.detect_anomalies(rows)
    assert any(a["anomaly_type"] == "incomplete_executable_candidate" for a in anomalies)


def test_detect_duplicate_cycle_id() -> None:
    rows = [
        {"cycle_id": "abc", "asset_class": "forex", "symbol": "EURUSD", "score": 80, "risk_reward": 2.0, "timestamp": "2026-01-01T00:00:00+00:00"},
        {"cycle_id": "abc", "asset_class": "forex", "symbol": "USDJPY", "score": 75, "risk_reward": 2.1, "timestamp": "2026-01-01T00:01:00+00:00"},
    ]
    anomalies = sad.detect_anomalies(rows)
    assert sum(1 for a in anomalies if a["anomaly_type"] == "duplicate_cycle_id") == 2


def test_export_json_csv_and_no_input_mutation(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    source = reports_dir / "signal_journal.jsonl"
    original = '{"score":50,"risk_reward":1.2,"asset_class":"forex","symbol":"EURUSD","timestamp":"2026-01-01T00:00:00+00:00"}\n'
    source.write_text(original, encoding="utf-8")

    rows = sad.collect_records(reports_dir)
    anomalies = sad.detect_anomalies(rows)
    summary = sad.build_summary(rows, anomalies, top_n=20)

    out_json = reports_dir / "signal_anomaly_summary.json"
    out_csv = reports_dir / "signal_anomaly_report.csv"
    out_json.write_text(json.dumps(summary), encoding="utf-8")
    sad.export_anomaly_csv(anomalies, out_csv)

    assert out_json.exists()
    assert out_csv.exists()
    assert source.read_text(encoding="utf-8") == original
