"""Tests for walk-forward execution with conservative threshold selection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.backtest.conservative_walk_forward import (
    run_conservative_walk_forward,
    run_conservative_walk_forward_parallel,
    threshold_result_to_dict,
    write_threshold_reports,
)
from app.backtest.metrics import calculate_metrics
from app.backtest.threshold_selection import ABSTAIN_SCORE_THRESHOLD, ThresholdSelectionConfig
from app.backtest.walk_forward import WalkForwardConfig
from app.core.types import (
    BacktestResult,
    DirectionBias,
    SetupFamily,
    SetupSubtype,
    TradeRecord,
    TradingStyle,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = BASE + timedelta(days=50)


def _trade(
    net_r: float,
    score: float,
    *,
    moment: datetime,
    symbol: str = "EUR/USD",
) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        entry_time=moment,
        exit_time=moment + timedelta(hours=1),
        entry=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        exit_price=1.11 if net_r > 0 else 1.095,
        gross_r=net_r,
        net_r=net_r,
        exit_reason="take_profit" if net_r > 0 else "stop_loss",
        cost_pips=1.0,
        final_score=score,
    )


def _result(symbols, style, setup_filter, start, end, trades):  # noqa: ANN001
    return BacktestResult(
        run_id="fixture",
        created_at=BASE,
        symbols=symbols,
        style=style,
        setup_filter=setup_filter,
        start=start,
        end=end,
        metrics=calculate_metrics(trades),
        trades=trades,
        equity_curve=[],
        limitations=[],
    )


def _negative_runner(symbols, style, setup_filter, start, end):  # noqa: ANN001
    is_segment = (end - start) > timedelta(days=7)
    count = 20 if is_segment else 3
    trades = [
        _trade(-0.2, 80.0, moment=start + timedelta(hours=index + 1))
        for index in range(count)
    ]
    return _result(symbols, style, setup_filter, start, end, trades)


def _positive_runner(symbols, style, setup_filter, start, end):  # noqa: ANN001
    is_segment = (end - start) > timedelta(days=7)
    count = 20 if is_segment else 3
    trades = [
        _trade(0.25, 80.0, moment=start + timedelta(hours=index + 1))
        for index in range(count)
    ]
    return _result(symbols, style, setup_filter, start, end, trades)


class _PositiveRunnerFactory:
    def __call__(self):
        return _positive_runner


def _walk_config() -> WalkForwardConfig:
    return WalkForwardConfig(
        in_sample_days=10,
        out_of_sample_days=5,
        step_days=5,
        score_grid=(0.0, 75.0),
        min_in_sample_trades=10,
    )


def _threshold_config() -> ThresholdSelectionConfig:
    return ThresholdSelectionConfig(
        score_grid=(0.0, 75.0),
        min_trades=10,
        shrinkage_trades=10.0,
        confidence_z=1.645,
    )


def test_negative_in_sample_evidence_abstains_and_excludes_oos_trades() -> None:
    result = run_conservative_walk_forward(
        _negative_runner,
        ["EUR/USD"],
        TradingStyle.DAY_TRADING,
        "all",
        BASE,
        END,
        _walk_config(),
        _threshold_config(),
    )

    assert result.selections
    assert all(selection.status == "abstain" for selection in result.selections)
    assert all(fold.selected_min_score == ABSTAIN_SCORE_THRESHOLD for fold in result.report.folds)
    assert all(fold.out_of_sample_trades == 0 for fold in result.report.folds)
    assert result.report.aggregate_metrics.number_of_trades == 0


def test_positive_in_sample_evidence_selects_and_keeps_oos_trades() -> None:
    result = run_conservative_walk_forward(
        _positive_runner,
        ["EUR/USD"],
        TradingStyle.DAY_TRADING,
        "all",
        BASE,
        END,
        _walk_config(),
        _threshold_config(),
    )

    assert all(selection.status == "selected" for selection in result.selections)
    assert all(fold.selected_min_score == 75.0 for fold in result.report.folds)
    assert result.report.aggregate_metrics.number_of_trades == len(result.report.folds) * 3
    assert result.report.aggregate_metrics.expectancy == 0.25


def test_threshold_diagnostics_are_serialized_and_written(tmp_path) -> None:
    result = run_conservative_walk_forward(
        _positive_runner,
        ["EUR/USD"],
        TradingStyle.DAY_TRADING,
        "all",
        BASE,
        END,
        _walk_config(),
        _threshold_config(),
    )
    payload = threshold_result_to_dict(result)
    diagnostics = payload["threshold_selection"]

    assert diagnostics["config"]["objective"] == "conservative_lcb"
    assert diagnostics["selected_folds"] == len(result.report.folds)
    assert diagnostics["abstained_folds"] == 0
    assert diagnostics["folds"][0]["selected_candidate"]["lower_confidence_bound_r"] > 0.0

    outputs = write_threshold_reports(result, tmp_path)
    assert outputs["json"].exists()
    assert outputs["txt"].read_text().startswith("Conservative Threshold Selection")


def test_parallel_and_sequential_conservative_results_are_identical() -> None:
    sequential = run_conservative_walk_forward(
        _positive_runner,
        ["EUR/USD", "GBP/USD"],
        TradingStyle.DAY_TRADING,
        "all",
        BASE,
        END,
        _walk_config(),
        _threshold_config(),
    )
    parallel = run_conservative_walk_forward_parallel(
        None,
        ["EUR/USD", "GBP/USD"],
        TradingStyle.DAY_TRADING,
        "all",
        BASE,
        END,
        _walk_config(),
        _threshold_config(),
        n_jobs=2,
        runner_factory=_PositiveRunnerFactory(),
    )

    assert threshold_result_to_dict(sequential) == threshold_result_to_dict(parallel)
