from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.config.settings import load_settings
from app.core.types import DirectionBias, SetupFamily, SetupSubtype, Timeframe, TradingStyle
from app.execution.models import CloseReason, ExecutionOrder, OrderRequest, OrderStatus, TradeEventType
from app.execution.autonomous_evidence import AutonomousEvidenceFinalStatus
from app.execution.autonomous_readiness import AutonomousReadinessFinalStatus
from app.execution.realtime_data_health import RealtimeDataHealthReport, RealtimeDataHealthStatus
from app.execution.realtime_paper_positions import RealtimePaperPositionConfig, RealtimePaperPositionManagerService
from app.execution.realtime_paper_supervisor import RealtimePaperSupervisorConfig, RealtimePaperSupervisorService, RealtimePaperStopReason
from app.storage.database import Database

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class StaticProvider:
    name = "synthetic"

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def get_ohlcv(self, symbol, timeframe, start=None, end=None):
        frame = self.frame.copy()
        frame.attrs["provider"] = self.name
        return frame


class FakeHealth:
    def check(self, config):
        return RealtimeDataHealthReport(
            started_at=NOW,
            completed_at=NOW,
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            status=RealtimeDataHealthStatus.REALTIME_DATA_READY,
            latest_data_age_seconds=1,
            data_health_status=RealtimeDataHealthStatus.REALTIME_DATA_READY.value,
            safe_for_realtime_paper=True,
            provider_fallback_status="not_used",
            synthetic_fallback_used=False,
            mt5_used=False,
            checks=[],
            blocking_reasons=[],
            warnings=[],
        )


def patch_ready(monkeypatch):
    monkeypatch.setattr(
        "app.execution.realtime_paper_supervisor.build_evidence",
        lambda *a, **k: SimpleNamespace(final_status=AutonomousEvidenceFinalStatus.READY_EVIDENCE, blocking_failures=[], model_dump=lambda mode="json": {}),
    )
    monkeypatch.setattr(
        "app.execution.realtime_paper_supervisor.build_readiness_report",
        lambda *a, **k: SimpleNamespace(final_status=AutonomousReadinessFinalStatus.READY, blocking_reasons=[], model_dump=lambda mode="json": {}),
    )
    monkeypatch.setattr(
        "app.execution.realtime_paper_supervisor.AutonomousPolicyEngine",
        lambda: SimpleNamespace(can_run_supervisor_cycle=lambda ctx: SimpleNamespace(decision=SimpleNamespace(value="ALLOW"), allowed=True, blocking_reasons=[])),
    )


@pytest.fixture
def settings():
    cfg = load_settings().model_copy(deep=True)
    cfg.execution.estimated_slippage_pips = 0.0
    cfg.execution.spread_aware_fills = False
    cfg.execution.partial_exit_fractions.tp1 = 0.33
    cfg.execution.partial_exit_fractions.tp2 = 0.33
    cfg.execution.partial_exit_fractions.tp3 = 0.34
    cfg.execution.move_stop_to_breakeven_after_tp1 = False
    return cfg


def bars(*, high: float, low: float, close: float | None = None, open_: float | None = None, periods: int = 3, spread: float = 0.00005, end: datetime = NOW) -> pd.DataFrame:
    index = pd.date_range(end=end, periods=periods, freq="1min", tz=timezone.utc)
    return pd.DataFrame(
        {
            "open": [open_ if open_ is not None else close or (high + low) / 2] * periods,
            "high": [high] * periods,
            "low": [low] * periods,
            "close": [close or (high + low) / 2] * periods,
            "volume": [100.0] * periods,
            "spread": [spread] * periods,
        },
        index=index,
    )


def request(**updates) -> OrderRequest:
    data = dict(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=1.0,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1150,
        tp1=None,
        tp2=None,
        tp3=None,
        signal_timestamp=NOW,
        source_status="approved",
    )
    data.update(updates)
    return OrderRequest(**data)


