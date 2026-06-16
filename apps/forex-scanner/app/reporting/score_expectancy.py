"""Score -> realized expectancy calibration from backtest trade records.

Given the :class:`~app.core.types.TradeRecord` list produced by a backtest, this
module answers one question: *does a higher ``final_score`` actually buy a higher
realized expectancy (net R)?* It buckets trades by score decile, measures realized
expectancy with a bootstrap confidence interval per bucket, tests monotonicity, and
flags the individual score components (technical/execution/context/empirical) that
fail to separate winners from losers.

Reporting only; paper/demo. No orders are sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

import numpy as np
import pandas as pd

from app.core.types import TradeRecord

COMPONENT_FIELDS: tuple[str, ...] = (
    "technical_score",
    "execution_score",
    "context_score",
    "empirical_score",
    "final_score",
)
# Spearman magnitude below which a component is deemed non-separating.
SEPARATION_THRESHOLD = 0.10


@dataclass(frozen=True)
class ScoreBucket:
    """One score decile (or rank bucket) with realized expectancy statistics."""

    index: int
    label: str
    score_min: float
    score_max: float
    samples: int
    expectancy: float
    win_rate: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True)
class ComponentSeparation:
    """Spearman separation of one score component against outcomes."""

    component: str
    samples: int
    spearman_to_expectancy: float
    spearman_to_win: float
    separates: bool


@dataclass(frozen=True)
class ScoreExpectancyReport:
    """Full score->expectancy calibration result."""

    trade_count: int
    scored_trades: int
    requested_buckets: int
    buckets: list[ScoreBucket]
    monotonic_non_decreasing: bool
    spearman_score_to_expectancy: float
    components: list[ComponentSeparation] = field(default_factory=list)
    flagged_components: list[str] = field(default_factory=list)


def build_report(
    trades: list[TradeRecord],
    *,
    n_buckets: int = 10,
    bootstrap_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 1729,
) -> ScoreExpectancyReport:
    """Build the calibration report from backtest trades."""

    scored = [trade for trade in trades if trade.final_score is not None]
    frame = pd.DataFrame(
        {
            "final_score": [float(trade.final_score) for trade in scored],
            "net_r": [float(trade.net_r) for trade in scored],
            "is_win": [1.0 if trade.net_r > 0.0 else 0.0 for trade in scored],
            "technical_score": [_optional(trade.technical_score) for trade in scored],
            "execution_score": [_optional(trade.execution_score) for trade in scored],
            "context_score": [_optional(trade.context_score) for trade in scored],
            "empirical_score": [_optional(trade.empirical_score) for trade in scored],
        }
    )

    return build_report_from_frame(
        frame,
        n_buckets=n_buckets,
        bootstrap_resamples=bootstrap_resamples,
        confidence=confidence,
        seed=seed,
        total_trades=len(trades),
    )


def build_report_from_frame(
    frame: pd.DataFrame,
    *,
    n_buckets: int = 10,
    bootstrap_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 1729,
    total_trades: int | None = None,
) -> ScoreExpectancyReport:
    """Build the calibration report from a frame with ``final_score`` and ``net_r``.

    Used both by :func:`build_report` (from TradeRecords) and by registry-based
    consumers. Component columns (``technical_score`` …) are optional; absent
    components are simply not evaluated.
    """

    scored = frame.dropna(subset=["final_score"]).copy() if "final_score" in frame else frame.iloc[0:0].copy()
    if "is_win" not in scored.columns and "net_r" in scored.columns:
        scored["is_win"] = (scored["net_r"] > 0.0).astype(float)
    buckets = _build_buckets(scored, n_buckets, bootstrap_resamples, confidence, seed)
    monotonic = _is_non_decreasing([bucket.expectancy for bucket in buckets])
    spearman_score = _spearman(scored.get("final_score"), scored.get("net_r"))
    components = _component_separation(scored)
    flagged = [component.component for component in components if not component.separates]

    return ScoreExpectancyReport(
        trade_count=total_trades if total_trades is not None else int(len(scored)),
        scored_trades=int(len(scored)),
        requested_buckets=n_buckets,
        buckets=buckets,
        monotonic_non_decreasing=monotonic,
        spearman_score_to_expectancy=spearman_score,
        components=components,
        flagged_components=flagged,
    )


def spearman_ci(
    x: pd.Series,
    y: pd.Series,
    *,
    resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 1729,
) -> tuple[float, float, float]:
    """Spearman rank correlation with a percentile-bootstrap CI.

    Returns ``(rho, ci_low, ci_high)``. Resamples paired observations with
    replacement. Returns zeros if there are too few/degenerate points.
    """

    paired = pd.DataFrame({"x": x, "y": y}).dropna().reset_index(drop=True)
    rho = _spearman(paired.get("x"), paired.get("y"))
    if len(paired) < 5:
        return rho, 0.0, 0.0
    rng = np.random.default_rng(seed)
    n = len(paired)
    xv = paired["x"].to_numpy()
    yv = paired["y"].to_numpy()
    estimates: list[float] = []
    for _ in range(resamples):
        idx = rng.integers(0, n, size=n)
        estimates.append(_spearman(pd.Series(xv[idx]), pd.Series(yv[idx])))
    alpha = (1.0 - confidence) / 2.0
    return rho, round(float(np.quantile(estimates, alpha)), 4), round(float(np.quantile(estimates, 1.0 - alpha)), 4)


def bootstrap_ci(
    values: np.ndarray,
    *,
    resamples: int,
    confidence: float,
    seed: int,
) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean of ``values``."""

    if values.size == 0:
        return 0.0, 0.0
    if values.size == 1:
        return float(values[0]), float(values[0])
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, values.size, size=(resamples, values.size))
    means = values[indices].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    low = float(np.quantile(means, alpha))
    high = float(np.quantile(means, 1.0 - alpha))
    return round(low, 4), round(high, 4)


