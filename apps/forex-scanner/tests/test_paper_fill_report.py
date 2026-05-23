"""Paper fill report tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.models import ExecutionOrder, OrderRequest, OrderStatus, PaperBlockRecord
from scripts.paper_fill_report import build_paper_fill_report, export_paper_fill_report


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


def test_paper_fill_report_exports_csv_and_json(tmp_path, monkeypatch) -> None:
    report = {
        "average_slippage": 0.00012,
        "average_spread_cost": 0.0002,
        "rejected_paper_fills": 3,
        "symbols_with_worst_execution": [{"symbol": "GBP/USD", "average_cost": 0.0005, "orders": 2}],
        "setups_most_affected_by_costs": [{"setup": "momentum_breakout", "average_cost": 0.0004, "orders": 2}],
        "rejection_reasons": {"paper fill rejected: spread/ATR 0.800 above 0.500": 2},
    }
    monkeypatch.setattr("scripts.paper_fill_report.PROJECT_ROOT", tmp_path)

    export_paper_fill_report(report)

    summary = tmp_path / "reports" / "paper_fill_summary.json"
    csv_report = tmp_path / "reports" / "paper_fill_report.csv"
    assert summary.exists()
    assert csv_report.exists()
    assert "average_slippage" in summary.read_text(encoding="utf-8")
    assert "rejected_paper_fills" in csv_report.read_text(encoding="utf-8")


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
