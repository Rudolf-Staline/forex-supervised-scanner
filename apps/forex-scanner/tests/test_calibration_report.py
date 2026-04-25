"""Calibration report generation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.types import (
    BacktestMetrics,
    BacktestResult,
    DirectionBias,
    SessionName,
    SetupFamily,
    SetupSubtype,
    TradeOutcomeLabel,
    TradeRecord,
    TradingStyle,
)
from app.execution.models import PaperBlockRecord, OrderRequest, OrderStatus
from app.execution.paper import PaperExecutor
from app.reporting.calibration import generate_calibration_report
from app.storage.database import Database


def test_calibration_report_generates_csv_and_markdown(tmp_path, settings) -> None:
    database = Database(tmp_path / "calibration.sqlite")
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    trade = TradeRecord(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        direction=DirectionBias.LONG,
        entry_time=now,
        exit_time=now,
        entry=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        exit_price=1.11,
        gross_r=1.8,
        net_r=1.7,
        exit_reason="take_profit",
        cost_pips=1.0,
        session=SessionName.LONDON,
        technical_score=78.0,
        execution_score=70.0,
        context_score=72.0,
        empirical_score=55.0,
        final_score=74.0,
        outcome=TradeOutcomeLabel.WIN_CLEAN,
        tp1_hit=True,
        tp2_hit=True,
        tp3_hit=False,
        mae=0.25,
        mfe=2.1,
        bars_to_activation=0,
        bars_to_tp1=2,
        bars_to_tp2=4,
    )
    result = BacktestResult(
        run_id="run",
        created_at=now,
        symbols=["EUR/USD"],
        style=TradingStyle.DAY_TRADING,
        setup_filter="all",
        start=now,
        end=now,
        metrics=BacktestMetrics(
            win_rate=100.0,
            average_win=1.7,
            average_loss=0.0,
            profit_factor=999.0,
            max_drawdown=0.0,
            expectancy=1.7,
            number_of_trades=1,
            sharpe_like=0.0,
        ),
        trades=[trade],
        equity_curve=[(now, 0.0), (now, 1.7)],
        limitations=[],
    )
    database.save_backtest_result(result)
    executor = PaperExecutor(settings)
    paper_order = executor.place_order(
        OrderRequest(
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
            final_score=74.0,
            session=SessionName.LONDON.value,
        )
    )
    paper_order = paper_order.model_copy(update={"status": OrderStatus.FULLY_CLOSED, "realized_r": 1.2, "realized_pnl": 1.2, "mae": 0.1, "mfe": 1.5})
    database.save_paper_orders([paper_order])
    database.save_paper_blocks(
        [
            PaperBlockRecord(
                block_id="block",
                created_at=now,
                symbol="GBP/USD",
                status="approved",
                setup_family="trend_continuation",
                setup_subtype="ema50_pullback",
                direction="long",
                final_score=62.0,
                reasons=["max exposure for GBP would be exceeded"],
            )
        ]
    )

    outputs = generate_calibration_report(database.path, tmp_path / "reports", top_k_values=[1])

    assert outputs["summary"].exists()
    assert outputs["score_buckets"].exists()
    assert outputs["layer_score_buckets"].exists()
    assert outputs["layer_predictiveness"].exists()
    assert outputs["status"].exists()
    assert outputs["status_separation"].exists()
    assert outputs["empirical_lift"].exists()
    assert outputs["suggested_layer_weights"].exists()
    assert outputs["summary_json"].exists()
    assert outputs["conditional_combinations"].exists()
    assert outputs["paper_lifecycle"].exists()
    assert outputs["paper_execution_summary"].exists()
    assert outputs["paper_blocks"].exists()
    assert outputs["subtype"].exists()
    assert "Overall win rate" in outputs["summary"].read_text(encoding="utf-8")
    assert "Final-score bucket expectancy monotonic" in outputs["summary"].read_text(encoding="utf-8")
    assert "Lifecycle separation" in outputs["summary"].read_text(encoding="utf-8")
    assert "shallow_ema20_pullback" in outputs["subtype"].read_text(encoding="utf-8")
    assert "subtype_symbol" in outputs["conditional_combinations"].read_text(encoding="utf-8")
    assert "final_score_expectancy" in outputs["empirical_lift"].read_text(encoding="utf-8")
    assert "insufficient_samples" in outputs["suggested_layer_weights"].read_text(encoding="utf-8")
    assert "precision_at_top_k" in outputs["top_k"].read_text(encoding="utf-8")
    assert "records_analyzed" in outputs["summary_json"].read_text(encoding="utf-8")
    assert "paper_orders" in outputs["summary_json"].read_text(encoding="utf-8")
    assert "max exposure" in outputs["paper_blocks"].read_text(encoding="utf-8")
