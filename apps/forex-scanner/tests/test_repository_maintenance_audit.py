from __future__ import annotations

import json
from pathlib import Path

from app.ops.repository_audit import build_repository_audit, export_report_json, export_report_txt


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_static_scan_counts_and_missing_links(tmp_path: Path) -> None:
    _write(tmp_path / "scripts" / "alpha.py", '"""alpha"""\n')
    _write(tmp_path / "scripts" / "beta.py", '"""beta"""\n')
    _write(tmp_path / "tests" / "test_alpha.py", "def test_ok():\n    assert True\n")
    _write(tmp_path / "tests" / "test_orphan.py", "def test_ok():\n    assert True\n")
    _write(tmp_path / "docs" / "alpha.md", "# alpha\n")
    _write(tmp_path / "app" / "core.py", "MODE = 'safe'\n")

    report = build_repository_audit(tmp_path)

    assert report.scripts_count == 2
    assert report.tests_count == 2
    assert report.docs_count == 1
    assert "beta.py" in report.missing_docs_for_scripts
    assert "beta.py" in report.missing_tests_for_scripts
    assert "test_orphan.py" in report.orphan_tests


def test_keyword_detection_for_order_send_and_live_trading(tmp_path: Path) -> None:
    _write(tmp_path / "scripts" / "danger.py", "# live trading\nclient.order_send(req)\n")
    _write(tmp_path / "tests" / "test_danger.py", "def test_ok():\n    assert True\n")
    _write(tmp_path / "docs" / "danger.md", "# danger\n")
    _write(tmp_path / "app" / "runner.py", "def run():\n    return 'ok'\n")

    report = build_repository_audit(tmp_path)

    assert any("live trading" in hit for hit in report.unsafe_keywords_detected)
    assert any("order_send" in hit for hit in report.order_execution_keywords_detected)
    assert report.maintenance_status == "BLOCKED"


def test_export_json_txt_and_no_mutation_of_input_files(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "audit.py"
    _write(script, '"""audit"""\nprint("ok")\n')
    _write(tmp_path / "tests" / "test_audit.py", "def test_ok():\n    assert True\n")
    _write(tmp_path / "docs" / "audit.md", "# audit\n")
    _write(tmp_path / "app" / "scanner.py", "VALUE = 1\n")

    before = script.read_text(encoding="utf-8")
    report = build_repository_audit(tmp_path)

    reports_dir = tmp_path / "reports"
    json_path = export_report_json(report, reports_dir)
    txt_path = export_report_txt(report, reports_dir)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["scripts_count"] == 1
    assert "maintenance_status" in payload
    assert "Repository Maintenance Audit" in txt_path.read_text(encoding="utf-8")
    assert script.read_text(encoding="utf-8") == before
