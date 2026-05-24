from __future__ import annotations

import os
import time
from pathlib import Path

from app.reporting.data_health import DataHealthOptions, build_data_health_report
from scripts.data_health_report import main as cli_main


def test_missing_files_no_crash(tmp_path: Path) -> None:
    report = build_data_health_report(DataHealthOptions(reports_dir=tmp_path))
    assert report["files_missing"]
    assert report["data_quality_status"] == "BLOCKED"


def test_invalid_jsonl_detected(tmp_path: Path) -> None:
    p = tmp_path / "signal_journal.jsonl"
    p.write_text('{"cycle_id": "1"}\n{bad json}\n', encoding="utf-8")
    report = build_data_health_report(DataHealthOptions(reports_dir=tmp_path, min_records=1))
    assert report["invalid_json_lines"]


def test_invalid_csv_detected(tmp_path: Path) -> None:
    p = tmp_path / "forward_test_paper.csv"
    p.write_text("a,b\n1,2,3\n", encoding="utf-8")
    report = build_data_health_report(DataHealthOptions(reports_dir=tmp_path, min_records=1))
    assert report["invalid_csv_rows"]


def test_stale_detected(tmp_path: Path) -> None:
    p = tmp_path / "signal_journal.jsonl"
    p.write_text('{"timestamp_utc":"x","cycle_id":"1","logical_symbol":"EURUSD","asset_class":"forex","status":"ok","score":80,"risk_reward":2,"spread_atr":0.1,"decision":"reject"}\n', encoding="utf-8")
    old_ts = time.time() - (72 * 3600)
    os.utime(p, (old_ts, old_ts))
    report = build_data_health_report(DataHealthOptions(reports_dir=tmp_path, max_age_hours=48, min_records=1))
    assert "signal_journal.jsonl" in report["files_stale"]


def test_missing_required_fields_detected(tmp_path: Path) -> None:
    p = tmp_path / "signal_journal.jsonl"
    p.write_text('{"cycle_id":"1"}\n', encoding="utf-8")
    report = build_data_health_report(DataHealthOptions(reports_dir=tmp_path, min_records=1))
    assert report["missing_required_fields"]


def test_export_json_txt(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "signal_journal.jsonl").write_text(
        '{"timestamp_utc":"x","cycle_id":"1","logical_symbol":"EURUSD","asset_class":"forex","status":"ok","score":80,"risk_reward":2,"spread_atr":0.1,"decision":"hold"}\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["data_health_report.py", "--export-json", "--export-txt", "--min-records", "1"])
    cli_main()
    assert (reports / "data_health_report.json").exists()
    assert (reports / "data_health_report.txt").exists()


def test_status_levels(tmp_path: Path) -> None:
    blocked = build_data_health_report(DataHealthOptions(reports_dir=tmp_path, min_records=1))
    assert blocked["data_quality_status"] == "BLOCKED"

    warn_dir = tmp_path / "warn"
    warn_dir.mkdir()
    (warn_dir / "signal_journal.jsonl").write_text(
        '{"timestamp_utc":"x","cycle_id":"1","logical_symbol":"EURUSD","asset_class":"forex","status":"ok","score":80,"risk_reward":2,"spread_atr":0.1,"decision":"hold"}\n',
        encoding="utf-8",
    )
    warn = build_data_health_report(DataHealthOptions(reports_dir=warn_dir, min_records=1, max_age_hours=0))
    assert warn["data_quality_status"] == "WARN"

    deg_dir = tmp_path / "deg"
    deg_dir.mkdir()
    (deg_dir / "signal_journal.jsonl").write_text('{"timestamp_utc":"x","cycle_id":"1","logical_symbol":"EURUSD","asset_class":"forex","status":"ok","score":80,"risk_reward":2,"spread_atr":0.1,"decision":"hold"}\n{bad json}\n', encoding="utf-8")
    degraded = build_data_health_report(DataHealthOptions(reports_dir=deg_dir, min_records=1))
    assert degraded["data_quality_status"] == "DEGRADED"

    healthy_dir = tmp_path / "healthy"
    healthy_dir.mkdir()
    rows = []
    for i in range(12):
        rows.append(
            '{"timestamp_utc":"x","cycle_id":"%d","logical_symbol":"EURUSD","asset_class":"forex","status":"ok","score":80,"risk_reward":2,"spread_atr":0.1,"decision":"hold"}'
            % i
        )
    (healthy_dir / "signal_journal.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    for name in ["forward_test_paper.csv", "backtest_multi_asset.csv"]:
        (healthy_dir / name).write_text("a,b\n1,2\n", encoding="utf-8")
    for name in ["multi_asset_signal_report_summary.json", "threshold_optimizer_summary.json"]:
        (healthy_dir / name).write_text("{}", encoding="utf-8")
    healthy = build_data_health_report(DataHealthOptions(reports_dir=healthy_dir, min_records=10, max_age_hours=99999))
    assert healthy["data_quality_status"] == "HEALTHY"
