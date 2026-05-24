from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.reporting.report_index import (
    ReportIndexOptions,
    build_report_index,
    export_report_index_json,
    export_report_index_txt,
)


def test_missing_reports_do_not_crash(tmp_path):
    payload = build_report_index(ReportIndexOptions(reports_dir=tmp_path, show_missing=True))
    assert isinstance(payload["reports_missing"], list)
    assert payload["reports_found"] == []


def test_json_validity_detection(tmp_path):
    (tmp_path / "readiness_report.json").write_text('{"ok": true}\n', encoding="utf-8")
    (tmp_path / "signal_quality_summary.json").write_text('{"broken": }\n', encoding="utf-8")

    payload = build_report_index(ReportIndexOptions(reports_dir=tmp_path, show_missing=True))
    assert payload["json_validity"]["readiness_report.json"] == "valid"
    assert payload["json_validity"]["signal_quality_summary.json"] == "invalid"


def test_csv_and_jsonl_counts(tmp_path):
    (tmp_path / "forward_test_paper.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    (tmp_path / "signal_journal.jsonl").write_text('{"a":1}\n\n{"a":2}\n', encoding="utf-8")

    payload = build_report_index(ReportIndexOptions(reports_dir=tmp_path, show_missing=True))
    assert payload["csv_row_counts"]["forward_test_paper.csv"] == 3
    assert payload["jsonl_line_counts"]["signal_journal.jsonl"] == 2


def test_stale_detection(tmp_path):
    target = tmp_path / "readiness_report.json"
    target.write_text('{"ok": true}\n', encoding="utf-8")
    stale = datetime.now(timezone.utc) - timedelta(hours=72)
    ts = stale.timestamp()
    target.touch()
    import os

    os.utime(target, (ts, ts))

    payload = build_report_index(
        ReportIndexOptions(reports_dir=tmp_path, show_missing=True, show_stale=True, max_age_hours=48)
    )
    assert "readiness_report.json" in payload["stale_reports"]


def test_exports_json_and_txt(tmp_path):
    payload = build_report_index(ReportIndexOptions(reports_dir=tmp_path, show_missing=True, show_stale=True))
    json_path = export_report_index_json(payload, tmp_path)
    txt_path = export_report_index_txt(payload, tmp_path)

    exported = json.loads(json_path.read_text(encoding="utf-8"))
    text = txt_path.read_text(encoding="utf-8")

    assert exported["generated_at"]
    assert "# Report Index" in text
