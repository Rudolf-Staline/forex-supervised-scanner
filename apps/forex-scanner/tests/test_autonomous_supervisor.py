"""Autonomous Supervisor v0 paper/demo safety tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import app.supervisor.autonomous as autonomous_module
from app.core.types import TradingStyle
from app.execution.demo_bot import DemoBotCycleResult, DemoBotDecision
from app.risk.daily_limits import DailyRiskSummary
from app.storage.database import Database
from app.supervisor.autonomous import AutonomousSupervisorConfig, AutonomousSupervisorService


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "supervisor.sqlite")


class FakeDemoBotService:
    calls: list[tuple[TradingStyle, list[str], str | None]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def run_cycle(self, style: TradingStyle, symbols: list[str], watchlist: str | None = None) -> DemoBotCycleResult:
        self.calls.append((style, symbols, watchlist))
        now = datetime.now(timezone.utc)
        return DemoBotCycleResult(
            cycle_id="cycle-1",
            started_at=now,
            completed_at=now,
            style=style,
            symbols=symbols,
            opportunities=2,
            orders_created=1,
            decisions=[
                DemoBotDecision(symbol=symbols[0], status="approved", setup_subtype="breakout", accepted=True, order_ids=["paper-1"]),
                DemoBotDecision(symbol=symbols[-1], status="watchlist", setup_subtype="pullback", accepted=False, reasons=["status watchlist is not executable by demo bot"]),
            ],
            logs=["fake cycle"],
            risk_summary=DailyRiskSummary(
                trades_today=1,
                open_trades=1,
                daily_pnl=0.0,
                daily_loss_percent=0.0,
                remaining_trade_slots=4,
                bot_risk_status="OK",
                consecutive_losses=0,
            ),
        )


def test_autonomous_supervisor_runs_bounded_paper_cycle(settings, database, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDemoBotService.calls = []
    monkeypatch.setattr(autonomous_module, "DemoBotService", FakeDemoBotService)

    config = AutonomousSupervisorConfig(
        symbols=["eur/usd", "gbp/usd"],
        cycles=1,
        export_reports=True,
        reports_dir=tmp_path / "reports",
    )
    result = AutonomousSupervisorService(settings, object(), database).run(config)

    assert result.status == "PAPER_DEMO_COMPLETED"
    assert result.completed_cycles == 1
    assert result.total_paper_orders_created == 1
    assert result.paper_demo_only is True
    assert result.live_trading_enabled is False
    assert result.broker_mode == "paper"
    assert result.mt5_called is False
    assert result.broker_orders_sent is False
    assert result.hidden_daemon_created is False
    assert result.subprocess_used is False
    assert FakeDemoBotService.calls == [(TradingStyle.DAY_TRADING, ["EUR/USD", "GBP/USD"], None)]
    assert (tmp_path / "reports" / "autonomous_supervisor_last_run.json").exists()
    assert (tmp_path / "reports" / "autonomous_supervisor_last_run.md").exists()


def test_autonomous_supervisor_blocks_live_environment(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDemoBotService.calls = []
    monkeypatch.setattr(autonomous_module, "DemoBotService", FakeDemoBotService)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    result = AutonomousSupervisorService(settings, object(), database).run(
        AutonomousSupervisorConfig(symbols=["EUR/USD"], export_reports=False)
    )

    assert result.status == "BLOCKED"
    assert result.completed_cycles == 0
    assert result.total_paper_orders_created == 0
    assert FakeDemoBotService.calls == []
    assert any("ALLOW_LIVE_TRADING" in reason for reason in result.blocking_reasons)


def test_autonomous_supervisor_uses_watchlist_symbols(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDemoBotService.calls = []
    monkeypatch.setattr(autonomous_module, "DemoBotService", FakeDemoBotService)

    result = AutonomousSupervisorService(settings, object(), database).run(
        AutonomousSupervisorConfig(watchlist="jpy_pairs", symbols=["EUR/USD"], export_reports=False)
    )

    assert result.symbols == ["USD/JPY", "EUR/JPY", "GBP/JPY"]
    assert FakeDemoBotService.calls == [(TradingStyle.DAY_TRADING, ["USD/JPY", "EUR/JPY", "GBP/JPY"], "jpy_pairs")]
