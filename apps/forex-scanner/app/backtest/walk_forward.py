"""Walk-forward / out-of-sample evaluation harness for the rules-based scanner.

The single most important guarantee of this module is **temporal hygiene**:

* Every tunable parameter (here, the minimum ``final_score`` threshold) is chosen
  using *only* the in-sample fold.
* Every reported metric is computed using *only* the out-of-sample fold, after
  applying the in-sample-selected threshold.

This makes the aggregated out-of-sample equity an honest estimate of forward
performance, free of the in-sample optimisation bias that plagues single-window
backtests. The harness reuses the existing :class:`~app.backtest.engine.Backtester`
(via an injectable ``segment_runner``) and :func:`~app.backtest.metrics.calculate_metrics`,
so scan/backtest parity is preserved.

Paper/demo only: nothing here sends orders.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from app.backtest.metrics import calculate_metrics
from app.core.types import BacktestMetrics, BacktestResult, SetupFamily, TradeRecord, TradingStyle

if TYPE_CHECKING:
    from app.config.settings import AppSettings


DEFAULT_SCORE_GRID: tuple[float, ...] = (0.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0)


@dataclass(frozen=True)
class WalkForwardConfig:
    """Sliding-window configuration. All lengths are expressed in days."""

    in_sample_days: int
    out_of_sample_days: int
    step_days: int
    score_grid: tuple[float, ...] = DEFAULT_SCORE_GRID
    min_in_sample_trades: int = 5

    def __post_init__(self) -> None:
        if self.in_sample_days <= 0 or self.out_of_sample_days <= 0 or self.step_days <= 0:
            raise ValueError("in_sample_days, out_of_sample_days and step_days must be positive")
        if not self.score_grid:
            raise ValueError("score_grid must contain at least one candidate threshold")


@dataclass(frozen=True)
class WalkForwardWindow:
    """One train/test split."""

    fold_index: int
    in_sample_start: datetime
    in_sample_end: datetime
    out_of_sample_start: datetime
    out_of_sample_end: datetime


@dataclass(frozen=True)
class FoldResult:
    """Result of a single fold: in-sample tuning + out-of-sample evaluation."""

    window: WalkForwardWindow
    selected_min_score: float
    in_sample_trades: int
    in_sample_expectancy: float
    out_of_sample_trades: int
    out_of_sample_metrics: BacktestMetrics
    oos_trade_records: list[TradeRecord] = field(default_factory=list)


@dataclass(frozen=True)
class WalkForwardReport:
    """Aggregated walk-forward result built only from out-of-sample folds."""

    config: WalkForwardConfig
    symbols: list[str]
    style: TradingStyle
    setup_filter: SetupFamily | Literal["all"]
    start: datetime
    end: datetime
    folds: list[FoldResult]
    aggregate_metrics: BacktestMetrics
    oos_equity_curve: list[tuple[datetime, float]]


class SegmentRunner(Protocol):
    """Runs a backtest over one date segment and returns its result.

    The real implementation wraps :meth:`Backtester.run`; tests can inject a stub.
    """

    def __call__(
        self,
        symbols: list[str],
        style: TradingStyle,
        setup_filter: SetupFamily | Literal["all"],
        start: datetime,
        end: datetime,
    ) -> BacktestResult: ...


def generate_windows(start: datetime, end: datetime, config: WalkForwardConfig) -> list[WalkForwardWindow]:
    """Build sliding in-sample/out-of-sample windows across ``[start, end]``."""

    in_sample = timedelta(days=config.in_sample_days)
    out_of_sample = timedelta(days=config.out_of_sample_days)
    step = timedelta(days=config.step_days)

    windows: list[WalkForwardWindow] = []
    fold_index = 0
    in_start = start
    while in_start + in_sample + out_of_sample <= end:
        in_end = in_start + in_sample
        oos_end = in_end + out_of_sample
        windows.append(
            WalkForwardWindow(
                fold_index=fold_index,
                in_sample_start=in_start,
                in_sample_end=in_end,
                out_of_sample_start=in_end,
                out_of_sample_end=oos_end,
            )
        )
        fold_index += 1
        in_start = in_start + step
    return windows


def select_min_score(
    in_sample_trades: list[TradeRecord],
    score_grid: tuple[float, ...],
    min_in_sample_trades: int,
) -> tuple[float, float]:
    """Pick the threshold maximising in-sample expectancy.

    Returns ``(selected_min_score, in_sample_expectancy)``. Only thresholds that
    retain at least ``min_in_sample_trades`` trades are eligible; ties are broken
    in favour of the *higher* threshold (fewer, more selective trades). If no
    threshold qualifies, the lowest grid value is returned with its expectancy.
    """

    ordered_grid = sorted(score_grid)
    best_threshold = ordered_grid[0]
    best_expectancy = float("-inf")
    found_eligible = False

    for threshold in ordered_grid:
        retained = [trade for trade in in_sample_trades if _passes(trade, threshold)]
        if len(retained) < min_in_sample_trades:
            continue
        expectancy = sum(trade.net_r for trade in retained) / len(retained)
        # >= keeps the higher threshold on ties because the grid is ascending.
        if expectancy >= best_expectancy:
            best_expectancy = expectancy
            best_threshold = threshold
            found_eligible = True

    if not found_eligible:
        retained = [trade for trade in in_sample_trades if _passes(trade, ordered_grid[0])]
        expectancy = sum(trade.net_r for trade in retained) / len(retained) if retained else 0.0
        return ordered_grid[0], round(expectancy, 4)
    return best_threshold, round(best_expectancy, 4)


def evaluate_fold(
    window: WalkForwardWindow,
    in_sample_trades: list[TradeRecord],
    out_of_sample_trades: list[TradeRecord],
    config: WalkForwardConfig,
) -> FoldResult:
    """Tune on in-sample trades, then score out-of-sample trades with that threshold."""

    selected, in_sample_expectancy = select_min_score(
        in_sample_trades, config.score_grid, config.min_in_sample_trades
    )
    eligible_in_sample = [trade for trade in in_sample_trades if _passes(trade, selected)]
    retained_oos = [trade for trade in out_of_sample_trades if _passes(trade, selected)]
    retained_oos.sort(key=lambda trade: trade.exit_time)
    return FoldResult(
        window=window,
        selected_min_score=selected,
        in_sample_trades=len(eligible_in_sample),
        in_sample_expectancy=in_sample_expectancy,
        out_of_sample_trades=len(retained_oos),
        out_of_sample_metrics=calculate_metrics(retained_oos),
        oos_trade_records=retained_oos,
    )


def _evaluate_single_fold(
    segment_runner: SegmentRunner,
    window: WalkForwardWindow,
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    config: WalkForwardConfig,
) -> FoldResult:
    """Run one fold: in-sample tuning + out-of-sample evaluation.

    Shared by the sequential (:func:`run_walk_forward`) and parallel
    (:func:`run_walk_forward_parallel`) paths so a fold is computed identically
    regardless of how it is scheduled.

    The in-sample and out-of-sample windows share the boundary instant
    (``out_of_sample_start == in_sample_end``). The Backtester treats date ranges
    as inclusive on both ends, so the in-sample run ends strictly *before* the
    boundary bar. The boundary bar then belongs only to the out-of-sample segment:
    train and test ranges are disjoint by construction.
    """
    in_sample_end_exclusive = window.in_sample_end - timedelta(microseconds=1)
    in_sample_result = segment_runner(
        symbols, style, setup_filter, window.in_sample_start, in_sample_end_exclusive
    )
    oos_result = segment_runner(
        symbols, style, setup_filter, window.out_of_sample_start, window.out_of_sample_end
    )
    return evaluate_fold(window, in_sample_result.trades, oos_result.trades, config)


def run_walk_forward(
    segment_runner: SegmentRunner,
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    start: datetime,
    end: datetime,
    config: WalkForwardConfig,
) -> WalkForwardReport:
    """Run the full walk-forward analysis over ``[start, end]``.

    For each fold the ``segment_runner`` is invoked twice: once for the in-sample
    segment (tuning) and once for the out-of-sample segment (evaluation). The two
    calls use disjoint date ranges, so out-of-sample data can never influence the
    threshold choice.
    """

    windows = generate_windows(start, end, config)
    folds: list[FoldResult] = [
        _evaluate_single_fold(segment_runner, window, symbols, style, setup_filter, config)
        for window in windows
    ]

    aggregate_trades: list[TradeRecord] = []
    for fold in folds:
        aggregate_trades.extend(fold.oos_trade_records)
    aggregate_trades.sort(key=lambda trade: trade.exit_time)

    equity_curve: list[tuple[datetime, float]] = [(start, 0.0)]
    cumulative = 0.0
    for trade in aggregate_trades:
        cumulative += trade.net_r
        equity_curve.append((trade.exit_time, round(cumulative, 4)))

    return WalkForwardReport(
        config=config,
        symbols=symbols,
        style=style,
        setup_filter=setup_filter,
        start=start,
        end=end,
        folds=folds,
        aggregate_metrics=calculate_metrics(aggregate_trades),
        oos_equity_curve=equity_curve,
    )


def report_to_dict(report: WalkForwardReport) -> dict[str, object]:
    """Serialise a walk-forward report to a JSON-friendly dictionary."""

    setup_filter = report.setup_filter if isinstance(report.setup_filter, str) else report.setup_filter.value
    return {
        "config": {
            "in_sample_days": report.config.in_sample_days,
            "out_of_sample_days": report.config.out_of_sample_days,
            "step_days": report.config.step_days,
            "score_grid": list(report.config.score_grid),
            "min_in_sample_trades": report.config.min_in_sample_trades,
        },
        "symbols": list(report.symbols),
        "style": report.style.value,
        "setup_filter": setup_filter,
        "start": report.start.isoformat(),
        "end": report.end.isoformat(),
        "fold_count": len(report.folds),
        "out_of_sample": {
            "total_trades": report.aggregate_metrics.number_of_trades,
            "metrics": _metrics_to_dict(report.aggregate_metrics),
            "equity_curve": [[ts.isoformat(), value] for ts, value in report.oos_equity_curve],
        },
        "folds": [
            {
                "fold_index": fold.window.fold_index,
                "in_sample_start": fold.window.in_sample_start.isoformat(),
                "in_sample_end": fold.window.in_sample_end.isoformat(),
                "out_of_sample_start": fold.window.out_of_sample_start.isoformat(),
                "out_of_sample_end": fold.window.out_of_sample_end.isoformat(),
                "selected_min_score": fold.selected_min_score,
                "in_sample_trades": fold.in_sample_trades,
                "in_sample_expectancy": fold.in_sample_expectancy,
                "out_of_sample_trades": fold.out_of_sample_trades,
                "out_of_sample_metrics": _metrics_to_dict(fold.out_of_sample_metrics),
            }
            for fold in report.folds
        ],
    }


def report_to_text(report: WalkForwardReport) -> str:
    """Render a compact, human-readable walk-forward summary."""

    setup_filter = report.setup_filter if isinstance(report.setup_filter, str) else report.setup_filter.value
    aggregate = report.aggregate_metrics
    lines = [
        "Walk-Forward / Out-of-Sample Report (paper-only)",
        "================================================",
        f"symbols           : {', '.join(report.symbols)}",
        f"style             : {report.style.value}",
        f"setup_filter      : {setup_filter}",
        f"range             : {report.start.date()} -> {report.end.date()}",
        f"windows           : in_sample={report.config.in_sample_days}d "
        f"oos={report.config.out_of_sample_days}d step={report.config.step_days}d",
        f"score_grid        : {', '.join(f'{value:g}' for value in report.config.score_grid)}",
        f"folds             : {len(report.folds)}",
        "",
        "Aggregated OUT-OF-SAMPLE performance (thresholds tuned in-sample only):",
        f"  trades          : {aggregate.number_of_trades}",
        f"  expectancy/trade: {aggregate.expectancy:.4f} R",
        f"  win_rate        : {aggregate.win_rate:.2f}%",
        f"  profit_factor   : {aggregate.profit_factor:.4f}",
        f"  max_drawdown    : {aggregate.max_drawdown:.4f} R",
        "",
        "Per-fold breakdown:",
    ]
    for fold in report.folds:
        oos = fold.out_of_sample_metrics
        lines.append(
            f"  fold {fold.window.fold_index}: "
            f"IS[{fold.window.in_sample_start.date()}->{fold.window.in_sample_end.date()}] "
            f"OOS[{fold.window.out_of_sample_start.date()}->{fold.window.out_of_sample_end.date()}] "
            f"min_score={fold.selected_min_score:g} "
            f"IS_exp={fold.in_sample_expectancy:.4f}R(n={fold.in_sample_trades}) "
            f"OOS_exp={oos.expectancy:.4f}R(n={oos.number_of_trades}) "
            f"OOS_win={oos.win_rate:.2f}%"
        )
    return "\n".join(lines) + "\n"


def write_reports(report: WalkForwardReport, output_dir: Path) -> dict[str, Path]:
    """Write ``walk_forward.json`` and ``walk_forward.txt`` into ``output_dir``."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "walk_forward.json"
    text_path = output_dir / "walk_forward.txt"
    json_path.write_text(json.dumps(report_to_dict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text_path.write_text(report_to_text(report), encoding="utf-8")
    return {"json": json_path, "txt": text_path}


def _metrics_to_dict(metrics: BacktestMetrics) -> dict[str, object]:
    return metrics.model_dump()


def backtester_segment_runner(backtester) -> SegmentRunner:  # noqa: ANN001 - avoid import cycle
    """Adapt a :class:`Backtester` instance into a :class:`SegmentRunner`."""

    def _run(
        symbols: list[str],
        style: TradingStyle,
        setup_filter: SetupFamily | Literal["all"],
        start: datetime,
        end: datetime,
    ) -> BacktestResult:
        return backtester.run(symbols, style, setup_filter, start, end)

    return _run


def _passes(trade: TradeRecord, threshold: float) -> bool:
    if threshold <= 0.0:
        return True
    return trade.final_score is not None and trade.final_score >= threshold


# ---------------------------------------------------------------------------
# Parallel walk-forward  (--jobs N)
# ---------------------------------------------------------------------------
#
# Parallelisable unit:  one fold = one WalkForwardWindow. Folds are mutually
# independent — each runs its own in-sample + out-of-sample backtests and tunes
# its threshold using ONLY its own in-sample fold (no cross-fold state).
#
# Centralised (must NOT move into workers): the final reassembly. Folds are
# sorted into the canonical order (fold_index) on the parent process before
# aggregation; the aggregate trade pool is then sorted by exit_time exactly as
# the sequential path does. This makes every downstream figure — dedup, equity
# curve, aggregate metrics and bootstrap IC — independent of worker finish order.
#
# The threshold selection here is per-fold (in-sample only), so it already lives
# inside each worker. It is NOT a global inter-symbol selection, so nothing about
# it needs to be hoisted to the parent.


class RunnerFactory(Protocol):
    """Picklable factory that builds a :class:`SegmentRunner` inside a worker.

    A *factory* (rather than a ready-made runner) is sent across the process
    boundary because :class:`~app.backtest.engine.Backtester` instances and their
    providers are not meant to be pickled and shared; instead each worker builds
    its own, reading its own data locally (minimal IPC, no large DataFrames on
    the wire).
    """

    def __call__(self) -> SegmentRunner: ...


class _BacktesterRunnerFactory:
    """Default factory: build a fresh provider + :class:`Backtester` per worker.

    Holds only the (picklable) :class:`AppSettings`. Each worker process calls
    ``build_provider(settings)`` and constructs its own backtester, so no heavy
    state crosses the process boundary.
    """

    def __init__(self, settings: "AppSettings") -> None:
        self._settings = settings

    def __call__(self) -> SegmentRunner:
        from app.backtest.engine import Backtester
        from app.data.providers import build_provider

        provider = build_provider(self._settings)
        backtester = Backtester(self._settings, provider, database=None)
        return backtester_segment_runner(backtester)


@dataclass(frozen=True)
class _FoldTask:
    """Serialisable work unit passed to each ProcessPoolExecutor worker."""

    window: WalkForwardWindow
    symbols: tuple[str, ...]
    style: TradingStyle
    setup_filter: SetupFamily | Literal["all"]
    config: WalkForwardConfig
    runner_factory: RunnerFactory


def _fold_worker(task: _FoldTask) -> FoldResult:
    """Top-level (picklable) entry point executed inside each worker process."""
    runner = task.runner_factory()
    return _evaluate_single_fold(
        runner, task.window, list(task.symbols), task.style, task.setup_filter, task.config
    )


def run_walk_forward_parallel(
    settings: "AppSettings | None",
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    start: datetime,
    end: datetime,
    config: WalkForwardConfig,
    *,
    n_jobs: int,
    runner_factory: RunnerFactory | None = None,
) -> WalkForwardReport:
    """Walk-forward using *n_jobs* worker processes.

    Produces results **rigorously identical** to :func:`run_walk_forward`
    (``--jobs 1``): parallelisation changes only scheduling, never computation.
    Folds are sorted into canonical order (``fold_index``) on the parent process
    before aggregation, so the dedup, equity curve, aggregate metrics and
    bootstrap IC are independent of which worker finishes first.

    Parameters
    ----------
    settings:
        Full application settings (picklable pydantic model) used to build the
        default per-worker backtester. May be ``None`` only when an explicit
        ``runner_factory`` is supplied (e.g. in tests).
    n_jobs:
        Number of worker processes (>= 1).
    runner_factory:
        Optional picklable factory overriding the default settings-based
        backtester construction. Each worker calls it to obtain its own
        :class:`SegmentRunner`.
    """
    if runner_factory is None:
        if settings is None:
            raise ValueError("settings is required when runner_factory is not provided")
        runner_factory = _BacktesterRunnerFactory(settings)

    windows = generate_windows(start, end, config)
    tasks = [
        _FoldTask(
            window=window,
            symbols=tuple(symbols),
            style=style,
            setup_filter=setup_filter,
            config=config,
            runner_factory=runner_factory,
        )
        for window in windows
    ]

    with ProcessPoolExecutor(max_workers=n_jobs) as executor:
        fold_results = list(executor.map(_fold_worker, tasks))

    # Canonical reassembly — deterministic regardless of worker completion order.
    folds = sorted(fold_results, key=lambda f: f.window.fold_index)

    # Aggregation below is byte-for-byte identical to run_walk_forward.
    aggregate_trades: list[TradeRecord] = []
    for fold in folds:
        aggregate_trades.extend(fold.oos_trade_records)
    aggregate_trades.sort(key=lambda trade: trade.exit_time)

    equity_curve: list[tuple[datetime, float]] = [(start, 0.0)]
    cumulative = 0.0
    for trade in aggregate_trades:
        cumulative += trade.net_r
        equity_curve.append((trade.exit_time, round(cumulative, 4)))

    return WalkForwardReport(
        config=config,
        symbols=list(symbols),
        style=style,
        setup_filter=setup_filter,
        start=start,
        end=end,
        folds=folds,
        aggregate_metrics=calculate_metrics(aggregate_trades),
        oos_equity_curve=equity_curve,
    )
