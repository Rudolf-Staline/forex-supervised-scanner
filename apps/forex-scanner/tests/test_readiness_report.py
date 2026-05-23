"""Readiness report tests."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import readiness_report as readiness  # noqa: E402
from app.brokers.mt5_reconciliation import MT5ReconciliationReport  # noqa: E402


def test_classify_readiness_not_ready_when_critical_fails() -> None:
    checks = [readiness.ReadinessCheck("critical_tests", "FAIL", "failed")]

    assert readiness.classify_readiness(checks, None) == "NOT_READY"


def test_classify_readiness_paper_ready_without_mt5() -> None:
    checks = _paper_ready_checks()

    assert readiness.classify_readiness(checks, None) == "PAPER_READY"


def test_classify_readiness_demo_ready_limited_when_all_strict_checks_pass() -> None:
    checks = [
        *_paper_ready_checks(),
        readiness.ReadinessCheck("mt5_connected", "OK", "connected"),
        readiness.ReadinessCheck("account_demo_only", "OK", "demo"),
        readiness.ReadinessCheck("symbol_resolver", "OK", "resolved"),
        readiness.ReadinessCheck("symbol_health", "OK", "healthy"),
        readiness.ReadinessCheck("max_demo_order_volume", "OK", "0.01"),
        readiness.ReadinessCheck("max_demo_orders_per_day", "OK", "1"),
    ]
    report = MT5ReconciliationReport(
        mt5_connected=True,
        account_server="Deriv-Demo",
        demo_only=True,
        open_positions=0,
        pending_orders=0,
        bot_positions=0,
        foreign_positions=0,
        duplicate_risk=False,
        reconciliation_status="OK",
    )

    assert readiness.classify_readiness(checks, report) == "DEMO_READY_LIMITED"


def test_write_readiness_outputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(readiness, "READINESS_TXT", tmp_path / "readiness_report.txt")
    monkeypatch.setattr(readiness, "READINESS_JSON", tmp_path / "readiness_report.json")
    payload = {
        "generated_at": "2026-05-23T00:00:00+00:00",
        "readiness_status": "PAPER_READY",
        "checks": [readiness.ReadinessCheck("journal", "OK", "ready").__dict__],
    }

    readiness.write_readiness_outputs(payload)

    assert "readiness_status: PAPER_READY" in (tmp_path / "readiness_report.txt").read_text(encoding="utf-8")
    assert '"readiness_status": "PAPER_READY"' in (tmp_path / "readiness_report.json").read_text(encoding="utf-8")


def test_build_readiness_report_is_conservative_when_tests_are_skipped(monkeypatch, tmp_path: Path, settings) -> None:
    class FakeDatabase:
        def __init__(self, _path) -> None:
            pass

        def load_trade_events(self):
            return []

        def load_paper_orders(self):
            return []

        def load_broker_orders(self):
            return []

        def save_trade_events(self, _events):
            return None

        def save_rejected_signals(self, _records):
            return None

        def rebuild_trading_journal(self):
            return None

        def load_operator_controls(self):
            from app.execution.operations import OperatorControlState

            return OperatorControlState()

    monkeypatch.setattr(readiness, "Database", FakeDatabase)
    monkeypatch.setattr(readiness, "READINESS_TXT", tmp_path / "readiness_report.txt")
    monkeypatch.setattr(readiness, "READINESS_JSON", tmp_path / "readiness_report.json")
    monkeypatch.setenv("ENABLE_DEMO_EXECUTION", "false")
    monkeypatch.setenv("MAX_DEMO_ORDER_VOLUME", "0.01")
    monkeypatch.setenv("MAX_DEMO_ORDERS_PER_DAY", "1")

    report = readiness.build_readiness_report(run_tests=False, mt5_module=None)

    assert report["orders_sent"] is False
    assert report["config_modified"] is False
    assert report["demo_execution_enabled_by_report"] is False
    assert report["readiness_status"] in {"NOT_READY", "PAPER_READY"}


def _paper_ready_checks() -> list[readiness.ReadinessCheck]:
    return [
        readiness.ReadinessCheck("critical_tests", "OK", "passed"),
        readiness.ReadinessCheck("ALLOW_LIVE_TRADING", "OK", "false"),
        readiness.ReadinessCheck("ENABLE_DEMO_EXECUTION", "OK", "false"),
        readiness.ReadinessCheck("broker_paper", "OK", "paper ok"),
        readiness.ReadinessCheck("demo_execution_gate", "OK", "available"),
        readiness.ReadinessCheck("daily_limits", "OK", "available"),
        readiness.ReadinessCheck("journal", "OK", "available"),
        readiness.ReadinessCheck("max_demo_order_volume", "OK", "0.01"),
        readiness.ReadinessCheck("max_demo_orders_per_day", "OK", "1"),
    ]
