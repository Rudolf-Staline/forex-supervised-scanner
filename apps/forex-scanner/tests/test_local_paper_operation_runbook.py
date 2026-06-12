from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = APP_ROOT / "docs" / "local_paper_operation_runbook.md"
README = APP_ROOT / "README.md"
PAPER_OPERATION_DOC = APP_ROOT / "docs" / "realtime_paper_operation.md"


def test_runbook_file_exists():
    assert RUNBOOK.exists(), "Local Paper Operation Runbook must exist"


def test_runbook_contains_key_safety_phrases():
    text = RUNBOOK.read_text(encoding="utf-8")
    required_phrases = [
        "No live trading is authorized",
        "read-only",
        "not a go-live approval",
        "evidence only",
        "separate manual review process",
        "EXECUTION_MODE=paper",
        "ALLOW_LIVE_TRADING=false",
        "BROKER_MODE=paper",
        "order_send",
    ]
    for phrase in required_phrases:
        assert phrase in text, f"runbook must mention {phrase!r}"


def test_runbook_documents_workflow_and_statuses():
    text = RUNBOOK.read_text(encoding="utf-8")
    for status in [
        "MT5_REALTIME_READY",
        "MT5_REALTIME_WARN",
        "BLOCKED_MT5_UNAVAILABLE",
        "BLOCKED_STALE_DATA",
        "BLOCKED_SPREAD_TOO_WIDE",
        "BLOCKED_POOR_DATA_QUALITY",
        "COMPLETED",
        "WARN",
        "BLOCKED",
        "BLOCKED_BY_POLICY",
        "BLOCKED_BY_SAFETY_DRIFT",
    ]:
        assert status in text, f"runbook must explain status {status}"

    for report in [
        "reports/local_mt5_realtime_validation.json",
        "reports/local_mt5_realtime_validation.txt",
        "reports/local_mt5_realtime_samples.csv",
        "reports/realtime_command_center_summary.json",
        "reports/realtime_command_center_report.txt",
        "reports/realtime_paper_supervisor_summary.json",
        "reports/realtime_paper_supervisor_report.txt",
        "reports/realtime_heartbeat.jsonl",
    ]:
        assert report in text, f"runbook must interpret {report}"

    # Copy-paste operator checklist with pass/fail fields.
    assert "CHECKLIST" in text
    assert "PASS / FAIL" in text


def test_runbook_is_linked_from_docs():
    link = "local_paper_operation_runbook.md"
    assert link in README.read_text(encoding="utf-8"), "README must link the runbook"
    assert link in PAPER_OPERATION_DOC.read_text(encoding="utf-8"), "paper operation doc must link the runbook"
