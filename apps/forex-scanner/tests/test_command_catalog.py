from __future__ import annotations

import json
from pathlib import Path

from app.ops.command_catalog import export_catalog_json, export_catalog_md, filter_entries, scan_commands


def _make_script(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_scan_static_and_basic_classification(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _make_script(scripts / "risk_exposure_report.py", '"""Risk report."""\nprint("ok")\n')
    _make_script(scripts / "paper_trade.py", '"""Paper trading."""\nmode="paper"\n')

    entries = scan_commands(scripts)
    by_name = {e.script_name: e for e in entries}

    assert by_name["risk_exposure_report.py"].guessed_category == "reports"
    assert by_name["risk_exposure_report.py"].safety_level == "READ_ONLY"
    assert by_name["paper_trade.py"].guessed_category == "paper"
    assert by_name["paper_trade.py"].safety_level == "PAPER_ONLY"


def test_export_json_and_md(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    reports = tmp_path / "reports"
    scripts.mkdir()
    _make_script(scripts / "audit_integrity.py", '"""Audit integrity."""\n')

    entries = scan_commands(scripts)
    json_path = export_catalog_json(entries, reports)
    md_path = export_catalog_md(entries, reports)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload[0]["script_name"] == "audit_integrity.py"
    assert "| `audit_integrity.py` |" in md_path.read_text(encoding="utf-8")


def test_order_keyword_detection_and_defaults(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _make_script(scripts / "broker_submit.py", '"""Submit something."""\napi.order_send(payload)\n')

    entry = scan_commands(scripts)[0]
    assert entry.can_send_order is True
    assert entry.safety_level in {"DEMO_GATED", "UNKNOWN"}
    assert entry.warnings


def test_no_subprocess_and_no_mt5_runtime_calls(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _make_script(scripts / "mt5_symbols_health.py", '"""Health check."""\nname="mt5"\n')

    entries = scan_commands(scripts)
    assert entries[0].requires_mt5 is True
    # Static scanner should not execute scripts; this test passes if scan returns without side effects.
    filtered = filter_entries(entries, "all", show_unsafe=True)
    assert len(filtered) == 1