def _build_buckets(
    frame: pd.DataFrame,
    n_buckets: int,
    resamples: int,
    confidence: float,
    seed: int,
) -> list[ScoreBucket]:
    if frame.empty:
        return []
    ordered = frame.sort_values("final_score", kind="mergesort").reset_index(drop=True)
    # Rank-based bucketing guarantees populated buckets even with ties/small samples.
    effective = max(1, min(n_buckets, len(ordered)))
    group_ids = np.floor(np.arange(len(ordered)) * effective / len(ordered)).astype(int)
    group_ids = np.clip(group_ids, 0, effective - 1)
    ordered["bucket"] = group_ids

    buckets: list[ScoreBucket] = []
    for index in range(effective):
        rows = ordered[ordered["bucket"] == index]
        if rows.empty:
            continue
        net_r = rows["net_r"].to_numpy(dtype=float)
        score_min = float(rows["final_score"].min())
        score_max = float(rows["final_score"].max())
        ci_low, ci_high = bootstrap_ci(net_r, resamples=resamples, confidence=confidence, seed=seed + index)
        buckets.append(
            ScoreBucket(
                index=index,
                label=f"D{index + 1}",
                score_min=round(score_min, 4),
                score_max=round(score_max, 4),
                samples=len(rows),
                expectancy=round(float(net_r.mean()), 4),
                win_rate=round(float(rows["is_win"].mean() * 100.0), 2),
                ci_low=ci_low,
                ci_high=ci_high,
            )
        )
    return buckets


def _component_separation(frame: pd.DataFrame) -> list[ComponentSeparation]:
    components: list[ComponentSeparation] = []
    for column in COMPONENT_FIELDS:
        if column not in frame:
            continue
        sample = frame[[column, "net_r", "is_win"]].dropna()
        spearman_r = _spearman(sample.get(column), sample.get("net_r"))
        spearman_win = _spearman(sample.get(column), sample.get("is_win"))
        separates = max(abs(spearman_r), abs(spearman_win)) >= SEPARATION_THRESHOLD
        components.append(
            ComponentSeparation(
                component=column.replace("_score", ""),
                samples=int(len(sample)),
                spearman_to_expectancy=spearman_r,
                spearman_to_win=spearman_win,
                separates=separates,
            )
        )
    return components


