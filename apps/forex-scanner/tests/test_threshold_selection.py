"""Tests for conservative in-sample score-threshold selection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.threshold_selection import (
    ABSTAIN_SCORE_THRESHOLD,
    ThresholdSelectionConfig,
    select_threshold,
)
from app.core.types import (
    DirectionBias,
    SetupFamily,
    SetupSubtype,
    TradeRecord,
    TradingStyle,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trade(net_r: float, score: float, index: int) -> TradeRecord:
    moment = BASE + timedelta(hours=index)
    return TradeRecord(
        symbol="EUR/USD",
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


def test_conservative_objective_prefers_stable_sample_over_small_volatile_mean() -> None:
    trades = [_trade(0.1, 70.0, index) for index in range(80)]
    volatile = [2.0, -1.0, 2.0, -1.0, 2.0, -1.0]
    trades += [_trade(value, 90.0, 100 + index) for index, value in enumerate(volatile)]
    selection = select_threshold(
        trades,
        ThresholdSelectionConfig(
            score_grid=(60.0, 85.0),
            min_trades=5,
            shrinkage_trades=20.0,
            confidence_z=1.645,
        ),
    )

    assert selection.status == "selected"
    assert selection.selected_threshold == 60.0
    assert selection.selected_candidate is not None
    high = next(candidate for candidate in selection.candidates if candidate.threshold == 85.0)
    assert high.raw_expectancy_r > selection.selected_candidate.raw_expectancy_r
    assert high.lower_confidence_bound_r < selection.selected_candidate.lower_confidence_bound_r


def test_abstains_when_every_eligible_threshold_has_non_positive_lcb() -> None:
    trades = [_trade(-0.2 if index % 2 else 0.1, 80.0, index) for index in range(60)]
    selection = select_threshold(
        trades,
        ThresholdSelectionConfig(score_grid=(0.0, 75.0), min_trades=30),
    )

    assert selection.status == "abstain"
    assert selection.selected_threshold == ABSTAIN_SCORE_THRESHOLD
    assert selection.selected_candidate is not None
    assert selection.selected_candidate.objective_r <= 0.0


def test_selects_large_positive_sample_with_positive_conservative_bound() -> None:
    trades = [_trade(-0.2, 50.0, index) for index in range(50)]
    trades += [_trade(0.25, 80.0, 100 + index) for index in range(100)]
    selection = select_threshold(
        trades,
        ThresholdSelectionConfig(score_grid=(0.0, 75.0), min_trades=50),
    )

    assert selection.status == "selected"
    assert selection.selected_threshold == 75.0
    assert selection.selected_candidate is not None
    assert selection.selected_candidate.lower_confidence_bound_r > 0.0


def test_no_eligible_sample_abstains_instead_of_choosing_lowest_threshold() -> None:
    trades = [_trade(1.0, 90.0, index) for index in range(10)]
    selection = select_threshold(
        trades,
        ThresholdSelectionConfig(score_grid=(0.0, 75.0), min_trades=50),
    )

    assert selection.status == "abstain"
    assert selection.selected_candidate is None
    assert "required minimum" in selection.reason


def test_mean_expectancy_mode_can_reproduce_legacy_style_selection() -> None:
    trades = [_trade(-1.0, 50.0, index) for index in range(6)]
    trades += [_trade(2.0, 80.0, 20 + index) for index in range(6)]
    selection = select_threshold(
        trades,
        ThresholdSelectionConfig(
            score_grid=(0.0, 60.0, 75.0),
            min_trades=5,
            objective="mean_expectancy",
            shrinkage_trades=0.0,
            confidence_z=0.0,
            allow_abstention=False,
        ),
    )

    assert selection.status == "selected"
    assert selection.selected_threshold == 75.0
    assert selection.selected_candidate is not None
    assert selection.selected_candidate.raw_expectancy_r == 2.0


def test_shrinkage_pulls_small_sample_expectancy_toward_zero() -> None:
    trades = [_trade(1.0, 80.0, index) for index in range(10)]
    selection = select_threshold(
        trades,
        ThresholdSelectionConfig(
            score_grid=(75.0,),
            min_trades=5,
            shrinkage_trades=40.0,
            confidence_z=0.0,
            minimum_objective_r=-1.0,
        ),
    )

    candidate = selection.selected_candidate
    assert candidate is not None
    assert candidate.shrinkage_weight == pytest.approx(0.2)
    assert candidate.shrunk_expectancy_r == pytest.approx(0.2)


def test_invalid_threshold_config_fails_loudly() -> None:
    with pytest.raises(ValueError, match="min_trades"):
        ThresholdSelectionConfig(score_grid=(0.0,), min_trades=1)
    with pytest.raises(ValueError, match="abstain_threshold"):
        ThresholdSelectionConfig(score_grid=(0.0, 75.0), abstain_threshold=75.0)
