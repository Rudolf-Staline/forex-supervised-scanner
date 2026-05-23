"""Realistic paper broker simulation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.brokers.paper_broker import PaperBrokerConfig, RealisticPaperBroker
from app.core.types import ConfidenceBucket, DataQualityDiagnostic, DirectionBias, MarketRegime, Opportunity, OpportunityStatus, SessionName, SetupFamily, SetupSubtype, Timeframe, TradingStyle
from app.execution.models import OrderRequest
from app.paper.trading import PaperTradingService


def test_realistic_paper_broker_decorates_fill_costs(settings) -> None:
    broker = RealisticPaperBroker(PaperBrokerConfig(max_spread_atr=1.0))
    simulation = broker.simulate_request(_request())

    assert simulation.accepted
    assert simulation.fill_status == "filled"
    assert simulation.filled_entry is not None and simulation.filled_entry > simulation.requested_entry
    assert simulation.spread_cost > 0
    assert simulation.commission_estimate > 0
    assert simulation.final_risk_reward is not None


def test_realistic_paper_broker_rejects_wide_spread_off_hours_close_stop_and_bad_volume() -> None:
    broker = RealisticPaperBroker(PaperBrokerConfig(max_spread_atr=0.2, min_volume=0.01, min_stop_spread_multiple=2.0))
    request = _request(quantity=0.001, session="off_hours", spread=0.001, atr=0.002, stop=1.099)

    simulation = broker.simulate_request(request)

    assert not simulation.accepted
    assert any("volume" in reason for reason in simulation.reasons)
    assert "paper fill rejected: session is not tradable" in simulation.reasons
    assert any("spread/ATR" in reason for reason in simulation.reasons)
    assert "paper fill rejected: stop_loss too close to current spread" in simulation.reasons


def test_paper_trading_service_uses_realistic_paper_broker(settings) -> None:
    service = PaperTradingService(settings, paper_broker=RealisticPaperBroker(PaperBrokerConfig(max_spread_atr=1.0)))

    result = service.submit_approved([_opportunity()])

    assert len(result.orders) == 1
    assumptions = result.orders[0].execution_assumptions
    assert assumptions["paper_realistic_fill"] is True
    assert assumptions["paper_fill_status"] == "filled"
    assert assumptions["paper_requested_entry"] == 1.1
    assert assumptions["paper_filled_entry"] > 1.1


def test_paper_trading_service_blocks_rejected_realistic_fill(settings) -> None:
    service = PaperTradingService(settings, paper_broker=RealisticPaperBroker(PaperBrokerConfig(max_spread_atr=0.1)))

    result = service.submit_approved([_opportunity(spread=0.00015, atr=0.001)])

    assert result.orders == []
    assert result.block_records
    assert any("paper fill rejected: spread/ATR" in reason for reason in result.block_records[0].reasons)


def _request(
    *,
    quantity: float = 1.0,
    session: str = "london",
    spread: float = 0.0001,
    atr: float = 0.001,
    stop: float = 1.095,
) -> OrderRequest:
    return OrderRequest(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=quantity,
        entry_price=1.1,
        stop_loss=stop,
        take_profit=1.11,
        signal_timestamp=datetime.now(timezone.utc),
        source_status="approved",
        final_score=82.0,
        provider="mt5",
        session=session,
        spread_at_signal=spread,
        atr_at_signal=atr,
    )


def _opportunity(**updates: object) -> Opportunity:
    payload = {
        "timestamp": datetime.now(timezone.utc),
        "symbol": "EUR/USD",
        "style": TradingStyle.DAY_TRADING,
        "setup_family": SetupFamily.TREND_CONTINUATION,
        "setup_subtype": SetupSubtype.EMA50_PULLBACK,
        "regime": MarketRegime.TRENDING_UP,
        "direction": DirectionBias.LONG,
        "score": 82.0,
        "confidence": ConfidenceBucket.HIGH,
        "entry": 1.1,
        "stop_loss": 1.095,
        "take_profit": 1.11,
        "risk_reward": 2.0,
        "explanation": "fixture",
        "timeframe_higher": Timeframe.H1,
        "timeframe_entry": Timeframe.M15,
        "timeframe_trigger": Timeframe.M5,
        "score_components": {},
        "provider": "mt5",
        "session": SessionName.LONDON,
        "approved": True,
        "status": OpportunityStatus.APPROVED,
        "raw_setup_family": SetupFamily.TREND_CONTINUATION,
        "final_score": 82.0,
        "tp1": 1.105,
        "tp2": 1.11,
        "tp3": 1.115,
        "spread": 0.0001,
        "atr": 0.001,
        "data_quality": DataQualityDiagnostic(score=95.0, missing_bars=0, spread_available=True, resampled=False),
    }
    payload.update(updates)
    return Opportunity.model_validate(payload)
