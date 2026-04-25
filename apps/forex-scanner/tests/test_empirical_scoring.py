"""Empirical shrinkage and fallback hierarchy tests."""

from __future__ import annotations

from app.scoring.empirical import EmpiricalQuery, estimate_empirical_score


def _query() -> EmpiricalQuery:
    return EmpiricalQuery(
        symbol="EUR/USD",
        style="day_trading",
        family="trend_continuation",
        subtype="shallow_ema20_pullback",
        session="london",
        regime="trending up",
    )


def test_empirical_score_uses_context_sensitive_fallback_hierarchy() -> None:
    records = [
        {
            "symbol": "EUR/USD",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": 1.0,
        }
        for _ in range(12)
    ]
    records.extend(
        {
            "symbol": "GBP/USD",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": -1.0,
        }
        for _ in range(12)
    )

    estimate = estimate_empirical_score(
        records=records,
        query=_query(),
        neutral_score=55.0,
        minimum_samples=20,
        min_condition_samples=4,
        shrinkage_samples=20,
        max_adjustment=18.0,
    )

    assert estimate.score > 55.0
    assert any(group.startswith("subtype_symbol") for group in estimate.matched_groups)
    assert estimate.conditional_adjustments["subtype_symbol"] > 0.0


def test_sparse_samples_are_shrunk_toward_neutral() -> None:
    records = [
        {
            "symbol": "EUR/USD",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": 4.0,
        }
    ]

    estimate = estimate_empirical_score(
        records=records,
        query=_query(),
        neutral_score=55.0,
        minimum_samples=20,
        min_condition_samples=1,
        shrinkage_samples=36,
        max_adjustment=18.0,
    )

    assert 55.0 < estimate.score < 58.0
    assert estimate.conditional_adjustments["subtype_symbol"] < 3.0


def test_conditional_underperformance_penalizes_empirical_score() -> None:
    records = [
        {
            "symbol": "EUR/USD",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": -1.0,
        }
        for _ in range(18)
    ]
    records.extend(
        {
            "symbol": "USD/JPY",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": 1.0,
        }
        for _ in range(18)
    )

    estimate = estimate_empirical_score(
        records=records,
        query=_query(),
        neutral_score=55.0,
        minimum_samples=20,
        min_condition_samples=4,
        shrinkage_samples=20,
        max_adjustment=18.0,
    )

    assert estimate.score < 55.0
    assert estimate.conditional_adjustments["subtype_symbol"] < 0.0


def test_empirical_score_backs_off_to_broader_aggregates_when_specific_samples_are_sparse() -> None:
    records = [
        {
            "symbol": "USD/JPY",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": 0.6,
        }
        for _ in range(24)
    ]

    estimate = estimate_empirical_score(
        records=records,
        query=_query(),
        neutral_score=55.0,
        minimum_samples=20,
        min_condition_samples=4,
        shrinkage_samples=20,
        max_adjustment=18.0,
    )

    assert estimate.score > 55.0
    assert any(group.startswith("subtype_session") for group in estimate.matched_groups)
    assert not any(group.startswith("subtype_symbol") for group in estimate.matched_groups)
