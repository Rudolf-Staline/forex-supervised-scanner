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
from app.execution.realtime_data_health import RealtimeDataHealthReport, RealtimeDataHealthStatus
from app.execution.realtime_paper_supervisor import (
    RealtimePaperSupervisorConfig,
    RealtimePaperSupervisorService,
    RealtimePaperStopReason,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class DummyProvider:
    name = "mt5"


class DummyDB:
    def __init__(self, *, maintenance: bool = False, degraded: bool = False) -> None:
        self.maintenance = maintenance
        self.degraded = degraded

    def load_operator_controls(self):
        return SimpleNamespace(model_dump=lambda mode="json": {"maintenance_mode": self.maintenance, "degraded_mode": self.degraded})


class DummyReadiness(BaseModel):
    final_status: AutonomousReadinessFinalStatus
    blocking_reasons: list[str] = []


class DummyEvidence(BaseModel):
    final_status: AutonomousEvidenceFinalStatus
    blocking_failures: list[str] = []


class FakeDataHealth:
    def __init__(self, statuses: list[RealtimeDataHealthStatus]) -> None:
        self.statuses = statuses
        self.calls = 0
        self.configs = []

    def check(self, config):
        self.configs.append(config)
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        safe = status in {RealtimeDataHealthStatus.REALTIME_DATA_READY, RealtimeDataHealthStatus.REALTIME_DATA_WARN}
        return RealtimeDataHealthReport(
            started_at=NOW,
            completed_at=NOW,
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            status=status,
            latest_data_age_seconds=10.0,
            data_health_status=status.value,
            safe_for_realtime_paper=safe,
            provider_fallback_status="not_used",
            synthetic_fallback_used=False,
            mt5_used=True,
            checks=[],
            blocking_reasons=[] if safe else [status.value],
            warnings=[],
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


def config(tmp_path: Path, *, max_cycles: int = 2) -> RealtimePaperSupervisorConfig:
    return RealtimePaperSupervisorConfig(
        provider="mt5",
        symbols=["EUR/USD"],
        timeframe=Timeframe.M1,
        interval_seconds=0,
        max_cycles=max_cycles,
        dry_run=True,
        reports_dir=tmp_path,
        export_json=True,
        export_txt=True,
    )


def patch_ready(monkeypatch):
    monkeypatch.setattr(
        "app.execution.realtime_paper_supervisor.build_readiness_report",
        lambda *a, **k: DummyReadiness(final_status=AutonomousReadinessFinalStatus.READY),
    )


def test_safety_env_drift_blocks(settings, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path))
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_BY_SAFETY_DRIFT.value
    assert report.cycles_attempted == 1


def test_live_confirmation_env_drift_blocks(settings, tmp_path: Path, monkeypatch):
    monkeypatch.setenv(settings.broker.live_confirmation_env, settings.broker.live_confirmation_value)
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path))
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_BY_SAFETY_DRIFT.value
    assert any(settings.broker.live_confirmation_env in reason for reason in report.blocking_reasons)
    assert report.paper_orders_created == 0


def test_post_cycle_safety_env_drift_blocks_before_success_heartbeat(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    monkeypatch.setattr(
        "app.execution.realtime_paper_supervisor.build_evidence",
        lambda *a, **k: DummyEvidence(final_status=AutonomousEvidenceFinalStatus.READY_EVIDENCE),
    )

    def drift_runner(cfg):
        monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
        return 0

    cfg = config(tmp_path, max_cycles=1).model_copy(update={"dry_run": False})
    service = RealtimePaperSupervisorService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]),
        autonomous_runner=drift_runner,
    )
    report = service.run(cfg)
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_BY_SAFETY_DRIFT.value
    assert report.cycles_attempted == 1
    assert report.cycles_completed == 0
    assert any("ALLOW_LIVE_TRADING" in reason for reason in report.blocking_reasons)
    heartbeat = tmp_path / "realtime_heartbeat.jsonl"
    payload = json.loads(heartbeat.read_text(encoding="utf-8").strip())
    assert payload["stop_reason"] == RealtimePaperStopReason.BLOCKED_BY_SAFETY_DRIFT.value

def test_maintenance_degraded_mode_blocks(settings, tmp_path: Path):
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(maintenance=True), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path))
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_BY_OPERATOR_CONTROL.value


def test_evidence_runs_before_readiness_and_policy(settings, tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def evidence(*args, **kwargs):
        calls.append("evidence")
        return DummyEvidence(final_status=AutonomousEvidenceFinalStatus.READY_EVIDENCE)

    def readiness(*args, **kwargs):
        calls.append("readiness")
        return DummyReadiness(final_status=AutonomousReadinessFinalStatus.READY)

    monkeypatch.setattr("app.execution.realtime_paper_supervisor.build_evidence", evidence)
    monkeypatch.setattr("app.execution.realtime_paper_supervisor.build_readiness_report", readiness)
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path, max_cycles=1))
    assert calls == ["evidence", "readiness"]
    assert report.evidence_status == AutonomousEvidenceFinalStatus.READY_EVIDENCE.value
    assert report.cycles[0].evidence_status == AutonomousEvidenceFinalStatus.READY_EVIDENCE.value


