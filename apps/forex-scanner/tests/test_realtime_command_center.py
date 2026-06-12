from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.config.settings import load_settings
from app.core.types import Timeframe
from app.execution.autonomous_evidence import AutonomousEvidenceFinalStatus
from app.execution.autonomous_readiness import AutonomousReadinessFinalStatus
from app.execution.realtime_command_center import RealtimeCommandCenterConfig, RealtimeCommandCenterService
from app.execution.realtime_data_health import RealtimeDataHealthReport, RealtimeDataHealthStatus
from app.execution.realtime_paper_supervisor import RealtimePaperStopReason

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class DummyProvider:
    name = "mt5"


class DummyDB:
    def load_operator_controls(self):
        return SimpleNamespace(model_dump=lambda mode="json": {"maintenance_mode": False, "degraded_mode": False})

    def load_paper_orders(self):
        return []


class DummyEvidence(BaseModel):
    final_status: AutonomousEvidenceFinalStatus = AutonomousEvidenceFinalStatus.READY_EVIDENCE
    blocking_failures: list[str] = []
    task_results: list[object] = []
    output_paths: list[str] = []


class DummyReadiness(BaseModel):
    final_status: AutonomousReadinessFinalStatus = AutonomousReadinessFinalStatus.READY
    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []


class FakeDataHealth:
    def __init__(self, status: RealtimeDataHealthStatus, *, safe: bool | None = None) -> None:
        self.status = status
        self.safe = safe
        self.calls = 0

    def check(self, config):
        self.calls += 1
        safe = self.safe if self.safe is not None else self.status in {RealtimeDataHealthStatus.REALTIME_DATA_READY, RealtimeDataHealthStatus.REALTIME_DATA_WARN}
        return RealtimeDataHealthReport(
            started_at=NOW,
            completed_at=NOW,
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            status=self.status,
            latest_data_age_seconds=10.0,
            data_health_status=self.status.value,
            safe_for_realtime_paper=safe,
            provider_fallback_status="synthetic" if self.status == RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK else "not_used",
            synthetic_fallback_used=self.status == RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK,
            mt5_used=False,
            checks=[],
            blocking_reasons=[] if safe else [self.status.value],
            warnings=[],
        )


class FakeSupervisor:
    def __init__(self, stop_reason: str = RealtimePaperStopReason.COMPLETED_MAX_CYCLES.value) -> None:
        self.stop_reason = stop_reason
        self.configs = []

    def run(self, config):
        self.configs.append(config)
        blocked = self.stop_reason.startswith("BLOCKED")
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            blocking_reasons=[self.stop_reason] if blocked else [],
            output_paths={"heartbeat": str(config.reports_dir / "realtime_heartbeat.jsonl")},
            model_dump=lambda mode="json": {"stop_reason": self.stop_reason},
            positions_updated=0,
            paper_orders_created=0,
            safety_flags={"live_execution_allowed": False, "order_send_called": False},
            cycles_completed=0 if blocked else 1,
        )


class FakePositionManager:
    def evaluate_position_lifecycle(self, config):
        return SimpleNamespace(
            positions_updated=2,
            blocking_reasons=[],
            warnings=[],
            output_paths={"json": str(config.reports_dir / "realtime_paper_positions.json")},
            model_dump=lambda mode="json": {"positions_updated": 2},
        )


@pytest.fixture(autouse=True)
def safe_env(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("BROKER_MODE", "paper")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("AUTO_BOT_ENABLED", "false")
    monkeypatch.delenv("LIVE_TRADING_CONFIRMATION", raising=False)


@pytest.fixture
def settings():
    return load_settings().model_copy(deep=True)


def cfg(tmp_path: Path, **updates) -> RealtimeCommandCenterConfig:
    values = dict(
        provider="mt5",
        symbols=["EUR/USD"],
        timeframe=Timeframe.M1,
        interval_seconds=0,
        max_cycles=1,
        dry_run=True,
        reports_dir=tmp_path,
        export_json=True,
        export_txt=True,
    )
    values.update(updates)
    return RealtimeCommandCenterConfig(**values)


def patch_ready(monkeypatch):
    monkeypatch.setattr("app.execution.realtime_command_center.build_evidence", lambda *a, **k: DummyEvidence())
    monkeypatch.setattr("app.execution.realtime_command_center.build_readiness_report", lambda *a, **k: DummyReadiness())


def test_dry_run_command_center_completes_with_diagnostic_stack(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.REALTIME_DATA_READY),
        supervisor_service=FakeSupervisor(),
    ).run(cfg(tmp_path))
    assert report.final_status == "COMPLETED"
    assert report.policy_decision in {"ALLOW", "WARN_ALLOW"}
    assert report.paper_orders_created == 0


