"""Paper execution and portfolio guardrail tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.core.types import (
    ConfidenceBucket,
    DataQualityDiagnostic,
    DirectionBias,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    SetupFamily,
    SetupSubtype,
    SessionName,
    Timeframe,
    TradingStyle,
)
from app.execution.models import CloseReason, ExecutionOrder, OrderRequest, OrderStatus, TradeEventType
from app.execution.paper import PaperExecutor
from app.execution.validation import PreTradeValidator
from app.paper.reporting import generate_paper_portfolio_report
from app.paper.trading import PaperTradingService
from app.risk.guardrails import PortfolioGuardrails


def _request() -> OrderRequest:
    return OrderRequest(
        symbol="EUR/USD",
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
    )


def _bars(highs: list[float], lows: list[float]) -> pd.DataFrame:
    index = pd.date_range(datetime(2025, 1, 1, tzinfo=timezone.utc), periods=len(highs), freq="5min")
    close = [(high + low) / 2.0 for high, low in zip(highs, lows, strict=True)]
    return pd.DataFrame({"open": close, "high": highs, "low": lows, "close": close, "volume": 100.0}, index=index)


def _opportunity(**updates: object) -> Opportunity:
    payload = {
        "timestamp": datetime.now(timezone.utc),
        "symbol": "EUR/USD",
        "style": TradingStyle.DAY_TRADING,
        "setup_family": SetupFamily.TREND_CONTINUATION,
        "setup_subtype": SetupSubtype.SHALLOW_EMA20_PULLBACK,
        "regime": MarketRegime.TRENDING_UP,
        "direction": DirectionBias.LONG,
        "score": 78.0,
        "confidence": ConfidenceBucket.HIGH,
        "entry": 1.1000,
        "stop_loss": 1.0950,
        "take_profit": 1.1100,
        "risk_reward": 2.0,
        "explanation": "paper test",
        "timeframe_higher": Timeframe.H1,
        "timeframe_entry": Timeframe.M15,
        "timeframe_trigger": Timeframe.M5,
        "score_components": {},
        "provider": "synthetic",
        "session": SessionName.LONDON,
        "approved": True,
        "status": OpportunityStatus.APPROVED,
        "raw_setup_family": SetupFamily.TREND_CONTINUATION,
        "technical_score": 76.0,
        "execution_score": 72.0,
        "context_score": 70.0,
        "empirical_score": 58.0,
        "final_score": 76.0,
        "tp1": 1.1050,
        "tp2": 1.1100,
        "tp3": 1.1150,
        "spread": 0.0001,
        "atr": 0.0012,
        "data_quality": DataQualityDiagnostic(score=95.0, missing_bars=0, spread_available=True, resampled=False),
    }
    payload.update(updates)
    return Opportunity.model_validate(payload)


def test_paper_executor_tracks_activation_take_profit_and_realized_r(settings) -> None:
    executor = PaperExecutor(settings)
    order = executor.place_order(_request())

    result = executor.process_market_data("EUR/USD", _bars([1.1010, 1.1160], [1.0995, 1.1030]))
    updated = executor.all_orders()[0]

    assert result.activated == 1
    assert result.closed == 1
    assert updated.status == OrderStatus.FULLY_CLOSED
    assert updated.close_reason == CloseReason.TAKE_PROFIT
    assert updated.bars_to_activation == 1
    assert updated.bars_in_trade == 1
    assert len(updated.partial_exits) == 3
    assert updated.remaining_fraction == 0.0
    assert updated.realized_r is not None and updated.realized_r > 1.0
    event_types = [event.event_type for event in updated.events]
    assert TradeEventType.SIGNAL_APPROVED in event_types
    assert TradeEventType.TRADE_ENTERED in event_types
    assert TradeEventType.TRADE_PARTIALLY_CLOSED in event_types
    assert TradeEventType.TRADE_CLOSED in event_types


def test_paper_executor_supports_modify_cancel_and_sync(settings) -> None:
    executor = PaperExecutor(settings)
    order = executor.place_order(_request())
    modified = executor.modify_order(order.order_id, stop_loss=1.0960)
    assert modified.request.stop_loss == 1.0960
    assert len(executor.sync_positions()) == 1
    canceled = executor.cancel_order(order.order_id)
    assert canceled.status == OrderStatus.CANCELLED_TRADE
    assert executor.query_order_status(order.order_id).status == OrderStatus.CANCELLED_TRADE
    assert executor.sync_positions() == []


def test_paper_executor_tracks_partial_open_trade_and_breakeven_stop(settings) -> None:
    executor = PaperExecutor(settings)
    order = executor.place_order(_request())

    result = executor.process_market_data("EUR/USD", _bars([1.1010, 1.1055], [1.0995, 1.1030]))
    updated = executor.all_orders()[0]

    assert result.activated == 1
    assert result.partials == 1
    assert updated.status == OrderStatus.PARTIALLY_CLOSED
    assert updated.tp1_exit_price is not None
    assert updated.remaining_fraction < 1.0
    assert updated.request.stop_loss == updated.request.entry_price
    assert updated.stop_movements
    assert any(event.event_type == TradeEventType.STOP_MOVED for event in updated.events)


def test_paper_executor_marks_missed_expired_and_invalidated_trades(settings) -> None:
    missed_executor = PaperExecutor(settings)
    missed_executor.place_order(_request())
    missed = missed_executor.process_market_data("EUR/USD", _bars([1.1040], [1.1020]))
    assert missed.missed == 1
    assert missed_executor.all_orders()[0].status == OrderStatus.MISSED_TRADE

    expired_settings = settings.model_copy(deep=True)
    expired_settings.execution.activation_timeout_bars = 2
    expired_executor = PaperExecutor(expired_settings)
    expired_executor.place_order(_request())
    expired = expired_executor.process_market_data("EUR/USD", _bars([1.0990, 1.0992], [1.0970, 1.0975]))
    assert expired.expired == 1
    assert expired_executor.all_orders()[0].status == OrderStatus.EXPIRED_TRADE

    invalid_executor = PaperExecutor(settings)
    invalid_executor.place_order(_request())
    invalid = invalid_executor.process_market_data("EUR/USD", _bars([1.0990], [1.0940]))
    assert invalid.canceled == 1
    assert invalid_executor.all_orders()[0].close_reason == CloseReason.SETUP_INVALIDATED
    assert any(event.event_type == TradeEventType.TRADE_CANCELLED for event in invalid_executor.all_orders()[0].events)


def test_spread_and_slippage_adjust_paper_fill(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.estimated_slippage_pips = 1.0
    adjusted.execution.spread_aware_fills = True
    executor = PaperExecutor(adjusted)
    executor.place_order(_request())
    bars = _bars([1.1010], [1.0995])
    bars["spread"] = 0.0002

    executor.process_market_data("EUR/USD", bars)
    updated = executor.all_orders()[0]

    assert updated.simulated_entry is not None
    assert updated.simulated_entry > updated.request.entry_price
    assert updated.spread_adjustment == 0.0001


def test_gap_through_entry_can_fill_at_open_when_configured(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.gap_through_entry_policy = "fill_at_open"
    executor = PaperExecutor(adjusted)
    executor.place_order(_request())

    result = executor.process_market_data("EUR/USD", _bars([1.1040], [1.1020]))
    updated = executor.all_orders()[0]

    assert result.activated == 1
    assert updated.status == OrderStatus.OPEN_TRADE
    assert updated.simulated_entry is not None and updated.simulated_entry > 1.1020


def test_paper_executor_supports_manual_partial_close_and_reconciliation(settings) -> None:
    executor = PaperExecutor(settings)
    order = executor.place_order(_request())
    executor.process_market_data("EUR/USD", _bars([1.1010], [1.0995]))

    updated = executor.partial_close_order(order.order_id, 1.1040, 0.25)

    assert updated.status == OrderStatus.PARTIALLY_CLOSED
    assert updated.remaining_fraction == 0.75
    assert updated.partial_exits[-1].target == "manual_partial"
    assert executor.reconcile()[0].order_id == order.order_id


def test_paper_trading_service_submits_only_guardrail_approved_opportunities(settings) -> None:
    executor = PaperExecutor(settings)
    service = PaperTradingService(settings, executor=executor)
    approved = _opportunity()
    watchlist = _opportunity(status=OpportunityStatus.WATCHLIST, approved=False)

    result = service.submit_approved([approved, watchlist])

    assert len(result.orders) == 1
    assert result.orders[0].request.symbol == "EUR/USD"
    assert result.orders[0].request.source_status == "approved"
    assert result.orders[0].request.entry_rationale == "paper test"


def test_portfolio_guardrails_block_bad_data_spread_and_exposure(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.portfolio_risk.max_exposure_per_currency = 1
    adjusted.portfolio_risk.max_gross_exposure_per_currency = 1
    adjusted.portfolio_risk.max_correlated_symbol_exposure = 1
    guardrails = PortfolioGuardrails(adjusted)
    executor = PaperExecutor(settings)
    existing = executor.place_order(_request())
    poor_data = _opportunity(data_quality=DataQualityDiagnostic(score=40.0, missing_bars=5, spread_available=False, resampled=True))
    wide_spread = _opportunity(symbol="GBP/USD", spread=0.002, atr=0.001)
    same_symbol = _opportunity(symbol="EUR/USD")
    exposure = _opportunity(symbol="EUR/JPY")

    assert not guardrails.evaluate(poor_data, [], []).allowed
    assert not guardrails.evaluate(wide_spread, [], []).allowed
    symbol_decision = guardrails.evaluate(same_symbol, [existing], [])
    assert not symbol_decision.allowed
    assert any("EUR/USD" in reason for reason in symbol_decision.reasons)
    exposure_decision = guardrails.evaluate(exposure, [existing], [])
    assert not exposure_decision.allowed
    assert any("EUR" in reason for reason in exposure_decision.reasons)
    correlated_decision = guardrails.evaluate(_opportunity(symbol="GBP/USD"), [existing], [])
    assert not correlated_decision.allowed
    assert any("correlated-symbol exposure" in reason for reason in correlated_decision.reasons)


def test_pre_trade_validation_blocks_stale_incomplete_and_off_hours(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.pre_live_validation.allow_off_hours = False
    validator = PreTradeValidator(adjusted)
    stale = _opportunity(timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))
    incomplete = _opportunity(entry=None)
    off_hours = _opportunity(session=SessionName.OFF_HOURS)

    stale_decision = validator.validate(stale, [], [], now=stale.timestamp + timedelta(minutes=300))
    incomplete_decision = validator.validate(incomplete, [], [], now=incomplete.timestamp)
    off_hours_decision = validator.validate(off_hours, [], [], now=off_hours.timestamp)

    assert not stale_decision.allowed
    assert any("signal age" in reason for reason in stale_decision.reasons)
    assert not incomplete_decision.allowed
    assert any("missing executable levels" in reason for reason in incomplete_decision.reasons)
    assert not off_hours_decision.allowed
    assert any("off-hours" in reason for reason in off_hours_decision.reasons)


def test_portfolio_guardrails_block_after_loss_streak(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.portfolio_risk.cooldown_after_consecutive_losses = 2
    guardrails = PortfolioGuardrails(adjusted)
    closed: list[ExecutionOrder] = []
    for idx in range(2):
        order = PaperExecutor(settings).place_order(_request())
        closed.append(
            order.model_copy(
                update={
                    "status": OrderStatus.CLOSED,
                    "closed_at": datetime(2025, 1, 1, idx, tzinfo=timezone.utc),
                    "realized_r": -1.0,
                }
            )
        )

    decision = guardrails.evaluate(_opportunity(), [], closed, now=datetime(2025, 1, 1, 1, 5, tzinfo=timezone.utc))

    assert not decision.allowed
    assert any("cooldown" in reason for reason in decision.reasons)


def test_portfolio_guardrails_block_after_daily_loss_limit(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.portfolio_risk.max_daily_loss_r = 2.0
    guardrails = PortfolioGuardrails(adjusted)
    order = PaperExecutor(settings).place_order(_request()).model_copy(
        update={
            "status": OrderStatus.FULLY_CLOSED,
            "closed_at": datetime(2025, 1, 1, 10, tzinfo=timezone.utc),
            "realized_r": -2.1,
        }
    )

    decision = guardrails.evaluate(_opportunity(), [], [order], now=datetime(2025, 1, 1, 11, tzinfo=timezone.utc))

    assert not decision.allowed
    assert any("daily paper loss" in reason for reason in decision.reasons)


def test_paper_portfolio_report_outputs_summary_and_exposures(settings, tmp_path) -> None:
    executor = PaperExecutor(settings)
    order = executor.place_order(_request())
    executor.process_market_data("EUR/USD", _bars([1.1010, 1.1160], [1.0995, 1.1030]))

    outputs = generate_paper_portfolio_report(executor.all_orders(), [], tmp_path / "paper_report")

    assert outputs["summary"].exists()
    assert outputs["summary_json"].exists()
    assert outputs["orders"].exists()
    assert outputs["exposure_by_currency"].exists()
    assert outputs["daily_summary"].exists()
    assert outputs["guardrail_triggers"].exists()
    assert outputs["score_vs_realized"].exists()
    assert "Realized R" in outputs["summary"].read_text(encoding="utf-8")
    assert "Profit factor" in outputs["summary"].read_text(encoding="utf-8")
    assert "fully_closed_trade" in outputs["orders"].read_text(encoding="utf-8")
