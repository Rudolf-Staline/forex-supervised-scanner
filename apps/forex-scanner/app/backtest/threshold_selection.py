"""Conservative score-threshold selection for walk-forward research.

The selector compares score cut-offs using only in-sample trades. Its default
objective is a zero-prior shrinkage estimate minus an uncertainty penalty. A fold
can explicitly abstain when no threshold has a positive conservative objective.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.core.types import TradeRecord

ThresholdObjective = Literal["conservative_lcb", "mean_expectancy"]
ThresholdSelectionStatus = Literal["selected", "abstain", "fallback"]
ABSTAIN_SCORE_THRESHOLD = 101.0


@dataclass(frozen=True)
class ThresholdSelectionConfig:
    """Statistical controls for one in-sample threshold search."""

    score_grid: tuple[float, ...]
    min_trades: int = 50
    objective: ThresholdObjective = "conservative_lcb"
    shrinkage_trades: float = 50.0
    confidence_z: float = 1.645
    minimum_objective_r: float = 0.0
    allow_abstention: bool = True
    abstain_threshold: float = ABSTAIN_SCORE_THRESHOLD

    def __post_init__(self) -> None:
        if not self.score_grid:
            raise ValueError("score_grid must contain at least one threshold")
        if self.min_trades < 2:
            raise ValueError("min_trades must be at least 2")
        if self.objective not in {"conservative_lcb", "mean_expectancy"}:
            raise ValueError(f"unsupported threshold objective: {self.objective}")
        if self.shrinkage_trades < 0.0:
            raise ValueError("shrinkage_trades cannot be negative")
        if self.confidence_z < 0.0:
            raise ValueError("confidence_z cannot be negative")
        if self.abstain_threshold <= max(self.score_grid):
            raise ValueError("abstain_threshold must be above every score-grid threshold")


@dataclass(frozen=True)
class ThresholdCandidate:
    """Diagnostics for one eligible score threshold."""

    threshold: float
    trades: int
    raw_expectancy_r: float
    standard_deviation_r: float
    standard_error_r: float
    shrinkage_weight: float
    shrunk_expectancy_r: float
    lower_confidence_bound_r: float
    objective_r: float


@dataclass(frozen=True)
class ThresholdSelection:
    """Selected threshold or an explicit abstention decision."""

    selected_threshold: float
    status: ThresholdSelectionStatus
    reason: str
    selected_candidate: ThresholdCandidate | None
    candidates: tuple[ThresholdCandidate, ...]

    @property
    def abstained(self) -> bool:
        return self.status == "abstain"


def select_threshold(
    in_sample_trades: list[TradeRecord],
    config: ThresholdSelectionConfig,
) -> ThresholdSelection:
    """Choose a threshold using only the supplied in-sample trades."""

    candidates: list[ThresholdCandidate] = []
    for threshold in sorted(set(config.score_grid)):
        retained = [trade for trade in in_sample_trades if _passes(trade, threshold)]
        if len(retained) < config.min_trades:
            continue
        candidates.append(_evaluate_candidate(threshold, retained, config))

    if not candidates:
        if config.allow_abstention:
            return ThresholdSelection(
                selected_threshold=config.abstain_threshold,
                status="abstain",
                reason=(
                    "no threshold retained the required minimum of "
                    f"{config.min_trades} in-sample trades"
                ),
                selected_candidate=None,
                candidates=(),
            )
        fallback = min(config.score_grid)
        return ThresholdSelection(
            selected_threshold=fallback,
            status="fallback",
            reason="no threshold met the sample-size requirement; lowest grid value used",
            selected_candidate=None,
            candidates=(),
        )

    best = max(candidates, key=lambda item: (item.objective_r, item.threshold))
    ordered = tuple(sorted(candidates, key=lambda item: item.threshold))
    if config.allow_abstention and best.objective_r <= config.minimum_objective_r:
        return ThresholdSelection(
            selected_threshold=config.abstain_threshold,
            status="abstain",
            reason=(
                f"best {config.objective} objective {best.objective_r:.6f}R is not above "
                f"the required {config.minimum_objective_r:.6f}R"
            ),
            selected_candidate=best,
            candidates=ordered,
        )

    return ThresholdSelection(
        selected_threshold=best.threshold,
        status="selected",
        reason=(
            f"threshold {best.threshold:g} maximized {config.objective} at "
            f"{best.objective_r:.6f}R with n={best.trades}"
        ),
        selected_candidate=best,
        candidates=ordered,
    )


def _evaluate_candidate(
    threshold: float,
    trades: list[TradeRecord],
    config: ThresholdSelectionConfig,
) -> ThresholdCandidate:
    returns = [float(trade.net_r) for trade in trades]
    count = len(returns)
    mean = sum(returns) / count
    variance = sum((value - mean) ** 2 for value in returns) / (count - 1)
    standard_deviation = math.sqrt(max(variance, 0.0))
    standard_error = standard_deviation / math.sqrt(count)
    shrinkage_weight = count / (count + config.shrinkage_trades)
    shrunk_expectancy = shrinkage_weight * mean
    lower_bound = shrunk_expectancy - config.confidence_z * standard_error
    objective = lower_bound if config.objective == "conservative_lcb" else mean
    return ThresholdCandidate(
        threshold=float(threshold),
        trades=count,
        raw_expectancy_r=mean,
        standard_deviation_r=standard_deviation,
        standard_error_r=standard_error,
        shrinkage_weight=shrinkage_weight,
        shrunk_expectancy_r=shrunk_expectancy,
        lower_confidence_bound_r=lower_bound,
        objective_r=objective,
    )


def _passes(trade: TradeRecord, threshold: float) -> bool:
    if threshold <= 0.0:
        return True
    return trade.final_score is not None and float(trade.final_score) >= threshold
