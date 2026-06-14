"""Walk-forward / out-of-sample harness tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.backtest.walk_forward import (
    WalkForwardConfig,
    evaluate_fold,
    generate_windows,
    report_to_dict,
    run_walk_forward,
    select_min_score,
    write_reports,
)
from app.core.types import (
    BacktestResult,
    DirectionBias,
    SetupFamily,
    SetupSubtype,
    TradeRecord,
    TradingStyle,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trade(net_r: float, score: float, *, day: int) -> TradeRecord:
    moment = BASE + timedelta(days=day)
    return TradeRecord(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        entry_time=moment,
        exit_time=moment,
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


def test_generate_windows_are_sequential_and_disjoint_train_test() -> None:
    config = WalkForwardConfig(in_sample_days=30, out_of_sample_days=10, step_days=10)
    windows = generate_windows(BASE, BASE + timedelta(days=70), config)

    assert len(windows) == 4
    for window in windows:
        # The out-of-sample segment always follows the in-sample segment.
        assert window.out_of_sample_start == window.in_sample_end
        assert window.out_of_sample_end > window.out_of_sample_start
        assert window.in_sample_end - window.in_sample_start == timedelta(days=30)
        assert window.out_of_sample_end - window.out_of_sample_start == timedelta(days=10)


def test_select_min_score_prefers_higher_expectancy_threshold() -> None:
    # Low scores lose, high scores win: the optimiser should lift the threshold.
    trades = [_trade(-1.0, score=50.0, day=i) for i in range(6)]
    trades += [_trade(2.0, score=80.0, day=10 + i) for i in range(6)]
    selected, expectancy = select_min_score(trades, (0.0, 60.0, 75.0), min_in_sample_trades=5)
    assert selected == 75.0
    assert expectancy == 2.0


def test_select_min_score_respects_minimum_trade_count() -> None:
    # Only two trades clear 75 -> threshold must fall back to an eligible band.
    trades = [_trade(-1.0, score=50.0, day=i) for i in range(6)]
    trades += [_trade(2.0, score=80.0, day=10 + i) for i in range(2)]
    selected, _ = select_min_score(trades, (0.0, 75.0), min_in_sample_trades=5)
    assert selected == 0.0


def test_evaluate_fold_reports_only_out_of_sample_trades() -> None:
    config = WalkForwardConfig(in_sample_days=10, out_of_sample_days=5, step_days=5, score_grid=(0.0, 70.0), min_in_sample_trades=3)
    window = generate_windows(BASE, BASE + timedelta(days=15), config)[0]
    in_sample = [_trade(-1.0, score=50.0, day=i) for i in range(4)] + [_trade(1.5, score=80.0, day=i) for i in range(4)]
    out_of_sample = [_trade(0.5, score=80.0, day=11), _trade(-0.4, score=55.0, day=12)]

    fold = evaluate_fold(window, in_sample, out_of_sample, config)

    assert fold.selected_min_score == 70.0
    # Only the score>=70 OOS trade survives the in-sample-selected threshold.
    assert fold.out_of_sample_trades == 1
    assert fold.out_of_sample_metrics.expectancy == 0.5


def test_run_walk_forward_aggregates_oos_only(tmp_path) -> None:
    config = WalkForwardConfig(in_sample_days=20, out_of_sample_days=10, step_days=10, score_grid=(0.0, 70.0), min_in_sample_trades=3)

    def runner(symbols, style, setup_filter, start, end):  # noqa: ANN001
        # In-sample segments (length 20d) get a mixed sample favouring high scores;
        # out-of-sample segments (length 10d) get one winning high-score trade.
        is_segment = (end - start) == timedelta(days=20)
        if is_segment:
            trades = [_trade(-1.0, 50.0, day=0) for _ in range(4)] + [_trade(1.2, 80.0, day=1) for _ in range(4)]
        else:
            trades = [_trade(0.8, 80.0, day=0), _trade(-0.5, 55.0, day=1)]
        return BacktestResult(
            run_id="x",
            created_at=BASE,
            symbols=symbols,
            style=style,
            setup_filter=setup_filter,
            start=start,
            end=end,
            metrics=__import__("app.backtest.metrics", fromlist=["calculate_metrics"]).calculate_metrics(trades),
            trades=trades,
            equity_curve=[],
            limitations=[],
        )

    report = run_walk_forward(runner, ["EUR/USD"], TradingStyle.DAY_TRADING, "all", BASE, BASE + timedelta(days=60), config)

    assert len(report.folds) >= 2
    # Every fold tuned to 70 and kept only the high-score OOS winner.
    for fold in report.folds:
        assert fold.selected_min_score == 70.0
        assert fold.out_of_sample_trades == 1
    assert report.aggregate_metrics.number_of_trades == len(report.folds)
    assert report.aggregate_metrics.expectancy == 0.8

    outputs = write_reports(report, tmp_path)
    payload = json.loads(outputs["json"].read_text())
    assert payload["out_of_sample"]["total_trades"] == len(report.folds)
    assert outputs["txt"].read_text().startswith("Walk-Forward")
    # Sanity: serialisation round-trips the fold count.
    assert report_to_dict(report)["fold_count"] == len(report.folds)