def test_blocked_evidence_stops_before_readiness(settings, tmp_path: Path, monkeypatch):
    readiness_called = False

    def blocked_evidence(*args, **kwargs):
        return DummyEvidence(final_status=AutonomousEvidenceFinalStatus.BLOCKED_EVIDENCE, blocking_failures=["evidence blocked"])

    def readiness(*args, **kwargs):
        nonlocal readiness_called
        readiness_called = True
        return DummyReadiness(final_status=AutonomousReadinessFinalStatus.READY)

    monkeypatch.setattr("app.execution.realtime_paper_supervisor.build_evidence", blocked_evidence)
    monkeypatch.setattr("app.execution.realtime_paper_supervisor.build_readiness_report", readiness)
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path, max_cycles=1))
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_BY_EVIDENCE.value
    assert report.blocking_reasons == ["evidence blocked"]
    assert readiness_called is False


def test_policy_denial_blocks(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    class DenyPolicy:
        decision = SimpleNamespace(value="DENY")
        allowed = False
        blocking_reasons = ["policy denied"]
    monkeypatch.setattr("app.execution.realtime_paper_supervisor.AutonomousPolicyEngine", lambda: SimpleNamespace(can_run_supervisor_cycle=lambda ctx: DenyPolicy()))
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path))
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_BY_POLICY.value


def test_bounded_max_cycles_respected_and_heartbeat_written(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path, max_cycles=2))
    assert report.stop_reason == RealtimePaperStopReason.COMPLETED_MAX_CYCLES.value
    assert report.cycles_attempted == 2
    assert report.cycles_completed == 2
    heartbeat = tmp_path / "realtime_heartbeat.jsonl"
    assert heartbeat.exists()
    lines = heartbeat.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[-1])
    assert payload["heartbeat_sequence"] == 2
    assert payload["runtime_safety_heartbeat"] is True
    assert payload["paper_demo_only"] is True
    assert payload["live_execution_allowed"] is False


def test_data_health_thresholds_are_forwarded(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    health = FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY])
    cfg = config(tmp_path, max_cycles=1).model_copy(
        update={
            "max_data_age_seconds": 90.0,
            "min_data_quality_score": 80.0,
            "warn_data_quality_score": 92.0,
            "max_spread_atr_ratio": 0.15,
        }
    )
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=health)
    report = service.run(cfg)
    assert report.stop_reason == RealtimePaperStopReason.COMPLETED_MAX_CYCLES.value
    assert health.configs[0].max_age_seconds == 90.0
    assert health.configs[0].min_quality_score == 80.0
    assert health.configs[0].warn_quality_score == 92.0
    assert health.configs[0].max_spread_atr_ratio == 0.15


def test_stale_data_stops(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.BLOCKED_STALE_DATA]))
    report = service.run(config(tmp_path))
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_STALE_DATA.value


def test_synthetic_fallback_has_explicit_stop_reason(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    service = RealtimePaperSupervisorService(
        settings,
        DummyProvider(),
        DummyDB(),
        data_health_service=FakeDataHealth([RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK]),
    )
    report = service.run(config(tmp_path))
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_SYNTHETIC_FALLBACK.value
    assert report.paper_orders_created == 0


def test_provider_failures_stop_after_repeated_failures(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    cfg = config(tmp_path, max_cycles=3).model_copy(update={"max_consecutive_provider_failures": 1})
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.BLOCKED_PROVIDER_FAILURE]))
    report = service.run(cfg)
    assert report.stop_reason == RealtimePaperStopReason.BLOCKED_BY_PROVIDER_FAILURES.value


def test_no_order_send_no_live_trading_no_env_mutation(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    env_path = Path(".env")
    before = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path, max_cycles=1))
    after = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    module_text = Path("app/execution/realtime_paper_supervisor.py").read_text(encoding="utf-8")
    assert before == after
    assert "order_send(" not in module_text
    assert report.safety_flags["live_execution_allowed"] is False
    assert report.safety_flags["order_send_called"] is False
    assert report.paper_orders_created == 0


def test_no_mt5_required_in_ci(settings, tmp_path: Path, monkeypatch):
    patch_ready(monkeypatch)
    service = RealtimePaperSupervisorService(settings, DummyProvider(), DummyDB(), data_health_service=FakeDataHealth([RealtimeDataHealthStatus.REALTIME_DATA_READY]))
    report = service.run(config(tmp_path, max_cycles=1))
    assert report.data_health_status == RealtimeDataHealthStatus.REALTIME_DATA_READY.value
