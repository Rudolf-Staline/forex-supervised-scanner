"""Paper fill report tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.models import ExecutionOrder, OrderRequest, OrderStatus, PaperBlockRecord
from scripts.paper_fill_report import build_paper_fill_report


def test_paper_fill_report_aggregates_costs_and_rejections() -> None:
    orders = [
        _order("EUR/USD", SetupSubtype.EMA50_PULLBACK, slippage=0.00005, spread=0.0001, commission=0.00002),
        _order("GBP/USD", SetupSubtype.MOMENTUM_BREAKOUT, slippage=0.0002, spread=0.0003, commission=0.00002),
    ]
    blocks = [_block("GBP/USD", ["paper fill rejected: spread/ATR 0.800 above 0.500"])]

    report = build_paper_fill_report(orders, blocks)

    assert report["average_slippage"] == pytest.approx(0.000125)
    assert report["average_spread_cost"] == pytest.approx(0.0002)
    assert report["rejected_paper_fills"] == 1
    assert report["symbols_with_worst_execution"][0]["symbol"] == "GBP/USD"
    assert report["setups_most_affected_by_costs"][0]["setup"] == "momentum_breakout"
    assert report["rejection_reasons"]["paper fill rejected: spread/ATR 0.800 above 0.500"] == 1


def _order(symbol: str, setup: SetupSubtype, *, slippage: float, spread: float, commission: float) -> ExecutionOrder:
    request = OrderRequest(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=setup,
        direction=DirectionBias.LONG,
        quantity_units=1.0,
        entry_price=1.1,
        stop_loss=1.095,
        take_profit=1.11,
    )
    return ExecutionOrder(
        order_id=f"order-{symbol}",
        request=request,
        status=OrderStatus.PENDING_OPPORTUNITY,
        created_at=datetime.now(timezone.utc),
        initial_stop_loss=1.095,
        execution_assumptions={
            "paper_realistic_fill": True,
            "paper_slippage_points": slippage,
            "paper_spread_cost": spread,
            "paper_commission_estimate": commission,
        },
    )


def _block(symbol: str, reasons: list[str]) -> PaperBlockRecord:
    return PaperBlockRecord(
        block_id=f"block-{symbol}",
        created_at=datetime.now(timezone.utc),
        symbol=symbol,
        status="approved",
        setup_family="trend_continuation",
        setup_subtype="ema50_pullback",
        direction="long",
        final_score=82.0,
        reasons=reasons,
    )
