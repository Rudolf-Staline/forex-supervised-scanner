"""Autonomous Readiness Gate paper/demo safety tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import app.execution.autonomous_readiness as readiness_module
import app.execution.autonomous_supervisor as supervisor_module
from app.core.types import TradingStyle
from app.execution.autonomous_readiness import (
    AutonomousReadinessConfig,
    AutonomousReadinessFinalStatus,
    build_readiness_report,
)
from app.execution.autonomous_supervisor import AutonomousSupervisorConfig, AutonomousSupervisorFinalStatus, AutonomousSupervisorService
from app.execution.demo_bot import DemoBotCycleResult
from app.execution.operations import OperatorControlState
from app.risk.daily_limits import DailyRiskSummary
from app.storage.database import Database


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "readiness.sqlite")


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_healthy_reports(reports_dir: Path) -> None:
    _write(reports_dir / "session_health_summary.json", {"overall_status": "HEALTHY", "mt5_called": False})
    _write(reports_dir / "data_health_report.json", {"data_quality_status": "HEALTHY", "data_quality_score": 95})
    _write(reports_dir / "failure_diagnostics_summary.json", {"severity": "CLEAN", "orders_sent": False})
    _write(reports_dir / "signal_anomaly_summary.json", {"data_integrity_status": "CLEAN", "high_severity_anomalies": 0})


class FakeDemoBotService:
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def run_cycle(self, style: TradingStyle, symbols: list[str], watchlist: str | None = None) -> DemoBotCycleResult:
        self.__class__.calls += 1
        now = datetime.now(timezone.utc)
        return DemoBotCycleResult(
            cycle_id="readiness-cycle",
            started_at=now,
            completed_at=now,
            style=style,
            symbols=symbols,
            opportunities=1,
            orders_created=1,
            decisions=[],
            logs=[],
            safety_flags={},
            risk_summary=DailyRiskSummary(
                trades_today=0,
                open_trades=0,
                daily_pnl=0.0,
                daily_loss_percent=0.0,
                remaining_trade_slots=5,
                bot_risk_status="ok",
                consecutive_losses=0,
            ),
        )


def test_safe_dry_run_missing_evidence_warn_ready(settings, database, tmp_path: Path) -> None:
    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=True))

    assert report.final_status == AutonomousReadinessFinalStatus.WARN_READY
    assert report.dry_run_allowed is True
    assert report.paper_run_allowed is False
    assert report.missing_reports


def test_non_dry_run_paper_missing_evidence_blocked(settings, database, tmp_path: Path) -> None:
    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=False))

    assert report.final_status == AutonomousReadinessFinalStatus.BLOCKED_BY_NO_EVIDENCE
    assert report.paper_run_allowed is False


def test_maintenance_mode_blocks(settings, database, tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    database.save_operator_controls(OperatorControlState(updated_at=datetime.now(timezone.utc), maintenance_mode=True))

    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=False))

    assert report.final_status == AutonomousReadinessFinalStatus.BLOCKED_BY_OPERATOR_CONTROL
    assert any("maintenance" in reason for reason in report.blocking_reasons)


def test_degraded_mode_blocks_by_default(settings, database, tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    database.save_operator_controls(OperatorControlState(updated_at=datetime.now(timezone.utc), degraded_mode=True))

    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=False))

    assert report.final_status == AutonomousReadinessFinalStatus.BLOCKED_BY_OPERATOR_CONTROL
    assert any("degraded" in reason for reason in report.blocking_reasons)


def test_stale_reports_block(settings, database, tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    old = datetime.now(timezone.utc) - timedelta(days=3)
    os.utime(tmp_path / "session_health_summary.json", (old.timestamp(), old.timestamp()))

    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=False))

    assert report.final_status == AutonomousReadinessFinalStatus.BLOCKED_BY_STALE_REPORTS
    assert "session_health_summary.json" in report.stale_reports


def test_failure_diagnostics_blocking_severity_blocks(settings, database, tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    _write(tmp_path / "failure_diagnostics_summary.json", {"severity": "BLOCKED", "safety_blockers": ["blocked by safety"]})

    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=False))

    assert report.final_status == AutonomousReadinessFinalStatus.BLOCKED_BY_SAFETY
    assert any("failure diagnostics" in reason for reason in report.blocking_reasons)


def test_severe_anomaly_report_blocks(settings, database, tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)
    _write(tmp_path / "signal_anomaly_summary.json", {"data_integrity_status": "DEGRADED", "high_severity_anomalies": 1})

    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=False))

    assert report.final_status == AutonomousReadinessFinalStatus.BLOCKED_BY_DATA_QUALITY


def test_healthy_reports_allow_ready(settings, database, tmp_path: Path) -> None:
    write_healthy_reports(tmp_path)

    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=False))

    assert report.final_status == AutonomousReadinessFinalStatus.READY
    assert report.ready is True
    assert report.paper_run_allowed is True


def test_readiness_report_schema_contains_required_fields(settings, database, tmp_path: Path) -> None:
    report = build_readiness_report(settings, database, AutonomousReadinessConfig(reports_dir=tmp_path, dry_run=True))
    payload = report.model_dump(mode="json")

    for key in {
        "generated_at",
        "final_status",
        "ready",
        "dry_run_allowed",
        "paper_run_allowed",
        "blocking_reasons",
        "warning_reasons",
        "checks",
        "evidence_files",
        "stale_reports",
        "missing_reports",
        "operator_controls",
        "risk_snapshot",
        "safety_flags",
    }:
        assert key in payload
    assert {"name", "status", "severity", "reason", "evidence"}.issubset(payload["checks"][0])


def test_supervisor_refuses_cycles_when_readiness_blocks(settings, database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDemoBotService.calls = 0
    monkeypatch.setattr(supervisor_module, "DemoBotService", FakeDemoBotService)

    result = AutonomousSupervisorService(settings, object(), database).run_loop(
        AutonomousSupervisorConfig(enabled=True, dry_run=False, reports_dir=tmp_path, max_cycles=1, interval_seconds=0)
    )

    assert result.final_status == AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS
    assert result.cycle_count == 0
    assert FakeDemoBotService.calls == 0


def test_skip_readiness_gate_only_dry_run_diagnostic(settings, database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDemoBotService.calls = 0
    monkeypatch.setattr(supervisor_module, "DemoBotService", FakeDemoBotService)

    dry = AutonomousSupervisorService(settings, object(), database).run_loop(
        AutonomousSupervisorConfig(enabled=True, dry_run=True, skip_readiness_gate=True, reports_dir=tmp_path, max_cycles=1, interval_seconds=0)
    )
    paper = AutonomousSupervisorService(settings, object(), database).run_loop(
        AutonomousSupervisorConfig(enabled=True, dry_run=False, skip_readiness_gate=True, reports_dir=tmp_path, max_cycles=1, interval_seconds=0)
    )

    assert dry.final_status == AutonomousSupervisorFinalStatus.DRY_RUN
    assert paper.final_status == AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS
    assert FakeDemoBotService.calls == 0


def test_readiness_cli_never_calls_mt5_order_execution_or_mutates_env(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    env_file = repo / ".env"
    before = env_file.read_text(encoding="utf-8") if env_file.exists() else None

    result = subprocess.run(
        [sys.executable, "scripts/autonomous_readiness_report.py", "--reports-dir", str(tmp_path), "--export-json", "--export-txt"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    after = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    source = Path(readiness_module.__file__).read_text(encoding="utf-8")
    forbidden_call = "order" + "_" + "send"
    assert result.returncode == 0
    assert "autonomous_readiness=WARN_READY" in result.stdout
    assert before == after
    assert forbidden_call not in source
    assert "MetaTrader5" not in source
