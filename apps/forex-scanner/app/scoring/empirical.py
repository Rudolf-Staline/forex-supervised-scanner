"""Empirical scoring with shrinkage and context-sensitive fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean


@dataclass(frozen=True)
class EmpiricalQuery:
    """Comparable setup identity for historical empirical scoring."""

    symbol: str
    style: str
    family: str
    subtype: str
    session: str
    regime: str


@dataclass(frozen=True)
class EmpiricalEstimate:
    """Smoothed empirical score and diagnostics for calibration transparency."""

    score: float
    sample_size: int
    matched_groups: list[str] = field(default_factory=list)
    adjustment: float = 0.0
    conditional_adjustments: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class _AggregateSpec:
    name: str
    filters: dict[str, str]
    specificity: float


def estimate_empirical_score(
    records: list[dict[str, object]],
    query: EmpiricalQuery,
    neutral_score: float,
    minimum_samples: int,
    min_condition_samples: int,
    shrinkage_samples: int,
    max_adjustment: float,
) -> EmpiricalEstimate:
    """Estimate empirical setup quality with shrinkage and graceful backoff.

    Narrow aggregates are allowed to influence ranking, but their contribution is
    shrunk toward neutral unless sample size is meaningful. Broader aggregates
    provide a stabilizing fallback so rare combinations do not become
    overconfident.
    """

    specs = _aggregate_specs(query)
    weighted_adjustments: list[tuple[float, float]] = []
    matched_groups: list[str] = []
    conditional_adjustments: dict[str, float] = {}
    total_samples = 0
    for spec in specs:
        values = [_r_value(record) for record in records if _matches(record, spec.filters)]
        scored = [value for value in values if value is not None]
        sample_size = len(scored)
        if sample_size < min_condition_samples:
            continue
        raw_score = _score_from_r_values(scored, neutral_score)
        shrink = sample_size / (sample_size + max(shrinkage_samples, 1))
        evidence = min(1.0, sample_size / max(minimum_samples, 1))
        group_adjustment = (raw_score - neutral_score) * shrink
        weighted_adjustments.append((group_adjustment, spec.specificity * evidence))
        matched_groups.append(f"{spec.name}:{sample_size}")
        if spec.name in {"subtype_symbol", "subtype_session", "subtype_regime", "symbol_style"}:
            conditional_adjustments[spec.name] = round(max(-max_adjustment, min(max_adjustment, group_adjustment)), 2)
        total_samples += sample_size

    if not weighted_adjustments:
        return EmpiricalEstimate(score=round(neutral_score, 2), sample_size=0, matched_groups=[], adjustment=0.0, conditional_adjustments={})

    numerator = sum(adjustment * weight for adjustment, weight in weighted_adjustments)
    denominator = sum(weight for _adjustment, weight in weighted_adjustments)
    adjustment = numerator / max(denominator, 1e-9)
    adjustment = max(-max_adjustment, min(max_adjustment, adjustment))
    score = max(20.0, min(85.0, neutral_score + adjustment))
    return EmpiricalEstimate(
        score=round(score, 2),
        sample_size=total_samples,
        matched_groups=matched_groups,
        adjustment=round(adjustment, 2),
        conditional_adjustments=conditional_adjustments,
    )


def _aggregate_specs(query: EmpiricalQuery) -> list[_AggregateSpec]:
    return [
        _AggregateSpec(
            name="subtype_symbol_session_regime_style",
            filters={
                "setup_subtype": query.subtype,
                "symbol": query.symbol,
                "session": query.session,
                "regime": query.regime,
                "style": query.style,
            },
            specificity=1.00,
        ),
        _AggregateSpec(
            name="subtype_symbol_style",
            filters={"setup_subtype": query.subtype, "symbol": query.symbol, "style": query.style},
            specificity=0.86,
        ),
        _AggregateSpec(
            name="subtype_symbol",
            filters={"setup_subtype": query.subtype, "symbol": query.symbol},
            specificity=0.78,
        ),
        _AggregateSpec(
            name="subtype_session",
            filters={"setup_subtype": query.subtype, "session": query.session},
            specificity=0.66,
        ),
        _AggregateSpec(
            name="subtype_regime",
            filters={"setup_subtype": query.subtype, "regime": query.regime},
            specificity=0.62,
        ),
        _AggregateSpec(
            name="symbol_style",
            filters={"symbol": query.symbol, "style": query.style},
            specificity=0.54,
        ),
        _AggregateSpec(
            name="subtype",
            filters={"setup_subtype": query.subtype},
            specificity=0.48,
        ),
        _AggregateSpec(
            name="family_style",
            filters={"setup_family": query.family, "style": query.style},
            specificity=0.36,
        ),
        _AggregateSpec(
            name="family",
            filters={"setup_family": query.family},
            specificity=0.30,
        ),
    ]


def _score_from_r_values(values: list[float], neutral_score: float) -> float:
    win_rate = sum(1 for value in values if value > 0.0) / len(values)
    loss_rate = sum(1 for value in values if value < -0.1) / len(values)
    expectancy = mean(values)
    raw = neutral_score + expectancy * 14.0 + (win_rate - 0.5) * 30.0 - max(0.0, loss_rate - 0.45) * 12.0
    return max(20.0, min(85.0, raw))


def _matches(record: dict[str, object], filters: dict[str, str]) -> bool:
    return all(str(record.get(key, "")) == expected for key, expected in filters.items())


def _r_value(record: dict[str, object]) -> float | None:
    net_r = record.get("net_r")
    if isinstance(net_r, (int, float)):
        return float(net_r)
    outcome = str(record.get("outcome", ""))
    if outcome in {"win_clean", "win_messy"}:
        return 1.0
    if outcome == "partial_win":
        return 0.35
    if outcome == "breakeven":
        return 0.0
    if outcome in {"loss_clean", "loss_fast"}:
        return -1.0
    if outcome == "timeout":
        return -0.1
    return None
