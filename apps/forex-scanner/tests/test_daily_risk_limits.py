"""Daily risk limit tests for the paper/demo bot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.execution.demo_bot as demo_bot_module
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
from app.execution.models import CloseReason, ExecutionOrder, OrderRequest, OrderStatus
from app.execution.paper import PaperExecutor
from app.risk.daily_limits import DailyRiskConfig, evaluate_daily_limits, summarize_daily_risk
from app.storage.database import Database


class FakeScannerService:
    opportunities: list[Opportunity] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def scan(self, style: TradingStyle, symbols: list[str], timestamp: datetime | None = None) -> ScanReport:
        scan_time = timestamp or datetime.now(timezone.utc)
        return ScanReport(
            timestamp=scan_time,
            style=style,
            opportunities=[
                opportunity.model_copy(update={"timestamp": scan_time, "style": style})
                for opportunity in self.opportunities
                if opportunity.symbol in symbols
            ],
        )


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "daily-risk.sqlite")


def test_daily_limits_block_duplicate_symbol(settings) -> None:
    now = datetime.now(timezone.utc)
    order = _order(settings, symbol="EUR/USD", created_at=now)

    decision = evaluate_daily_limits(
        [order],
        symbol="EUR/USD",
        now=now,
        config=DailyRiskConfig(cooldown_after_trade_minutes=0),
    )

    assert not decision.allowed
    assert "open paper position already exists for EUR/USD" in decision.reasons


def test_daily_limits_block_max_open_trades(settings) -> None:
    now = datetime.now(timezone.utc)
    orders = [_order(settings, symbol="EUR/USD", created_at=now), _order(settings, symbol="GBP/USD", created_at=now)]

    decision = evaluate_daily_limits(
        orders,
        symbol="USD/CHF",
        now=now,
        config=DailyRiskConfig(max_open_trades=2, cooldown_after_trade_minutes=0),
    )

    assert not decision.allowed
    assert "max open trades 2 reached" in decision.reasons


def test_daily_limits_block_max_trades_per_day(settings) -> None:
    now = datetime.now(timezone.utc)
    orders = [
        _closed_order(settings, symbol="EUR/USD", created_at=now, closed_at=now, realized_r=1.0),
        _closed_order(settings, symbol="GBP/USD", created_at=now, closed_at=now, realized_r=1.0),
        _closed_order(settings, symbol="USD/CHF", created_at=now, closed_at=now, realized_r=1.0),
    ]

    decision = evaluate_daily_limits(
        orders,
        symbol="AUD/USD",
        now=now,
        config=DailyRiskConfig(max_trades_per_day=3, cooldown_after_trade_minutes=0),
    )

    assert not decision.allowed
    assert "daily trade cap 3 reached" in decision.reasons
    assert decision.summary.remaining_trade_slots == 0


def test_daily_limits_block_daily_loss_percent(settings) -> None:
    now = datetime.now(timezone.utc)
    orders = [
        _closed_order(settings, symbol="EUR/USD", created_at=now, closed_at=now, realized_r=-4.0),
        _closed_order(settings, symbol="GBP/USD", created_at=now, closed_at=now, realized_r=-4.1),
    ]

    decision = evaluate_daily_limits(
        orders,
        symbol="USD/CHF",
        now=now,
        config=DailyRiskConfig(max_daily_loss_percent=2.0, cooldown_after_loss_minutes=0, cooldown_after_trade_minutes=0),
        risk_per_trade_percent=0.25,
    )

    assert not decision.allowed
    assert any("daily loss 2.02% reached limit 2.00%" in reason for reason in decision.reasons)


def test_daily_limits_block_consecutive_losses_and_loss_cooldown(settings) -> None:
    now = datetime.now(timezone.utc)
    orders = [
        _closed_order(settings, symbol="EUR/USD", created_at=now, closed_at=now - timedelta(minutes=10), realized_r=-1.0),
        _closed_order(settings, symbol="GBP/USD", created_at=now, closed_at=now - timedelta(minutes=20), realized_r=-1.0),
        _closed_order(settings, symbol="USD/CHF", created_at=now, closed_at=now - timedelta(minutes=30), realized_r=-1.0),
    ]

    decision = evaluate_daily_limits(
        orders,
        symbol="AUD/USD",
        now=now,
        config=DailyRiskConfig(max_consecutive_losses=3, cooldown_after_loss_minutes=60, cooldown_after_trade_minutes=0),
    )

    assert not decision.allowed
    assert "consecutive losses 3 reached limit 3" in decision.reasons
    assert "cooldown after loss is active" in decision.reasons


def test_bot_uses_daily_limits_and_persists_risk_summary(settings, database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TRADES_PER_DAY", "1")
    monkeypatch.setenv("MAX_OPEN_TRADES", "2")
    monkeypatch.setenv("COOLDOWN_AFTER_TRADE_MINUTES", "0")
    existing = _closed_order(settings, symbol="EUR/USD", created_at=datetime.now(timezone.utc), closed_at=datetime.now(timezone.utc), realized_r=1.0)
    database.save_paper_orders([existing])
    FakeScannerService.opportunities = [_opportunity(symbol="GBP/USD")]
    monkeypatch.setattr(demo_bot_module, "ScannerService", FakeScannerService)

    result = DemoBotService(settings, object(), database).run_cycle(TradingStyle.DAY_TRADING, ["GBP/USD"])

    assert result.orders_created == 0
    assert result.risk_summary.trades_today == 1
    assert result.risk_summary.remaining_trade_slots == 0
    assert "daily trade cap 1 reached" in result.decisions[0].reasons
    assert any("Risk summary:" in line for line in result.logs)
    completed = database.load_trade_events(result.cycle_id)[-1]
    assert completed.payload["trades_today"] == 1
    assert completed.payload["bot_risk_status"] == "blocked"


def test_daily_risk_summary_reports_ok_when_capacity_remains(settings) -> None:
    now = datetime.now(timezone.utc)
    summary = summarize_daily_risk(
        [_closed_order(settings, symbol="EUR/USD", created_at=now, closed_at=now, realized_r=1.0)],
        now=now,
        config=DailyRiskConfig(max_trades_per_day=3, max_open_trades=2),
    )

    assert summary.trades_today == 1
    assert summary.open_trades == 0
    assert summary.remaining_trade_slots == 2
    assert summary.bot_risk_status == "ok"


def _order(settings, *, symbol: str, created_at: datetime) -> ExecutionOrder:
    order = PaperExecutor(settings).place_order(_request(symbol=symbol))
    return order.model_copy(update={"created_at": created_at})


def _closed_order(
    settings,
    *,
    symbol: str,
    created_at: datetime,
    closed_at: datetime,
    realized_r: float,
) -> ExecutionOrder:
    order = _order(settings, symbol=symbol, created_at=created_at)
    return order.model_copy(
        update={
            "status": OrderStatus.FULLY_CLOSED,
            "closed_at": closed_at,
            "close_reason": CloseReason.MANUAL,
            "realized_r": realized_r,
            "realized_pnl": realized_r * order.request.quantity_units,
        }
    )


def _request(*, symbol: str) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=1.0,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        tp1=1.1050,
        tp2=1.1100,
        tp3=1.1150,
        source_status="approved",
        final_score=82.0,
        provider="synthetic",
    )


def _opportunity(*, symbol: str = "EUR/USD") -> Opportunity:
    return Opportunity(
        timestamp=datetime.now(timezone.utc),
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        regime=MarketRegime.TRENDING_UP,
        direction=DirectionBias.LONG,
        score=82.0,
        confidence=ConfidenceBucket.HIGH,
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        risk_reward=2.0,
        explanation="Approved demo setup for paper execution.",
        timeframe_higher=Timeframe.H1,
        timeframe_entry=Timeframe.M15,
        timeframe_trigger=Timeframe.M5,
        score_components={"trend_clarity": 82.0},
        provider="synthetic",
        approved=True,
        status=OpportunityStatus.APPROVED,
        raw_setup_family=SetupFamily.TREND_CONTINUATION,
        pre_gate_score=82.0,
        technical_score=82.0,
        execution_score=82.0,
        context_score=82.0,
        empirical_score=82.0,
        final_score=82.0,
        required_min_rr=1.5,
        tp1=1.1050,
        tp2=1.1100,
        tp3=1.1150,
        activation_quality=85.0,
        invalidation_quality=85.0,
        spread=0.00005,
        atr=0.001,
        session=SessionName.LONDON,
        htf_regime=MarketRegime.TRENDING_UP,
        entry_regime=MarketRegime.TRENDING_UP,
        trigger_regime=MarketRegime.TRENDING_UP,
        data_quality=DataQualityDiagnostic(
            score=95.0,
            missing_bars=0,
            stale_minutes=0.0,
            spread_available=True,
            resampled=False,
        ),
    )