def test_synthetic_provider_blocks_realtime_paper_operation(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK),
        supervisor_service=FakeSupervisor(RealtimePaperStopReason.BLOCKED_SYNTHETIC_FALLBACK.value),
    ).run(cfg(tmp_path, provider="synthetic"))
    assert report.final_status == "BLOCKED"
    assert report.data_health_status == RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK.value
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_SYNTHETIC_FALLBACK.value


def test_stale_data_blocks(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.BLOCKED_STALE_DATA),
        supervisor_service=FakeSupervisor(RealtimePaperStopReason.BLOCKED_STALE_DATA.value),
    ).run(cfg(tmp_path))
    assert report.final_status == "BLOCKED"
    assert report.data_health_status == RealtimeDataHealthStatus.BLOCKED_STALE_DATA.value


def test_safety_drift_blocks(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.REALTIME_DATA_READY),
        supervisor_service=FakeSupervisor(RealtimePaperStopReason.BLOCKED_BY_SAFETY_DRIFT.value),
    ).run(cfg(tmp_path))
    assert report.final_status == "BLOCKED"
    assert any("ALLOW_LIVE_TRADING" in reason for reason in report.blocking_reasons)


def test_recovery_plan_is_included_when_blocked(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.BLOCKED_STALE_DATA),
        supervisor_service=FakeSupervisor(RealtimePaperStopReason.BLOCKED_STALE_DATA.value),
    ).run(cfg(tmp_path, plan_recovery_on_block=True))
    assert report.recovery_plan_status != "NOT_RUN"
    assert "recovery_json" in report.output_paths


def test_scenario_runner_can_be_included(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.REALTIME_DATA_READY),
        supervisor_service=FakeSupervisor(),
    ).run(cfg(tmp_path, run_scenarios=True))
    assert report.scenario_suite_status in {"PASS", "WARN", "FAIL"}
    assert any(stage.name == "autonomous_scenario_runner" for stage in report.stages)


def test_position_manager_can_be_included(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.REALTIME_DATA_READY),
        supervisor_service=FakeSupervisor(),
        position_manager=FakePositionManager(),
    ).run(cfg(tmp_path, manage_positions=True))
    assert report.position_manager_status == "COMPLETED"
    assert report.paper_positions_updated == 2


def test_reports_are_exported(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.REALTIME_DATA_READY),
        supervisor_service=FakeSupervisor(),
    ).run(cfg(tmp_path))
    summary = tmp_path / "realtime_command_center_summary.json"
    text = tmp_path / "realtime_command_center_report.txt"
    assert summary.exists()
    assert text.exists()
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["final_status"] == report.final_status
    assert "summary_json" in report.output_paths


def test_no_order_send_no_live_trading_no_mt5_required_no_env_mutation(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    env_path = Path(".env")
    before_file = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    before_env = dict(os.environ)
    report = RealtimeCommandCenterService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth(RealtimeDataHealthStatus.REALTIME_DATA_READY),
        supervisor_service=FakeSupervisor(),
    ).run(cfg(tmp_path))
    after_file = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    module_text = Path("app/execution/realtime_command_center.py").read_text(encoding="utf-8")
    assert before_file == after_file
    assert before_env["ALLOW_LIVE_TRADING"] == os.environ["ALLOW_LIVE_TRADING"]
    assert "order_send(" not in module_text
    assert report.safety_flags["live_execution_allowed"] is False
    assert report.safety_flags["order_send_called"] is False
