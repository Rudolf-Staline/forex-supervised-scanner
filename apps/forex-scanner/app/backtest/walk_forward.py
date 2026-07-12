"""Walk-forward / out-of-sample evaluation harness for the rules-based scanner.

The harness enforces two distinct integrity guarantees:

* temporal hygiene: tuning uses only the in-sample segment of each fold;
* sample hygiene: overlapping out-of-sample windows contribute each realized
  trade exactly once to aggregate metrics, equity, and exported registries.

Per-fold diagnostics intentionally retain their original records. The canonical
aggregate keeps the first occurrence from the earliest fold, keyed by
``(symbol, entry_time)``.

Paper/demo only: nothing here sends orders.
"""

from __future__ import annotations

import csv
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
    """Walk-forward result with a canonical, de-duplicated OOS aggregate."""

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
    """Runs a backtest over one date segment and returns its result."""

    def __call__(
        self,
        symbols: list[str],
        style: TradingStyle,
        setup_filter: SetupFamily | Literal["all"],
        start: datetime,
        end: datetime,
    ) -> BacktestResult: ...


class RunnerFactory(Protocol):
    """Picklable factory that builds a :class:`SegmentRunner` in a worker."""

    def __call__(self) -> SegmentRunner: ...


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

    Only thresholds retaining at least ``min_in_sample_trades`` are eligible.
    Ties prefer the higher threshold. If no threshold qualifies, the lowest grid
    value is returned with the expectancy of the retained sample.
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
    """Tune on in-sample trades, then filter the out-of-sample fold."""

    selected, in_sample_expectancy = select_min_score(
        in_sample_trades,
        config.score_grid,
        config.min_in_sample_trades,
    )
    eligible_in_sample = [trade for trade in in_sample_trades if _passes(trade, selected)]
    retained_oos = [trade for trade in out_of_sample_trades if _passes(trade, selected)]
    retained_oos.sort(key=_chronological_trade_key)
    return FoldResult(
        window=window,
        selected_min_score=selected,
        in_sample_trades=len(eligible_in_sample),
        in_sample_expectancy=in_sample_expectancy,
        out_of_sample_trades=len(retained_oos),
        out_of_sample_metrics=calculate_metrics(retained_oos),
        oos_trade_records=retained_oos,
    )


