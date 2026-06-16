"""Equivalence proof: parallel walk-forward == sequential walk-forward.

The parallelisation must change ONLY scheduling, never any computed figure. This
test runs the *same* walk-forward analysis on the *same* deterministic fixture
through both code paths and asserts strict, value-by-value equality:

  * ``--jobs 1`` -> :func:`run_walk_forward`           (the exact sequential path)
  * ``--jobs 2`` -> :func:`run_walk_forward_parallel`  (ProcessPoolExecutor)

and compares:
  - per-fold OOS trade register (record-by-record via ``model_dump``)
  - per-fold selected_min_score / expectancy / trade counts / OOS metrics
  - aggregate metrics (incl. bootstrap confidence interval)
  - OOS equity curve (timestamps + values)

A fast, deterministic, *picklable* runner factory stands in for the real
Backtester so the test is hermetic (no network, no slow per-bar engine loop)
while still exercising the full parallel reassembly/aggregation machinery — the
only part of the pipeline where worker ordering could possibly leak into output.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.backtest.walk_forward import (
    WalkForwardConfig,
    run_walk_forward,
    run_walk_forward_parallel,
)
from app.core.types import (
    BacktestResult,
    DirectionBias,
    SetupFamily,
    SetupSubtype,
    TradeRecord,
    TradingStyle,
)
from app.backtest.metrics import calculate_metrics

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = BASE + timedelta(days=120)  # several folds with IS=20 / OOS=10 / step=10


def _trade(symbol: str, net_r: float, score: float, *, moment: datetime) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
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


def _deterministic_segment_runner(symbols, style, setup_filter, start, end):  # noqa: ANN001
    """Deterministic, fast stand-in for the real Backtester.

    Trades depend only on (symbols, start, end) so a given window always produces
    the same trades — different folds get different, time-ordered trades, which
    makes the canonical-reassembly step meaningful. Module-level (picklable) so it
    can cross the ProcessPoolExecutor boundary.
    """
    is_segment = (end - start) > timedelta(days=15)
    trades: list[TradeRecord] = []
    # Use the segment's start day as a stable, fold-dependent seed.
    day_seed = (start - BASE).days
    for offset, symbol in enumerate(symbols):
        moment = start + timedelta(hours=6 + offset)
        if is_segment:
            # In-sample: a mix that favours the high-score band.
            for k in range(4):
                trades.append(_trade(symbol, -1.0, 50.0, moment=moment + timedelta(hours=k)))
            for k in range(4):
                trades.append(_trade(symbol, 1.0 + 0.1 * day_seed, 80.0, moment=moment + timedelta(hours=4 + k)))
        else:
            # Out-of-sample: one high-score winner + one low-score loser per symbol.
            trades.append(_trade(symbol, 0.5 + 0.01 * day_seed, 80.0, moment=moment))
            trades.append(_trade(symbol, -0.3, 55.0, moment=moment + timedelta(hours=1)))
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


class _DeterministicRunnerFactory:
    """Picklable factory returning the deterministic segment runner above."""

    def __call__(self):
        return _deterministic_segment_runner


def _config():
    return WalkForwardConfig(
        in_sample_days=20,
        out_of_sample_days=10,
        step_days=10,
        score_grid=(0.0, 60.0, 70.0),
        min_in_sample_trades=3,
    )


def _assert_reports_identical(report_seq, report_par) -> None:
    assert len(report_seq.folds) == len(report_par.folds), (
        f"fold count: seq={len(report_seq.folds)} par={len(report_par.folds)}"
    )
    assert len(report_seq.folds) >= 3, "fixture should produce several folds"

    for fold_s, fold_p in zip(report_seq.folds, report_par.folds, strict=True):
        idx = fold_s.window.fold_index
        assert fold_s.window.fold_index == fold_p.window.fold_index, f"fold index mismatch at {idx}"
        assert fold_s.window == fold_p.window, f"fold {idx}: window mismatch"
        assert fold_s.selected_min_score == fold_p.selected_min_score, (
            f"fold {idx}: min_score {fold_s.selected_min_score} != {fold_p.selected_min_score}"
        )
        assert fold_s.in_sample_trades == fold_p.in_sample_trades, f"fold {idx}: IS trade count"
        assert fold_s.in_sample_expectancy == fold_p.in_sample_expectancy, f"fold {idx}: IS expectancy"
        assert fold_s.out_of_sample_trades == fold_p.out_of_sample_trades, f"fold {idx}: OOS trade count"
        assert (
            fold_s.out_of_sample_metrics.model_dump() == fold_p.out_of_sample_metrics.model_dump()
        ), f"fold {idx}: OOS metrics differ"

        # Record-by-record comparison of the OOS register.
        assert len(fold_s.oos_trade_records) == len(fold_p.oos_trade_records), (
            f"fold {idx}: OOS record count differs"
        )
        for j, (ts, tp) in enumerate(zip(fold_s.oos_trade_records, fold_p.oos_trade_records, strict=True)):
            assert ts.model_dump() == tp.model_dump(), f"fold {idx} trade {j}: record mismatch"

    # Aggregate metrics include the bootstrap confidence interval -> proves IC identical.
    assert (
        report_seq.aggregate_metrics.model_dump() == report_par.aggregate_metrics.model_dump()
    ), "aggregate metrics differ"

    assert len(report_seq.oos_equity_curve) == len(report_par.oos_equity_curve), "equity curve length"
    for k, ((ts_s, val_s), (ts_p, val_p)) in enumerate(
        zip(report_seq.oos_equity_curve, report_par.oos_equity_curve, strict=True)
    ):
        assert ts_s == ts_p, f"equity point {k}: timestamp {ts_s} != {ts_p}"
        assert val_s == val_p, f"equity point {k}: value {val_s} != {val_p}"


def test_parallel_walk_forward_equals_sequential() -> None:
    """--jobs 2 must produce results identical to the sequential --jobs 1 path."""
    config = _config()
    symbols = ["EUR/USD", "GBP/USD", "USD/CHF"]

    report_seq = run_walk_forward(
        _deterministic_segment_runner, symbols, TradingStyle.DAY_TRADING, "all", BASE, END, config
    )
    report_par = run_walk_forward_parallel(
        None, symbols, TradingStyle.DAY_TRADING, "all", BASE, END, config,
        n_jobs=2, runner_factory=_DeterministicRunnerFactory(),
    )

    _assert_reports_identical(report_seq, report_par)


def test_parallel_walk_forward_equals_sequential_more_workers() -> None:
    """Result is independent of the worker count (4 workers == sequential)."""
    config = _config()
    symbols = ["EUR/USD", "GBP/USD", "USD/CHF"]

    report_seq = run_walk_forward(
        _deterministic_segment_runner, symbols, TradingStyle.DAY_TRADING, "all", BASE, END, config
    )
    report_par = run_walk_forward_parallel(
        None, symbols, TradingStyle.DAY_TRADING, "all", BASE, END, config,
        n_jobs=4, runner_factory=_DeterministicRunnerFactory(),
    )

    _assert_reports_identical(report_seq, report_par)


def test_run_walk_forward_parallel_requires_settings_without_factory() -> None:
    """Guard: settings is mandatory when no explicit runner factory is given."""
    import pytest

    with pytest.raises(ValueError, match="settings is required"):
        run_walk_forward_parallel(
            None, ["EUR/USD"], TradingStyle.DAY_TRADING, "all", BASE, END, _config(), n_jobs=2
        )