def order(status: OrderStatus = OrderStatus.PENDING_OPPORTUNITY, **updates) -> ExecutionOrder:
    req = request(**updates.pop("request_updates", {}))
    return ExecutionOrder(
        order_id=updates.pop("order_id", "order-1"),
        request=req,
        status=status,
        created_at=NOW,
        signal_timestamp=NOW,
        activated_at=updates.pop("activated_at", NOW if status in {OrderStatus.OPEN_TRADE, OrderStatus.PARTIALLY_CLOSED} else None),
        entry_timestamp=updates.pop("entry_timestamp", NOW if status in {OrderStatus.OPEN_TRADE, OrderStatus.PARTIALLY_CLOSED} else None),
        simulated_entry=updates.pop("simulated_entry", req.entry_price if status in {OrderStatus.OPEN_TRADE, OrderStatus.PARTIALLY_CLOSED} else None),
        initial_stop_loss=req.stop_loss,
        bars_in_trade=updates.pop("bars_in_trade", 0 if status in {OrderStatus.OPEN_TRADE, OrderStatus.PARTIALLY_CLOSED} else None),
        **updates,
    )


def database(tmp_path: Path, orders: list[ExecutionOrder], settings) -> Database:
    db = Database(tmp_path / "paper.sqlite3")
    db.save_paper_orders(orders)
    return db


def run_manager(tmp_path: Path, settings, frame: pd.DataFrame, orders: list[ExecutionOrder], *, dry_run: bool = False, **cfg_updates):
    db = database(tmp_path, orders, settings)
    config = RealtimePaperPositionConfig(
        provider="synthetic",
        symbols=["EUR/USD"],
        timeframe=Timeframe.M1,
        dry_run=dry_run,
        reports_dir=tmp_path,
        export_json=True,
        export_txt=True,
        **cfg_updates,
    )
    report = RealtimePaperPositionManagerService(settings, StaticProvider(frame), db, now_fn=lambda: NOW).evaluate_position_lifecycle(config)
    return report, db


def test_pending_order_activates_when_price_reaches_entry(settings, tmp_path):
    report, db = run_manager(tmp_path, settings, bars(high=1.1010, low=1.0990), [order()])
    updated = db.load_paper_orders()[0]
    assert updated.status == OrderStatus.OPEN_TRADE
    assert report.activations == 1
    assert any(event.event_type == TradeEventType.TRADE_ACTIVATED for event in updated.events)


def test_stop_loss_closes_paper_position(settings, tmp_path):
    report, db = run_manager(tmp_path, settings, bars(high=1.1010, low=1.0940), [order(OrderStatus.OPEN_TRADE)])
    updated = db.load_paper_orders()[0]
    assert updated.status == OrderStatus.FULLY_CLOSED
    assert updated.close_reason == CloseReason.STOP_LOSS
    assert report.positions_closed == 1


def test_take_profit_closes_paper_position(settings, tmp_path):
    settings.execution.partial_exit_fractions.tp1 = 0
    settings.execution.partial_exit_fractions.tp2 = 0
    settings.execution.partial_exit_fractions.tp3 = 0
    report, db = run_manager(tmp_path, settings, bars(high=1.1160, low=1.1000), [order(OrderStatus.OPEN_TRADE)])
    updated = db.load_paper_orders()[0]
    assert updated.status == OrderStatus.FULLY_CLOSED
    assert updated.close_reason == CloseReason.TAKE_PROFIT
    assert report.positions_closed == 1


def test_tp1_partial_exit_creates_audit_event(settings, tmp_path):
    paper_order = order(OrderStatus.OPEN_TRADE, request_updates={"tp1": 1.1050})
    report, db = run_manager(tmp_path, settings, bars(high=1.1060, low=1.1000), [paper_order])
    updated = db.load_paper_orders()[0]
    assert updated.status == OrderStatus.PARTIALLY_CLOSED
    assert updated.partial_exits[0].target == "tp1"
    assert report.partial_exits_created == 1
    assert any(event.event_type == TradeEventType.TRADE_PARTIALLY_CLOSED for event in updated.events)


def test_stop_moves_to_breakeven_after_tp1_when_configured(settings, tmp_path):
    settings.execution.move_stop_to_breakeven_after_tp1 = True
    paper_order = order(OrderStatus.OPEN_TRADE, request_updates={"tp1": 1.1050})
    report, db = run_manager(tmp_path, settings, bars(high=1.1060, low=1.1000), [paper_order])
    updated = db.load_paper_orders()[0]
    assert updated.request.stop_loss == updated.request.entry_price
    assert report.breakeven_moves == 1
    assert any(event.event_type == TradeEventType.STOP_MOVED for event in updated.events)


