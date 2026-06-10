"""Autonomous Supervisor v0 paper/demo safety tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.execution.autonomous_supervisor as autonomous_module
from app.core.types import TradingStyle
from app.execution.autonomous_supervisor import (
    AutonomousSupervisorConfig,
    AutonomousSupervisorFinalStatus,
    AutonomousSupervisorService,
)
from app.execution.demo_bot import DemoBotCycleResult, DemoBotDecision
from app.execution.operations import OperatorControlState
from app.execution.autonomous_readiness import AutonomousReadinessFinalStatus, AutonomousReadinessReport
from app.risk.daily_limits import DailyRiskSummary
from app.storage.database import Database


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "supervisor.sqlite")


class FakeDemoBotService:
    calls: list[tuple[TradingStyle, list[str], str | None]] = []
    orders_created = 1
    fail = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    def run_cycle(self, style: TradingStyle, symbols: list[str], watchlist: str | None = None) -> DemoBotCycleResult:
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((style, symbols, watchlist))
        now = datetime.now(timezone.utc)
        accepted = self.orders_created > 0
        return DemoBotCycleResult(
            cycle_id=f"cycle-{len(self.calls)}",
            started_at=now,
            completed_at=now,
            style=style,
            symbols=symbols,
            opportunities=2,
            orders_created=self.orders_created,
            decisions=[
                DemoBotDecision(
                    symbol=symbols[0],
                    status="approved" if accepted else "watchlist",
                    setup_subtype="breakout",
                    accepted=accepted,
                    order_ids=["paper-1"] if accepted else [],
                    reasons=[] if accepted else ["status watchlist is not executable by demo bot"],
                ),
                DemoBotDecision(
                    symbol=symbols[-1],
                    status="watchlist",
                    setup_subtype="pullback",
                    accepted=False,
                    reasons=["status watchlist is not executable by demo bot"],
                ),
            ],
            logs=["fake cycle"],
            risk_summary=DailyRiskSummary(
                trades_today=self.orders_created,
                open_trades=self.orders_created,
                daily_pnl=0.0,
                daily_loss_percent=0.0,
                remaining_trade_slots=4,
                bot_risk_status="OK",
                consecutive_losses=0,
            ),
        )


@pytest.fixture(autouse=True)
def reset_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDemoBotService.calls = []
    FakeDemoBotService.orders_created = 1
    FakeDemoBotService.fail = False
    monkeypatch.setattr(autonomous_module, "DemoBotService", FakeDemoBotService)

    def fake_readiness(*args, **kwargs) -> AutonomousReadinessReport:
        now = datetime.now(timezone.utc)
        return AutonomousReadinessReport(
            generated_at=now,
            final_status=AutonomousReadinessFinalStatus.READY,
            ready=True,
            dry_run_allowed=True,
            paper_run_allowed=True,
            safety_flags={"live_execution_allowed": False},
        )

    monkeypatch.setattr(autonomous_module, "build_readiness_report", fake_readiness)


def enabled_config(**overrides: object) -> AutonomousSupervisorConfig:
    values = {
        "enabled": True,
        "dry_run": False,
        "symbols": ["EUR/USD", "GBP/USD"],
        "max_cycles": 1,
        "interval_seconds": 0,
        "cooldown_seconds": 0,
    }
    values.update(overrides)
    return AutonomousSupervisorConfig(**values)


def test_run_once_calls_existing_demo_bot_safely(settings, database) -> None:
    result = AutonomousSupervisorService(settings, object(), database).run_once(enabled_config())

    assert result.final_status == AutonomousSupervisorFinalStatus.COMPLETED
    assert result.cycle_count == 1
    assert result.paper_orders_created == 1
    assert result.safety_flags["paper_demo_only"] is True
    assert result.safety_flags["live_execution_allowed"] is False
    assert FakeDemoBotService.calls == [(TradingStyle.DAY_TRADING, ["EUR/USD", "GBP/USD"], None)]


def test_dry_run_mode_does_not_create_paper_orders(settings, database) -> None:
    result = AutonomousSupervisorService(settings, object(), database).run_loop(
        enabled_config(dry_run=True, max_cycles=1)
    )

    assert result.final_status == AutonomousSupervisorFinalStatus.DRY_RUN
    assert result.dry_run is True
    assert result.paper_orders_created == 0
    assert FakeDemoBotService.calls == []


def test_loop_stops_at_max_cycles(settings, database) -> None:
    result = AutonomousSupervisorService(settings, object(), database).run_loop(enabled_config(max_cycles=3))

    assert result.final_status == AutonomousSupervisorFinalStatus.COMPLETED
    assert result.cycle_count == 3
    assert len(FakeDemoBotService.calls) == 3


@pytest.mark.parametrize("maintenance,degraded", [(True, False), (False, True)])
def test_loop_stops_on_operator_controls(settings, database, maintenance: bool, degraded: bool) -> None:
    database.save_operator_controls(
        OperatorControlState(updated_at=datetime.now(timezone.utc), maintenance_mode=maintenance, degraded_mode=degraded)
    )

    result = AutonomousSupervisorService(settings, object(), database).run_loop(enabled_config(max_cycles=3))

    assert result.final_status == AutonomousSupervisorFinalStatus.STOPPED_BY_OPERATOR_CONTROL
    assert result.cycle_count == 0
    assert FakeDemoBotService.calls == []
    assert "operator control active" in (result.stop_reason or "")


def test_loop_stops_after_max_consecutive_failures(settings, database) -> None:
    FakeDemoBotService.fail = True

    result = AutonomousSupervisorService(settings, object(), database).run_loop(
        enabled_config(max_cycles=5, max_consecutive_failures=2)
    )

    assert result.final_status == AutonomousSupervisorFinalStatus.STOPPED_BY_FAILURES
    assert result.cycle_count == 2
    assert all(cycle.error for cycle in result.cycles)


def test_loop_stops_after_max_zero_order_cycles(settings, database) -> None:
    FakeDemoBotService.orders_created = 0

    result = AutonomousSupervisorService(settings, object(), database).run_loop(
        enabled_config(max_cycles=5, max_zero_order_cycles=2)
    )

    assert result.final_status == AutonomousSupervisorFinalStatus.STOPPED_BY_RISK
    assert result.cycle_count == 2
    assert result.paper_orders_created == 0


def test_json_and_txt_exports_work(settings, database, tmp_path) -> None:
    result = AutonomousSupervisorService(settings, object(), database).run_loop(
        enabled_config(export_json=True, export_txt=True, reports_dir=tmp_path)
    )

    json_path = tmp_path / "autonomous_supervisor_summary.json"
    txt_path = tmp_path / "autonomous_supervisor_report.txt"
    assert json_path.exists()
    assert txt_path.exists()
    assert str(json_path) in result.export_paths
    assert str(txt_path) in result.export_paths
    assert '"final_status": "COMPLETED"' in json_path.read_text(encoding="utf-8")
    assert "orders_created: 1" in txt_path.read_text(encoding="utf-8")


def test_unsafe_live_trading_settings_are_blocked(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    result = AutonomousSupervisorService(settings, object(), database).run_loop(enabled_config(max_cycles=3))

    assert result.final_status == AutonomousSupervisorFinalStatus.BLOCKED_BY_SAFETY
    assert result.cycle_count == 1
    assert result.paper_orders_created == 0
    assert FakeDemoBotService.calls == []
    assert "ALLOW_LIVE_TRADING" in (result.stop_reason or "")


def test_supervisor_does_not_mutate_env_file(settings, database, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    original = "EXECUTION_MODE=paper\nALLOW_LIVE_TRADING=false\n"
    env_file.write_text(original, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    AutonomousSupervisorService(settings, object(), database).run_loop(enabled_config(max_cycles=1))

    assert env_file.read_text(encoding="utf-8") == original


def test_no_broker_live_order_submission_or_daemon_behavior_in_supervisor_source() -> None:
    source = Path(autonomous_module.__file__).read_text(encoding="utf-8")
    forbidden_call = "order" + "_" + "send"

    assert forbidden_call not in source
    assert "broker_live_enabled = True" not in source
    assert "live_execution_allowed = True" not in source
    assert "while True" not in source
    assert "threading" not in source
    assert "multiprocessing" not in source
    assert "daemon=True" not in source


def test_compatibility_module_only_reexports_canonical_api() -> None:
    import app.supervisor.autonomous as compat

    assert compat.AutonomousSupervisorService is autonomous_module.AutonomousSupervisorService
    assert compat.AutonomousSupervisorConfig is autonomous_module.AutonomousSupervisorConfig
    compat_source = Path(compat.__file__).read_text(encoding="utf-8")
    assert "class AutonomousSupervisorService" not in compat_source
    assert "def run_loop" not in compat_source


def test_report_schema_uses_stable_canonical_fields(settings, database, tmp_path) -> None:
    result = AutonomousSupervisorService(settings, object(), database).run_loop(
        enabled_config(export_json=True, export_txt=True, reports_dir=tmp_path)
    )

    payload = json.loads((tmp_path / "autonomous_supervisor_summary.json").read_text(encoding="utf-8"))
    required = {
        "started_at",
        "completed_at",
        "cycle_count",
        "style",
        "symbols",
        "watchlist",
        "dry_run",
        "final_status",
        "stop_reason",
        "orders_created",
        "risk_summaries",
        "safety_flags",
    }
    assert required <= payload.keys()
    assert "paper_orders_created" not in payload
    assert payload["orders_created"] == result.orders_created == result.paper_orders_created == 1
    assert payload["safety_flags"]["live_execution_allowed"] is False
    assert payload["safety_flags"]["broker_order_submission_allowed"] is False
    assert payload["safety_flags"]["hidden_daemon_created"] is False
    assert payload["safety_flags"]["infinite_loop_default"] is False
    assert payload["risk_summaries"]
    txt = (tmp_path / "autonomous_supervisor_report.txt").read_text(encoding="utf-8")
    assert "orders_created: 1" in txt
    assert "Safety flags proving live execution was not allowed" in txt


def test_safety_lock_runs_before_demo_bot_cycle(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_ensure(*args, **kwargs) -> None:
        calls.append("ensure_demo_bot_safe_mode")

    class OrderedFakeDemoBotService(FakeDemoBotService):
        def run_cycle(self, style: TradingStyle, symbols: list[str], watchlist: str | None = None) -> DemoBotCycleResult:
            calls.append("demo_bot_run_cycle")
            return super().run_cycle(style, symbols, watchlist)

    monkeypatch.setattr(autonomous_module, "ensure_demo_bot_safe_mode", fake_ensure)
    monkeypatch.setattr(autonomous_module, "DemoBotService", OrderedFakeDemoBotService)

    AutonomousSupervisorService(settings, object(), database).run_loop(enabled_config(max_cycles=1))

    assert calls == ["ensure_demo_bot_safe_mode", "demo_bot_run_cycle"]


def test_supervisor_source_does_not_print_broker_credential_values() -> None:
    source = Path(autonomous_module.__file__).read_text(encoding="utf-8")
    lowered = source.lower()

    assert "mt5_password" not in lowered
    assert "mt5_login" not in lowered
    assert "credential" not in lowered


def test_cli_help_documents_canonical_options_and_legacy_aliases() -> None:
    import subprocess

    completed = subprocess.run(
        ["python", "scripts/run_autonomous_supervisor.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    help_text = completed.stdout
    for option in [
        "--style",
        "--symbols",
        "--watchlist",
        "--once",
        "--max-cycles",
        "--cycles",
        "--interval-seconds",
        "--dry-run",
        "--no-dry-run",
        "--export-json",
        "--export-txt",
        "--no-export",
        "--no-sleep",
    ]:
        assert option in help_text

def test_supervisor_never_bypasses_readiness_gate_if_not_dry_run(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    def blocking_readiness(*args, **kwargs) -> AutonomousReadinessReport:
        now = datetime.now(timezone.utc)
        return AutonomousReadinessReport(
            generated_at=now,
            final_status=AutonomousReadinessFinalStatus.BLOCKED_BY_SAFETY,
            ready=False,
            dry_run_allowed=False,
            paper_run_allowed=False,
            blocking_reasons=["central safety mode is degraded"],
            checks=[],
        )
    monkeypatch.setattr(autonomous_module, "build_readiness_report", blocking_readiness)

    result = AutonomousSupervisorService(settings, object(), database).run_loop(
        enabled_config(max_cycles=3, dry_run=False, skip_readiness_gate=False)
    )

    assert result.final_status == AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS
    assert result.cycle_count == 0
    assert result.paper_orders_created == 0
    assert FakeDemoBotService.calls == []
