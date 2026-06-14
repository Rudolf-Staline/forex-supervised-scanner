"""Entry activation (fill-on-touch) tests for the backtest trade simulator."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.backtest.engine import _simulate_trade
from app.core.types import (
    DirectionBias,
    MarketRegime,
    RiskPlan,
    SessionName,
    SetupFamily,
    SetupSubtype,
    TradingStyle,
)


def _risk_plan() -> RiskPlan:
    return RiskPlan(
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        tp1=1.1050,
        tp2=1.1100,
        tp3=1.1150,
        risk_reward=2.0,
        tp1_risk_reward=1.0,
        tp2_risk_reward=2.0,
        tp3_risk_reward=3.0,
        stop_method="atr",
        target_method="fixed_rr",
    )


def _future(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """Build a future frame from (high, low, close) tuples."""

    index = pd.date_range(datetime(2025, 1, 1, tzinfo=timezone.utc), periods=len(rows), freq="5min")
    highs = [row[0] for row in rows]
    lows = [row[1] for row in rows]
    closes = [row[2] for row in rows]
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": 100.0},
        index=index,
    )


def _simulate(
    future: pd.DataFrame,
    direction: DirectionBias = DirectionBias.LONG,
    *,
    cost_pips: float = 0.0,
    spread_price: float | None = None,
):
    entry_time = pd.Timestamp(datetime(2024, 12, 31, 23, 55, tzinfo=timezone.utc))
    return _simulate_trade(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        family=SetupFamily.TREND_CONTINUATION,
        subtype=SetupSubtype.EMA50_PULLBACK,
        direction=direction,
        entry_time=entry_time,
        risk_plan=_risk_plan(),
        future=future,
        cost_pips=cost_pips,
        session=SessionName.LONDON,
        regime=MarketRegime.TRENDING_UP,
        technical_score=70.0,
        execution_score=70.0,
        context_score=70.0,
        empirical_score=70.0,
        final_score=70.0,
        detected_patterns=[],
        pattern_score=0.0,
        spread_price=spread_price,
    )


def test_normal_fill_then_take_profit() -> None:
    # Bar 1 trades through entry (1.1000), bar 2 reaches take profit (1.1100).
    future = _future(
        [
            (1.1010, 1.0990, 1.1005),  # fills here (low<=entry<=high)
            (1.1120, 1.1005, 1.1110),  # take profit
        ]
    )
    trade = _simulate(future)
    assert trade is not None
    assert trade.exit_reason == "take_profit"
    assert trade.bars_to_activation == 1
    assert trade.net_r > 0.0


def test_fill_never_reached_is_excluded() -> None:
    # Price gaps above the entry and never trades back through it.
    future = _future(
        [
            (1.1090, 1.1030, 1.1080),
            (1.1140, 1.1060, 1.1120),
            (1.1200, 1.1100, 1.1180),
        ]
    )
    trade = _simulate(future)
    assert trade is None  # not triggered -> excluded from P&L


def test_fill_then_stop_loss() -> None:
    # Entry reached on bar 2, stop loss hit on bar 3.
    future = _future(
        [
            (1.1090, 1.1040, 1.1080),  # entry not yet touched (low>entry)
            (1.1020, 1.0995, 1.1005),  # fills here -> bars_to_activation == 2
            (1.1010, 1.0940, 1.0960),  # stop loss (1.0950) hit
        ]
    )
    trade = _simulate(future)
    assert trade is not None
    assert trade.bars_to_activation == 2
    assert trade.exit_reason == "stop_loss"
    assert trade.net_r < 0.0


def test_fill_then_take_profit_delayed_activation() -> None:
    future = _future(
        [
            (1.1085, 1.1030, 1.1075),  # not touched
            (1.1005, 1.0992, 1.1000),  # fills here
            (1.1130, 1.1010, 1.1120),  # take profit
        ]
    )
    trade = _simulate(future)
    assert trade is not None
    assert trade.bars_to_activation == 2
    assert trade.exit_reason == "take_profit"
    assert trade.net_r > 0.0


def test_data_spread_is_used_as_round_trip_cost() -> None:
    # EUR/USD pip size is 0.0001; a 0.00020 spread == 2.0 pips of round-trip cost.
    future = _future(
        [
            (1.1010, 1.0990, 1.1005),  # fills
            (1.1120, 1.1005, 1.1110),  # take profit at 1.1100
        ]
    )
    with_spread = _simulate(future, spread_price=0.00020)
    no_cost = _simulate(future, spread_price=None, cost_pips=0.0)
    assert with_spread is not None and no_cost is not None
    # Risk distance is 0.0050; a 0.00020 cost reduces net R by 0.00020/0.0050 = 0.04.
    assert with_spread.cost_pips == 2.0
    assert abs((no_cost.net_r - with_spread.net_r) - 0.04) < 1e-6


def test_fixed_pip_cost_used_when_spread_absent() -> None:
    future = _future(
        [
            (1.1010, 1.0990, 1.1005),
            (1.1120, 1.1005, 1.1110),
        ]
    )
    trade = _simulate(future, spread_price=None, cost_pips=1.5)
    assert trade is not None
    assert trade.cost_pips == 1.5  # falls back to the configured fixed pip cost


def test_same_bar_fill_and_stop_prefers_stop() -> None:
    # The activation bar straddles entry and also reaches the stop: SL wins (conservative).
    future = _future(
        [
            (1.1005, 1.0940, 1.0960),  # fills (1.1000) and hits stop (1.0950) same bar
            (1.1100, 1.1000, 1.1090),
        ]
    )
    trade = _simulate(future)
    assert trade is not None
    assert trade.bars_to_activation == 1
    assert trade.exit_reason == "stop_loss"
