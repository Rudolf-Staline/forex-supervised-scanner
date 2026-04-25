"""Trading journal and audit trail tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.models import OrderRequest, TradeEventType
from app.execution.paper import PaperExecutor
from app.paper.journal import all_trade_events, export_trading_journal, journal_entries_from_orders, reconstruct_event_trail


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
        source_status="premium",
        signal_timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        entry_rationale="trend continuation with clean pullback",
        regime_context="trending up",
        session="london",
        spread_at_signal=0.0001,
    )


def _bars() -> pd.DataFrame:
    index = pd.date_range(datetime(2025, 1, 1, tzinfo=timezone.utc), periods=2, freq="5min")
    return pd.DataFrame(
        {
            "open": [1.1002, 1.1060],
            "high": [1.1010, 1.1160],
            "low": [1.0995, 1.1030],
            "close": [1.1000, 1.1140],
            "volume": [100.0, 100.0],
        },
        index=index,
    )


def test_journal_reconstructs_order_events_and_histories(settings) -> None:
    executor = PaperExecutor(settings)
    order = executor.place_order(_request())
    executor.process_market_data("EUR/USD", _bars())
    updated = executor.all_orders()[0]

    entries = journal_entries_from_orders([updated])
    events = all_trade_events([updated])
    trail = reconstruct_event_trail(updated.order_id, events)

    assert entries[0].entry_rationale_summary == "trend continuation with clean pullback"
    assert entries[0].partial_close_history
    assert entries[0].stop_movement_history
    assert trail[0].event_type == TradeEventType.SIGNAL_PREMIUM
    assert trail[-1].event_type == TradeEventType.TRADE_CLOSED


def test_journal_export_writes_operator_files(settings, tmp_path) -> None:
    executor = PaperExecutor(settings)
    executor.place_order(_request())
    executor.process_market_data("EUR/USD", _bars())

    outputs = export_trading_journal(executor.all_orders(), [], tmp_path / "journal")

    assert outputs["journal_csv"].exists()
    assert outputs["journal_json"].exists()
    assert outputs["events_csv"].exists()
    assert outputs["events_json"].exists()
    assert "Trading Journal Summary" in outputs["summary"].read_text(encoding="utf-8")
    assert "trade_closed" in outputs["events_csv"].read_text(encoding="utf-8")