def test_invalidation_before_activation_blocks_or_cancels_paper_order(settings, tmp_path):
    report, db = run_manager(tmp_path, settings, bars(high=1.0970, low=1.0940), [order()])
    updated = db.load_paper_orders()[0]
    assert updated.status == OrderStatus.CANCELLED_TRADE
    assert updated.close_reason == CloseReason.SETUP_INVALIDATED
    assert report.invalidations == 1


def test_stale_data_blocks_lifecycle_update(settings, tmp_path):
    stale = bars(high=1.1010, low=1.0990, end=datetime(2025, 1, 1, tzinfo=timezone.utc))
    report, db = run_manager(tmp_path, settings, stale, [order()])
    assert db.load_paper_orders()[0].status == OrderStatus.PENDING_OPPORTUNITY
    assert report.positions_updated == 0
    assert report.blocking_reasons


def test_high_spread_warns_or_blocks_according_to_config(settings, tmp_path):
    wide = bars(high=1.1010, low=1.0990, spread=0.0100)
    warn_report, warn_db = run_manager(tmp_path / "warn", settings, wide, [order()])
    assert warn_report.warnings
    assert warn_db.load_paper_orders()[0].status == OrderStatus.OPEN_TRADE
    block_report, block_db = run_manager(tmp_path / "block", settings, wide, [order()], block_on_wide_spread=True)
    assert block_report.blocking_reasons
    assert block_db.load_paper_orders()[0].status == OrderStatus.PENDING_OPPORTUNITY


def test_dry_run_does_not_persist_destructive_updates(settings, tmp_path):
    report, db = run_manager(tmp_path, settings, bars(high=1.1010, low=1.0940), [order(OrderStatus.OPEN_TRADE)], dry_run=True)
    assert report.positions_closed == 1
    assert db.load_paper_orders()[0].status == OrderStatus.OPEN_TRADE


def test_no_order_send_no_live_trading_no_env_mutation_no_mt5_required(settings, tmp_path, monkeypatch):
    mt5 = SimpleNamespace(order_send=lambda *a, **k: pytest.fail("order_send must not be called"))
    monkeypatch.setitem(__import__("sys").modules, "MetaTrader5", mt5)
    env_path = Path(".env")
    before = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    report, _ = run_manager(tmp_path, settings, bars(high=1.1010, low=1.0990), [order()])
    after = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    assert report.safety_flags["live_execution_allowed"] is False
    assert report.safety_flags["order_send_called"] is False
    assert before == after


def test_cli_exports_position_reports(settings, tmp_path, monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    code = os.system(f"python scripts/realtime_paper_positions.py --provider synthetic --symbols EUR/USD --timeframe M1 --dry-run --export-json --export-txt --reports-dir {tmp_path}")
    assert code == 0
    payload = json.loads((tmp_path / "realtime_paper_positions.json").read_text(encoding="utf-8"))
    assert payload["provider"] == "synthetic"
    assert (tmp_path / "realtime_paper_positions.txt").exists()


def test_realtime_supervisor_manage_positions_exports_summary(settings, tmp_path, monkeypatch):
    patch_ready(monkeypatch)
    db = database(tmp_path, [order()], settings)
    config = RealtimePaperSupervisorConfig(
        provider="synthetic",
        symbols=["EUR/USD"],
        timeframe=Timeframe.M1,
        interval_seconds=0,
        max_cycles=1,
        dry_run=False,
        manage_positions=True,
        reports_dir=tmp_path,
        export_json=True,
        export_txt=True,
    )
    service = RealtimePaperSupervisorService(settings, StaticProvider(bars(high=1.1010, low=1.0990)), db, data_health_service=FakeHealth(), now_fn=lambda: NOW)
    report = service.run(config)
    assert report.stop_reason == RealtimePaperStopReason.COMPLETED_MAX_CYCLES.value
    assert report.positions_updated == 1
    assert report.position_lifecycle_summary["manage_positions"] is True
    assert (tmp_path / "realtime_paper_positions.json").exists()
