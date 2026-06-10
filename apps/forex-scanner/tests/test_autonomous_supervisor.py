"""Autonomous Supervisor v0 paper/demo safety tests."""

from __future__ import annotations

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
    assert "paper orders created: 1" in txt_path.read_text(encoding="utf-8")


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


def test_no_broker_live_or_order_submission_behavior_in_supervisor_source() -> None:
    source = Path(autonomous_module.__file__).read_text(encoding="utf-8")
    forbidden_call = "order" + "_" + "send"

    assert forbidden_call not in source
    assert "broker_live_enabled = True" not in source
    assert "while True" not in source