def run_walk_forward(
    segment_runner: SegmentRunner,
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    start: datetime,
    end: datetime,
    config: WalkForwardConfig,
) -> WalkForwardReport:
    """Run sequential walk-forward analysis with canonical OOS aggregation."""

    windows = generate_windows(start, end, config)
    folds = [
        _evaluate_single_fold(
            segment_runner,
            window,
            symbols,
            style,
            setup_filter,
            config,
        )
        for window in windows
    ]
    return _assemble_report(
        config=config,
        symbols=symbols,
        style=style,
        setup_filter=setup_filter,
        start=start,
        end=end,
        folds=folds,
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
    """Run walk-forward folds in parallel with deterministic reassembly."""

    if n_jobs < 1:
        raise ValueError("n_jobs must be at least 1")
    if runner_factory is None:
        if settings is None:
            raise ValueError("settings is required when runner_factory is not provided")
        runner_factory = _BacktesterRunnerFactory(settings)

    tasks = [
        _FoldTask(
            window=window,
            symbols=tuple(symbols),
            style=style,
            setup_filter=setup_filter,
            config=config,
            runner_factory=runner_factory,
        )
        for window in generate_windows(start, end, config)
    ]
    with ProcessPoolExecutor(max_workers=n_jobs) as executor:
        fold_results = list(executor.map(_fold_worker, tasks))

    folds = sorted(fold_results, key=lambda fold: fold.window.fold_index)
    return _assemble_report(
        config=config,
        symbols=list(symbols),
        style=style,
        setup_filter=setup_filter,
        start=start,
        end=end,
        folds=folds,
    )


def deduplicated_oos_trades(report: WalkForwardReport) -> list[TradeRecord]:
    """Return the canonical OOS sample used by aggregate metrics and exports."""

    return _deduplicate_fold_trades(report.folds)


def raw_oos_trade_count(report: WalkForwardReport) -> int:
    """Count OOS records before overlap de-duplication."""

    return sum(len(fold.oos_trade_records) for fold in report.folds)


def report_to_dict(report: WalkForwardReport) -> dict[str, object]:
    """Serialise a walk-forward report to a JSON-friendly dictionary."""

    setup_filter = report.setup_filter if isinstance(report.setup_filter, str) else report.setup_filter.value
    raw_count = raw_oos_trade_count(report)
    unique_count = report.aggregate_metrics.number_of_trades
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
            "total_trades": unique_count,
            "raw_fold_trade_records": raw_count,
            "duplicates_removed": raw_count - unique_count,
            "aggregation": "deduplicated_by_symbol_entry_time_first_fold_wins",
            "metrics": _metrics_to_dict(report.aggregate_metrics),
            "equity_curve": [[timestamp.isoformat(), value] for timestamp, value in report.oos_equity_curve],
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
    raw_count = raw_oos_trade_count(report)
    unique_count = aggregate.number_of_trades
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
        "Canonical OUT-OF-SAMPLE performance (unique trades; thresholds tuned in-sample only):",
        f"  trades unique   : {unique_count}",
        f"  raw fold records: {raw_count}",
        f"  duplicates      : {raw_count - unique_count}",
        f"  expectancy/trade: {aggregate.expectancy:.4f} R",
        f"  win_rate        : {aggregate.win_rate:.2f}%",
        f"  profit_factor   : {aggregate.profit_factor:.4f}",
        f"  max_drawdown    : {aggregate.max_drawdown:.4f} R",
        "",
        "Per-fold breakdown (overlapping windows may repeat trades here):",
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


def oos_registry_rows(report: WalkForwardReport) -> list[dict[str, object]]:
    """Build the canonical OOS trade registry, one row per unique trade."""

    rows: list[dict[str, object]] = []
    for trade in deduplicated_oos_trades(report):
        entry = trade.entry_time
        rows.append(
            {
                "pair": trade.symbol,
                "timestamp": entry.isoformat() if hasattr(entry, "isoformat") else str(entry),
                "score": "" if trade.final_score is None else round(float(trade.final_score), 6),
                "gross_r": round(float(trade.gross_r), 6),
                "net_r": round(float(trade.net_r), 6),
                "exit_reason": trade.exit_reason,
            }
        )
    return rows


def write_oos_registry(report: WalkForwardReport, path: Path) -> Path:
    """Write the canonical OOS registry as CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = oos_registry_rows(report)
    fieldnames = ["pair", "timestamp", "score", "gross_r", "net_r", "exit_reason"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_reports(report: WalkForwardReport, output_dir: Path) -> dict[str, Path]:
    """Write JSON/TXT reports and the same canonical OOS registry they summarize."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "walk_forward.json"
    text_path = output_dir / "walk_forward.txt"
    registry_path = output_dir / "oos_trade_registry.csv"
    json_path.write_text(
        json.dumps(report_to_dict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(report_to_text(report), encoding="utf-8")
    write_oos_registry(report, registry_path)
    return {"json": json_path, "txt": text_path, "registry": registry_path}


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


def _evaluate_single_fold(
    segment_runner: SegmentRunner,
    window: WalkForwardWindow,
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    config: WalkForwardConfig,
) -> FoldResult:
    """Run one fold with a strictly disjoint IS/OOS boundary."""

    in_sample_end_exclusive = window.in_sample_end - timedelta(microseconds=1)
    in_sample_result = segment_runner(
        symbols,
        style,
        setup_filter,
        window.in_sample_start,
        in_sample_end_exclusive,
    )
    oos_result = segment_runner(
        symbols,
        style,
        setup_filter,
        window.out_of_sample_start,
        window.out_of_sample_end,
    )
    return evaluate_fold(window, in_sample_result.trades, oos_result.trades, config)


def _assemble_report(
    *,
    config: WalkForwardConfig,
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    start: datetime,
    end: datetime,
    folds: list[FoldResult],
) -> WalkForwardReport:
    """Build all aggregate artifacts from one canonical OOS trade sample."""

    canonical_trades = _deduplicate_fold_trades(folds)
    equity_curve: list[tuple[datetime, float]] = [(start, 0.0)]
    cumulative = 0.0
    for trade in canonical_trades:
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
        aggregate_metrics=calculate_metrics(canonical_trades),
        oos_equity_curve=equity_curve,
    )


def _deduplicate_fold_trades(folds: list[FoldResult]) -> list[TradeRecord]:
    """Keep the earliest-fold occurrence, then order trades by realized chronology."""

    seen: set[tuple[str, datetime]] = set()
    unique: list[TradeRecord] = []
    for fold in sorted(folds, key=lambda item: item.window.fold_index):
        for trade in fold.oos_trade_records:
            key = (trade.symbol, trade.entry_time)
            if key in seen:
                continue
            seen.add(key)
            unique.append(trade)
    unique.sort(key=_chronological_trade_key)
    return unique


def _chronological_trade_key(trade: TradeRecord) -> tuple[datetime, str, datetime]:
    return trade.exit_time, trade.symbol, trade.entry_time


def _passes(trade: TradeRecord, threshold: float) -> bool:
    if threshold <= 0.0:
        return True
    return trade.final_score is not None and trade.final_score >= threshold


def _metrics_to_dict(metrics: BacktestMetrics) -> dict[str, object]:
    return metrics.model_dump()


class _BacktesterRunnerFactory:
    """Build a fresh provider and backtester inside each process."""

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
    """Serializable work unit passed to a process worker."""

    window: WalkForwardWindow
    symbols: tuple[str, ...]
    style: TradingStyle
    setup_filter: SetupFamily | Literal["all"]
    config: WalkForwardConfig
    runner_factory: RunnerFactory


def _fold_worker(task: _FoldTask) -> FoldResult:
    runner = task.runner_factory()
    return _evaluate_single_fold(
        runner,
        task.window,
        list(task.symbols),
        task.style,
        task.setup_filter,
        task.config,
    )
