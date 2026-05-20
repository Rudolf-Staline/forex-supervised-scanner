"""Paper-only demo bot tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.execution.demo_bot as demo_bot_module
from app.config.safety import DemoSafetyError
from app.core.types import (
    ConfidenceBucket,
    DataQualityDiagnostic,
    DirectionBias,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    ScanReport,
    SessionName,
    SetupFamily,
    SetupSubtype,
    Timeframe,
    TradingStyle,
)
from app.execution.demo_bot import DemoBotService
from app.execution.demo_bot_state import DemoBotRuntimeState
from app.execution.models import TradeEventType
from app.execution.operations import OperatorControlState
from app.paper.trading import close_paper_order_manually, submit_signal_to_paper
from app.storage.database import Database


class FakeScannerService:
    opportunities: list[Opportunity] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def scan(self, style: TradingStyle, symbols: list[str], timestamp: datetime | None = None) -> ScanReport:
        scan_time = timestamp or datetime.now(timezone.utc)
        opportunities = [
            opportunity.model_copy(update={"timestamp": scan_time, "style": style})
            for opportunity in self.opportunities
            if opportunity.symbol in symbols
        ]
        return ScanReport(timestamp=scan_time, style=style, opportunities=opportunities)


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "demo_bot.sqlite")


def test_signal_rejected_not_executed(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity(status=OpportunityStatus.REJECTED, score=92.0)]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 0
    assert not database.load_paper_orders()
    assert not result.decisions[0].accepted
    assert "status rejected is not executable by demo bot" in result.decisions[0].reasons
    assert any("REJECT EUR/USD" in line for line in result.logs)
    assert any(event.event_type == TradeEventType.DEMO_BOT_DECISION_REJECTED for event in database.load_trade_events())


def test_watchlist_not_executed(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity(status=OpportunityStatus.WATCHLIST, score=92.0)]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 0
    assert not database.load_paper_orders()
    assert not result.decisions[0].accepted
    assert "status watchlist is not executable by demo bot" in result.decisions[0].reasons
    events = database.load_trade_events()
    assert any(event.event_type == TradeEventType.DEMO_BOT_DECISION_REJECTED for event in events)


def test_approved_signal_can_execute(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity()]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 1
    assert result.decisions[0].accepted
    assert database.load_paper_orders()[0].broker_mode is None
    events = database.load_trade_events()
    assert any(event.event_type == TradeEventType.DEMO_BOT_DECISION_ACCEPTED for event in events)
    assert any("created paper order" in line for line in result.logs)


def test_duplicate_symbol_blocked(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity()]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)
    service = DemoBotService(settings, object(), database)

    first = service.run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])
    second = service.run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert first.orders_created == 1
    assert second.orders_created == 0
    assert len(database.load_paper_orders()) == 1
    assert any("open paper position already exists" in reason for reason in second.decisions[0].reasons)


def test_max_open_trades_blocked(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_BOT_MAX_OPEN_TRADES", "1")
    FakeScannerService.opportunities = [_opportunity(symbol="EUR/USD"), _opportunity(symbol="GBP/USD")]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD", "GBP/USD"])

    assert result.orders_created == 1
    assert len(database.load_paper_orders()) == 1
    rejected = [decision for decision in result.decisions if not decision.accepted]
    assert rejected
    assert any("max open trades 1 reached" in reason for decision in rejected for reason in decision.reasons)


def test_live_mode_always_blocked(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity()]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_live"
    adjusted.broker.live_enabled = True
    adjusted.execution_capabilities.broker_live_enabled = True

    with pytest.raises(DemoSafetyError, match="broker_live"):
        DemoBotService(adjusted, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])


def test_detected_signal_not_executed(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity(status=OpportunityStatus.DETECTED, score=92.0)]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 0
    assert not result.decisions[0].accepted
    assert "status detected is not executable by demo bot" in result.decisions[0].reasons


def test_bad_levels_spread_and_session_are_blocked(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [
        _opportunity(symbol="EUR/USD", stop_loss=1.1010),
        _opportunity(symbol="GBP/USD", spread=0.002, atr=0.001),
        _opportunity(symbol="USD/CHF", session=SessionName.OFF_HOURS),
    ]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD", "GBP/USD", "USD/CHF"])
    reasons = [reason for decision in result.decisions for reason in decision.reasons]

    assert result.orders_created == 0
    assert any("stop loss must be below entry" in reason for reason in reasons)
    assert any("spread/ATR" in reason for reason in reasons)
    assert any("off-hours session" in reason for reason in reasons)
    assert any(event.event_type == TradeEventType.DEMO_BOT_DECISION_REJECTED for event in database.load_trade_events())


def test_degraded_mode_blocks_and_logs_decision(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity()]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)
    database.save_operator_controls(OperatorControlState(updated_at=datetime.now(timezone.utc), degraded_mode=True))

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 0
    assert "operator degraded mode is active" in result.decisions[0].reasons
    assert any("degraded mode is active" in line for line in result.logs)
    assert any(event.event_type == TradeEventType.DEMO_BOT_DECISION_REJECTED for event in database.load_trade_events())


def test_score_rr_data_quality_and_missing_levels_are_blocked(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [
        _opportunity(symbol="EUR/USD", score=70.0, final_score=70.0),
        _opportunity(symbol="GBP/USD", risk_reward=1.0),
        _opportunity(
            symbol="USD/CHF",
            data_quality=DataQualityDiagnostic(score=40.0, missing_bars=8, stale_minutes=0.0, spread_available=True, resampled=False),
        ),
        _opportunity(symbol="AUD/USD", entry=None),
    ]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD", "GBP/USD", "USD/CHF", "AUD/USD"])
    reasons = [reason for decision in result.decisions for reason in decision.reasons]

    assert result.orders_created == 0
    assert any("score 70.0 below minimum 75.0" in reason for reason in reasons)
    assert any("risk/reward 1.00 below minimum 1.50" in reason for reason in reasons)
    assert any("data quality 40.0 below paper-entry threshold 60.0" in reason for reason in reasons)
    assert any("missing executable levels: entry" in reason for reason in reasons)
    assert all(any(f"REJECT {decision.symbol}" in line for line in result.logs) for decision in result.decisions)


def test_incoherent_take_profits_are_blocked(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [
        _opportunity(symbol="EUR/USD", take_profit=1.0990, tp1=1.0990, tp2=1.0980, tp3=1.0970)
    ]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])
    reasons = result.decisions[0].reasons

    assert result.orders_created == 0
    assert "take profit must be above entry for a long setup" in reasons
    assert "tp1 must be above entry for a long setup" in reasons
    assert "tp2 must be above entry for a long setup" in reasons
    assert "tp3 must be above entry for a long setup" in reasons


def test_daily_trade_cap_blocks_new_bot_trade(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_BOT_MAX_TRADES_PER_DAY", "1")
    submission = submit_signal_to_paper(_opportunity(symbol="EUR/USD"), settings=settings, database=database, source="demo_bot")
    assert submission.order is not None
    close_paper_order_manually(submission.order, settings=settings, database=database, exit_price=1.1010)
    FakeScannerService.opportunities = [_opportunity(symbol="GBP/USD")]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["GBP/USD"])

    assert result.orders_created == 0
    assert "daily trade cap 1 reached" in result.decisions[0].reasons
    assert any("daily trade cap 1 reached" in line for line in result.logs)


def test_cooldown_blocks_recent_symbol_even_after_close(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    submission = submit_signal_to_paper(_opportunity(symbol="EUR/USD"), settings=settings, database=database, source="demo_bot")
    assert submission.order is not None
    closed = close_paper_order_manually(submission.order, settings=settings, database=database, exit_price=1.1010)
    assert closed.closed_at is not None and datetime.now(timezone.utc) - closed.closed_at < timedelta(minutes=30)
    FakeScannerService.opportunities = [_opportunity(symbol="EUR/USD")]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 0
    assert "cooldown active for EUR/USD" in result.decisions[0].reasons


def test_maintenance_mode_blocks_and_logs_decision(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = [_opportunity()]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)
    database.save_operator_controls(OperatorControlState(updated_at=datetime.now(timezone.utc), maintenance_mode=True))

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])

    assert result.orders_created == 0
    assert "operator maintenance mode is active" in result.decisions[0].reasons
    assert any("maintenance mode is active" in line for line in result.logs)
    assert any(event.event_type == TradeEventType.DEMO_BOT_DECISION_REJECTED for event in database.load_trade_events())


def test_cycle_always_persists_started_and_completed_audit_events(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeScannerService.opportunities = []
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["EUR/USD"])
    events = database.load_trade_events(result.cycle_id)

    assert result.orders_created == 0
    assert [event.event_type for event in events] == [
        TradeEventType.DEMO_BOT_CYCLE_STARTED,
        TradeEventType.DEMO_BOT_CYCLE_COMPLETED,
    ]


def test_demo_bot_runtime_state_is_stopped_by_default() -> None:
    state = DemoBotRuntimeState()

    assert state.status == "STOPPED"
    assert not state.running
    assert not state.due_for_cycle(300)


def _opportunity(
    *,
    status: OpportunityStatus = OpportunityStatus.APPROVED,
    score: float = 82.0,
    symbol: str = "EUR/USD",
    **updates: object,
) -> Opportunity:
    approved = status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}
    payload: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc),
        "symbol": symbol,
        "style": TradingStyle.DAY_TRADING,
        "setup_family": SetupFamily.TREND_CONTINUATION,
        "setup_subtype": SetupSubtype.SHALLOW_EMA20_PULLBACK,
        "regime": MarketRegime.TRENDING_UP,
        "direction": DirectionBias.LONG,
        "score": score,
        "confidence": ConfidenceBucket.HIGH,
        "entry": 1.1000,
        "stop_loss": 1.0950,
        "take_profit": 1.1100,
        "risk_reward": 2.0,
        "explanation": "Approved demo setup for paper execution.",
        "timeframe_higher": Timeframe.H1,
        "timeframe_entry": Timeframe.M15,
        "timeframe_trigger": Timeframe.M5,
        "score_components": {"trend_clarity": score},
        "provider": "synthetic",
        "approved": approved,
        "status": status,
        "raw_setup_family": SetupFamily.TREND_CONTINUATION,
        "pre_gate_score": score,
        "technical_score": score,
        "execution_score": score,
        "context_score": score,
        "empirical_score": score,
        "final_score": score,
        "required_min_rr": 1.5,
        "tp1": 1.1050,
        "tp2": 1.1100,
        "tp3": 1.1150,
        "activation_quality": 85.0,
        "invalidation_quality": 85.0,
        "spread": 0.00005,
        "atr": 0.001,
        "session": SessionName.LONDON,
        "htf_regime": MarketRegime.TRENDING_UP,
        "entry_regime": MarketRegime.TRENDING_UP,
        "trigger_regime": MarketRegime.TRENDING_UP,
        "data_quality": DataQualityDiagnostic(
            score=95.0,
            missing_bars=0,
            stale_minutes=0.0,
            spread_available=True,
            resampled=False,
        ),
    }
    payload.update(updates)
    return Opportunity(**payload)
