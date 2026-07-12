"""Walk-forward harness with conservative in-sample threshold selection.

This module reuses the canonical window, fold, report, de-duplication, and
serialization types from :mod:`app.backtest.walk_forward` while replacing only
the threshold-selection policy.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from app.backtest.metrics import calculate_metrics
from app.backtest.threshold_selection import (
    ThresholdCandidate,
    ThresholdSelection,
    ThresholdSelectionConfig,
    select_threshold,
)
from app.backtest.walk_forward import (
    FoldResult,
    SegmentRunner,
    WalkForwardConfig,
    WalkForwardReport,
    WalkForwardWindow,
    _assemble_report,
    generate_windows,
    report_to_dict,
)
from app.core.types import BacktestResult, SetupFamily, TradeRecord, TradingStyle

if TYPE_CHECKING:
    from app.config.settings import AppSettings


@dataclass(frozen=True)
class ConservativeWalkForwardResult:
    """Ordinary walk-forward report plus threshold-selection diagnostics."""

    report: WalkForwardReport
    threshold_config: ThresholdSelectionConfig
    selections: list[ThresholdSelection]


class RunnerFactory(Protocol):
    """Picklable factory that creates a segment runner inside a worker."""

    def __call__(self) -> SegmentRunner: ...


def evaluate_fold_conservative(
    window: WalkForwardWindow,
    in_sample_trades: list[TradeRecord],
    out_of_sample_trades: list[TradeRecord],
    threshold_config: ThresholdSelectionConfig,
) -> tuple[FoldResult, ThresholdSelection]:
    """Select a conservative in-sample threshold and apply it to the OOS fold."""

    selection = select_threshold(in_sample_trades, threshold_config)
    selected = selection.selected_threshold
    eligible_in_sample = [trade for trade in in_sample_trades if _passes(trade, selected)]
    retained_oos = [trade for trade in out_of_sample_trades if _passes(trade, selected)]
    retained_oos.sort(key=lambda trade: (trade.exit_time, trade.symbol, trade.entry_time))
    selected_expectancy = (
        selection.selected_candidate.raw_expectancy_r
        if selection.selected_candidate is not None and not selection.abstained
        else 0.0
    )
    fold = FoldResult(
        window=window,
        selected_min_score=selected,
        in_sample_trades=len(eligible_in_sample),
        in_sample_expectancy=round(selected_expectancy, 4),
        out_of_sample_trades=len(retained_oos),
        out_of_sample_metrics=calculate_metrics(retained_oos),
        oos_trade_records=retained_oos,
    )
    return fold, selection


def run_conservative_walk_forward(
    segment_runner: SegmentRunner,
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    start: datetime,
    end: datetime,
    walk_forward_config: WalkForwardConfig,
    threshold_config: ThresholdSelectionConfig,
) -> ConservativeWalkForwardResult:
    """Run sequential walk-forward evaluation with conservative thresholds."""

    evaluated = [
        _evaluate_single_fold(
            segment_runner,
            window,
            symbols,
            style,
            setup_filter,
            threshold_config,
        )
        for window in generate_windows(start, end, walk_forward_config)
    ]
    folds = [item[0] for item in evaluated]
    selections = [item[1] for item in evaluated]
    report = _assemble_report(
        config=walk_forward_config,
        symbols=symbols,
        style=style,
        setup_filter=setup_filter,
        start=start,
        end=end,
        folds=folds,
    )
    return ConservativeWalkForwardResult(report, threshold_config, selections)


def run_conservative_walk_forward_parallel(
    settings: "AppSettings | None",
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    start: datetime,
    end: datetime,
    walk_forward_config: WalkForwardConfig,
    threshold_config: ThresholdSelectionConfig,
    *,
    n_jobs: int,
    runner_factory: RunnerFactory | None = None,
) -> ConservativeWalkForwardResult:
    """Run conservative walk-forward folds in parallel and reassemble deterministically."""

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
            threshold_config=threshold_config,
            runner_factory=runner_factory,
        )
        for window in generate_windows(start, end, walk_forward_config)
    ]
    with ProcessPoolExecutor(max_workers=n_jobs) as executor:
        evaluated = list(executor.map(_fold_worker, tasks))
    evaluated.sort(key=lambda item: item[0].window.fold_index)
    folds = [item[0] for item in evaluated]
    selections = [item[1] for item in evaluated]
    report = _assemble_report(
        config=walk_forward_config,
        symbols=list(symbols),
        style=style,
        setup_filter=setup_filter,
        start=start,
        end=end,
        folds=folds,
    )
    return ConservativeWalkForwardResult(report, threshold_config, selections)


def threshold_result_to_dict(result: ConservativeWalkForwardResult) -> dict[str, object]:
    """Serialize threshold diagnostics alongside the ordinary walk-forward report."""

    payload = report_to_dict(result.report)
    payload["threshold_selection"] = {
        "config": _config_to_dict(result.threshold_config),
        "selected_folds": sum(selection.status == "selected" for selection in result.selections),
        "abstained_folds": sum(selection.status == "abstain" for selection in result.selections),
        "fallback_folds": sum(selection.status == "fallback" for selection in result.selections),
        "folds": [
            {
                "fold_index": fold.window.fold_index,
                **_selection_to_dict(selection),
            }
            for fold, selection in zip(result.report.folds, result.selections, strict=True)
        ],
    }
    return payload


def threshold_result_to_text(result: ConservativeWalkForwardResult) -> str:
    """Render a concise threshold-selection audit."""

    config = result.threshold_config
    selected = sum(selection.status == "selected" for selection in result.selections)
    abstained = sum(selection.status == "abstain" for selection in result.selections)
    fallback = sum(selection.status == "fallback" for selection in result.selections)
    lines = [
        "Conservative Threshold Selection",
        "================================",
        f"objective              : {config.objective}",
        f"minimum IS trades      : {config.min_trades}",
        f"shrinkage prior trades : {config.shrinkage_trades:g}",
        f"confidence z           : {config.confidence_z:g}",
        f"minimum objective      : {config.minimum_objective_r:.4f} R",
        f"abstention enabled     : {config.allow_abstention}",
        f"folds selected         : {selected}",
        f"folds abstained        : {abstained}",
        f"folds fallback         : {fallback}",
        "",
        "Per-fold decisions:",
    ]
    for fold, selection in zip(result.report.folds, result.selections, strict=True):
        candidate = selection.selected_candidate
        threshold = "ABSTAIN" if selection.abstained else f"{selection.selected_threshold:g}"
        if candidate is None:
            diagnostics = "n=0 mean=0.0000R shrunk=0.0000R lcb=0.0000R"
        else:
            diagnostics = (
                f"n={candidate.trades} mean={candidate.raw_expectancy_r:.4f}R "
                f"shrunk={candidate.shrunk_expectancy_r:.4f}R "
                f"lcb={candidate.lower_confidence_bound_r:.4f}R"
            )
        lines.append(
            f"  fold {fold.window.fold_index}: status={selection.status} "
            f"threshold={threshold} {diagnostics}"
        )
    return "\n".join(lines) + "\n"


def write_threshold_reports(
    result: ConservativeWalkForwardResult,
    output_dir: Path,
) -> dict[str, Path]:
    """Write threshold-selection JSON and text diagnostics."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "threshold_selection.json"
    text_path = output_dir / "threshold_selection.txt"
    json_path.write_text(
        json.dumps(threshold_result_to_dict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    text_path.write_text(threshold_result_to_text(result), encoding="utf-8")
    return {"json": json_path, "txt": text_path}


def _evaluate_single_fold(
    segment_runner: SegmentRunner,
    window: WalkForwardWindow,
    symbols: list[str],
    style: TradingStyle,
    setup_filter: SetupFamily | Literal["all"],
    threshold_config: ThresholdSelectionConfig,
) -> tuple[FoldResult, ThresholdSelection]:
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
    return evaluate_fold_conservative(
        window,
        in_sample_result.trades,
        oos_result.trades,
        threshold_config,
    )


def _selection_to_dict(selection: ThresholdSelection) -> dict[str, object]:
    return {
        "selected_threshold": selection.selected_threshold,
        "status": selection.status,
        "reason": selection.reason,
        "selected_candidate": (
            None
            if selection.selected_candidate is None
            else _candidate_to_dict(selection.selected_candidate)
        ),
        "candidates": [_candidate_to_dict(candidate) for candidate in selection.candidates],
    }


def _candidate_to_dict(candidate: ThresholdCandidate) -> dict[str, object]:
    return {
        "threshold": candidate.threshold,
        "trades": candidate.trades,
        "raw_expectancy_r": round(candidate.raw_expectancy_r, 8),
        "standard_deviation_r": round(candidate.standard_deviation_r, 8),
        "standard_error_r": round(candidate.standard_error_r, 8),
        "shrinkage_weight": round(candidate.shrinkage_weight, 8),
        "shrunk_expectancy_r": round(candidate.shrunk_expectancy_r, 8),
        "lower_confidence_bound_r": round(candidate.lower_confidence_bound_r, 8),
        "objective_r": round(candidate.objective_r, 8),
    }


def _config_to_dict(config: ThresholdSelectionConfig) -> dict[str, object]:
    return {
        "score_grid": list(config.score_grid),
        "min_trades": config.min_trades,
        "objective": config.objective,
        "shrinkage_trades": config.shrinkage_trades,
        "confidence_z": config.confidence_z,
        "minimum_objective_r": config.minimum_objective_r,
        "allow_abstention": config.allow_abstention,
        "abstain_threshold": config.abstain_threshold,
    }


def _passes(trade: TradeRecord, threshold: float) -> bool:
    if threshold <= 0.0:
        return True
    return trade.final_score is not None and float(trade.final_score) >= threshold


class _BacktesterRunnerFactory:
    def __init__(self, settings: "AppSettings") -> None:
        self._settings = settings

    def __call__(self) -> SegmentRunner:
        from app.backtest.engine import Backtester
        from app.data.providers import build_provider

        provider = build_provider(self._settings)
        backtester = Backtester(self._settings, provider, database=None)

        def _run(
            symbols: list[str],
            style: TradingStyle,
            setup_filter: SetupFamily | Literal["all"],
            start: datetime,
            end: datetime,
        ) -> BacktestResult:
            return backtester.run(symbols, style, setup_filter, start, end)

        return _run


@dataclass(frozen=True)
class _FoldTask:
    window: WalkForwardWindow
    symbols: tuple[str, ...]
    style: TradingStyle
    setup_filter: SetupFamily | Literal["all"]
    threshold_config: ThresholdSelectionConfig
    runner_factory: RunnerFactory


def _fold_worker(task: _FoldTask) -> tuple[FoldResult, ThresholdSelection]:
    runner = task.runner_factory()
    return _evaluate_single_fold(
        runner,
        task.window,
        list(task.symbols),
        task.style,
        task.setup_filter,
        task.threshold_config,
    )
