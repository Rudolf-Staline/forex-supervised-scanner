"""Tests for the reusable quote-aware historical execution simulator."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.backtest.execution import simulate_execution
from app.core.types import DirectionBias, RiskPlan


def _risk_plan(
    *,
    entry: float = 1.1000,
    stop_loss: float = 1.0950,
    take_profit: float = 1.1100,
) -> RiskPlan:
    return RiskPlan(
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        tp1=1.1050,
        tp2=take_profit,
        tp3=1.1150,
        risk_reward=2.0,
        tp1_risk_reward=1.0,
        tp2_risk_reward=2.0,
        tp3_risk_reward=3.0,
        stop_method="atr",
        target_method="fixed_rr",
    )


def _index(rows: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=rows, freq="5min", tz="UTC")


def _legacy(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "high": [row[0] for row in rows],
            "low": [row[1] for row in rows],
            "close": [row[2] for row in rows],
            "volume": 100.0,
        },
        index=_index(len(rows)),
    )


def _quotes(
    rows: list[
        tuple[
            float,
            float,
            float,
            float,
            float,
            float,
        ]
    ],
) -> pd.DataFrame:
    """Build rows as bid_high,bid_low,bid_close,ask_high,ask_low,ask_close."""

    return pd.DataFrame(
        {
            "bid_high": [row[0] for row in rows],
            "bid_low": [row[1] for row in rows],
            "bid_close": [row[2] for row in rows],
            "ask_high": [row[3] for row in rows],
            "ask_low": [row[4] for row in rows],
            "ask_close": [row[5] for row in rows],
            "volume": 100.0,
        },
        index=_index(len(rows)),
    )


def _simulate(
    future: pd.DataFrame,
    *,
    direction: DirectionBias = DirectionBias.LONG,
    plan: RiskPlan | None = None,
    spread_price: float | None = None,
):
    return simulate_execution(
        symbol="EUR/USD",
        direction=direction,
        risk_plan=plan or _risk_plan(),
        future=future,
        signal_time=datetime(2025, 12, 31, 23, 55, tzinfo=timezone.utc),
        cost_pips=1.5,
        spread_price=spread_price,
    )


def test_legacy_model_preserves_fill_stop_first_and_spread_deduction() -> None:
    future = _legacy(
        [
            (1.1005, 1.0940, 1.0960),
            (1.1120, 1.1000, 1.1110),
        ]
    )
    result = _simulate(future, spread_price=0.0002)

    assert result is not None
    assert result.execution_model == "single_price_plus_spread"
    assert result.exit_reason == "stop_loss"
    assert result.intrabar_ambiguous is True
    assert result.cost_pips == pytest.approx(2.0)
    assert result.gross_r == pytest.approx(-1.0)
    assert result.net_r == pytest.approx(-1.04)


def test_long_entry_uses_ask_not_bid() -> None:
    future = _quotes(
        [
            # Bid crosses 1.1000, but ask remains above it: a long cannot fill.
            (1.1002, 1.0998, 1.1000, 1.1004, 1.1001, 1.1002),
            (1.1010, 1.1003, 1.1008, 1.1012, 1.1005, 1.1010),
        ]
    )
    assert _simulate(future) is None


def test_long_target_uses_bid_not_ask() -> None:
    plan = _risk_plan(entry=1.1002, stop_loss=1.0952, take_profit=1.1100)
    future = _quotes(
        [
            # Ask activates the long.
            (1.1001, 1.0998, 1.1000, 1.1004, 1.1000, 1.1002),
            # Ask reaches the target, bid does not. The position must remain open.
            (1.1099, 1.1080, 1.1090, 1.1102, 1.1083, 1.1093),
        ]
    )
    result = _simulate(future, plan=plan)

    assert result is not None
    assert result.execution_model == "bid_ask_ohlc"
    assert result.exit_reason == "time_exit"
    assert result.exit_price == pytest.approx(1.1090)


def test_short_stop_uses_ask_not_bid() -> None:
    plan = _risk_plan(entry=1.1000, stop_loss=1.1050, take_profit=1.0900)
    future = _quotes(
        [
            # Bid activates the short.
            (1.1002, 1.0997, 1.1000, 1.1004, 1.0999, 1.1002),
            # Bid remains below stop, ask crosses it: short is stopped.
            (1.1049, 1.1020, 1.1040, 1.1052, 1.1023, 1.1043),
        ]
    )
    result = _simulate(future, direction=DirectionBias.SHORT, plan=plan)

    assert result is not None
    assert result.exit_reason == "stop_loss"
    assert result.exit_price == pytest.approx(1.1050)
    assert result.net_r == pytest.approx(-1.0)


def test_bid_ask_model_embeds_cost_in_executable_prices() -> None:
    plan = _risk_plan(entry=1.1002, stop_loss=1.0952, take_profit=1.1100)
    future = _quotes(
        [
            (1.1001, 1.0998, 1.1000, 1.1004, 1.1000, 1.1002),
            (1.1102, 1.1080, 1.1100, 1.1104, 1.1082, 1.1102),
        ]
    )
    result = _simulate(future, plan=plan)

    assert result is not None
    assert result.exit_reason == "take_profit"
    # Executable P&L: bid exit 1.1100 - ask entry 1.1002 = 0.0098.
    assert result.net_r == pytest.approx(0.0098 / 0.0050)
    assert result.gross_r > result.net_r
    assert result.cost_price > 0.0
    assert result.path_frame["high"].iloc[-1] == pytest.approx(1.1102)


def test_partial_bid_ask_schema_fails_loudly() -> None:
    future = _legacy([(1.1010, 1.0990, 1.1005)])
    future["bid_high"] = future["high"]
    with pytest.raises(ValueError, match="incomplete bid/ask OHLC schema"):
        _simulate(future)