def _spearman(left: pd.Series | None, right: pd.Series | None) -> float:
    """Spearman rank correlation computed without scipy (Pearson over average ranks)."""

    if left is None or right is None:
        return 0.0
    paired = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(paired) < 3 or paired["left"].nunique() < 2 or paired["right"].nunique() < 2:
        return 0.0
    left_ranks = paired["left"].rank(method="average").to_numpy()
    right_ranks = paired["right"].rank(method="average").to_numpy()
    matrix = np.corrcoef(left_ranks, right_ranks)
    value = float(matrix[0, 1])
    return round(value, 4) if np.isfinite(value) else 0.0


def _is_non_decreasing(values: list[float]) -> bool:
    return all(values[i] <= values[i + 1] + 1e-9 for i in range(len(values) - 1))


def _optional(value: float | None) -> float:
    return float(value) if value is not None else float("nan")


def report_to_dict(report: ScoreExpectancyReport) -> dict[str, object]:
    return {
        "trade_count": report.trade_count,
        "scored_trades": report.scored_trades,
        "requested_buckets": report.requested_buckets,
        "monotonic_non_decreasing": report.monotonic_non_decreasing,
        "spearman_score_to_expectancy": report.spearman_score_to_expectancy,
        "buckets": [
            {
                "index": bucket.index,
                "label": bucket.label,
                "score_min": bucket.score_min,
                "score_max": bucket.score_max,
                "samples": bucket.samples,
                "expectancy": bucket.expectancy,
                "win_rate": bucket.win_rate,
                "ci_low": bucket.ci_low,
                "ci_high": bucket.ci_high,
            }
            for bucket in report.buckets
        ],
        "components": [
            {
                "component": component.component,
                "samples": component.samples,
                "spearman_to_expectancy": component.spearman_to_expectancy,
                "spearman_to_win": component.spearman_to_win,
                "separates": component.separates,
            }
            for component in report.components
        ],
        "flagged_components": list(report.flagged_components),
    }


def report_to_text(report: ScoreExpectancyReport) -> str:
    lines = [
        "Score -> Expectancy Calibration (paper-only)",
        "============================================",
        f"trades analyzed   : {report.trade_count} ({report.scored_trades} with a final score)",
        f"score buckets      : {len(report.buckets)} (requested {report.requested_buckets})",
        f"monotonic (non-dec): {'yes' if report.monotonic_non_decreasing else 'no'}",
        f"spearman score->R  : {report.spearman_score_to_expectancy:.4f}",
        "",
        "Reliability table (decile -> realized expectancy):",
        f"  {'bucket':<7}{'score_range':<20}{'n':>5}{'exp_R':>10}{'win%':>8}{'ci_low':>10}{'ci_high':>10}",
    ]
    for bucket in report.buckets:
        score_range = f"{bucket.score_min:.1f}-{bucket.score_max:.1f}"
        lines.append(
            f"  {bucket.label:<7}{score_range:<20}{bucket.samples:>5}{bucket.expectancy:>10.4f}"
            f"{bucket.win_rate:>8.2f}{bucket.ci_low:>10.4f}{bucket.ci_high:>10.4f}"
        )
    lines.append("")
    lines.append("Component separation (Spearman vs realized R / win flag):")
    for component in report.components:
        flag = "" if component.separates else "  <-- does NOT separate outcomes"
        lines.append(
            f"  {component.component:<12} n={component.samples:<5} "
            f"R={component.spearman_to_expectancy:>7.4f} win={component.spearman_to_win:>7.4f}{flag}"
        )
    if report.flagged_components:
        lines.append("")
        lines.append(f"Flagged (non-separating) components: {', '.join(report.flagged_components)}")
    return "\n".join(lines) + "\n"


def write_reports(report: ScoreExpectancyReport, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "score_expectancy_calibration.json"
    text_path = output_dir / "score_expectancy_calibration.txt"
    json_path.write_text(json.dumps(report_to_dict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text_path.write_text(report_to_text(report), encoding="utf-8")
    return {"json": json_path, "txt": text_path}
