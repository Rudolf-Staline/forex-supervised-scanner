from __future__ import annotations

import csv
import json
from pathlib import Path

from app.config.watchlists import get_watchlist
from app.reporting.mt5_symbol_mapping_audit import (
    EXPECTED_MAPPINGS,
    MappingAuditOptions,
    export_audit_csv,
    export_audit_json,
    run_mapping_audit,
)


def test_expected_mapping_is_present() -> None:
    report = run_mapping_audit(MappingAuditOptions(check_static=True))
    for logical, mt5 in EXPECTED_MAPPINGS.items():
        assert report["expected_mappings"][logical] == mt5


def test_mismatch_detected_for_known_static_case() -> None:
    report = run_mapping_audit(MappingAuditOptions(check_static=True))
    mismatched = {row["logical_symbol"] for row in report["mismatched_mappings"]}
    assert "US500" in mismatched


def test_missing_report_files_do_not_crash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report = run_mapping_audit(MappingAuditOptions(check_reports=True, check_static=True))
    assert report["mapping_status"] in {"WARN", "NEEDS_REVIEW", "BLOCKED", "CLEAN"}
    assert len(report["symbols_seen_in_reports"]) == 0


def test_export_json_and_csv(tmp_path: Path) -> None:
    report = run_mapping_audit(MappingAuditOptions(check_static=True))
    json_path = export_audit_json(report, tmp_path / "out.json")
    csv_path = export_audit_csv(report, tmp_path / "out.csv")

    assert json.loads(json_path.read_text(encoding="utf-8"))["expected_mappings"]["EUR/USD"] == "EURUSD"
    with csv_path.open("r", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["logical_symbol"] == "EUR/USD" for row in rows)


def test_watchlist_is_not_mutated() -> None:
    before = get_watchlist("multi_asset_demo")
    _ = run_mapping_audit(MappingAuditOptions(check_static=True))
    after = get_watchlist("multi_asset_demo")
    assert before == after
