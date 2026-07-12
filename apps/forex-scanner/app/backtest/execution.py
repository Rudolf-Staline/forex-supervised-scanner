"""Reusable quote-aware trade execution simulation for historical backtests.

The simulator supports two data models:

* explicit bid/ask OHLC columns, where fills and exits are evaluated on the
  executable quote side and spread friction is embedded in the realized P&L;
* legacy single-price OHLC candles plus a spread estimate, preserving the
  historical backtester behaviour.

No order is sent. This module is deterministic and paper/backtest only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from app.core.types import DirectionBias, RiskPlan
from app.data.validation import pips_to_price, price_to_pips

ExitReason = Literal["take_profit", "stop_loss", "time_exit", "end_of_data"]
ExecutionModel = Literal["bid_ask_ohlc", "single_price_plus_spread"]

_BID_ASK_COLUMNS = {
    "bid_high",
    "bid_low",
    "bid_close",
    "ask_high",
    "ask_low",
    "ask_close",
}


@dataclass(frozen=True)
class ExecutionSimulation:
    """Deterministic result of one historical order lifecycle."""

    entry_price: float
    exit_price: float
    exit_time: datetime
    exit_reason: ExitReason
    bars_to_activation: int
    exit_bar_count: int
    gross_r: float
    net_r: float
    cost_price: float
    cost_pips: float
    execution_model: ExecutionModel
    intrabar_ambiguous: bool
    path_frame: pd.DataFrame


def simulate_execution(
    *,
    symbol: str,
    direction: DirectionBias,
    risk_plan: RiskPlan,
    future: pd.DataFrame,
    signal_time: pd.Timestamp | datetime,
    cost_pips: float,
    spread_price: float | None = None,
) -> ExecutionSimulation | None:
    """Simulate activation and exit on future candles.

    ``risk_plan.entry`` is treated as the executable resting-order level. With
    explicit quote candles, long entries are tested against ask OHLC and long
    exits against bid OHLC; short entries use bid and short exits use ask.

    If explicit bid/ask columns are absent, the legacy single-price path is
    preserved and one full spread is subtracted as a round-trip cost.

    A bar that contains both stop and target is resolved stop-first. A terminal
    event on the activation bar is marked ``intrabar_ambiguous`` because OHLC
    candles do not reveal whether activation occurred before that event.
    """

    if direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
        raise ValueError("execution requires a long or short direction")
    if future.empty:
        return None

    available_quote_columns = _BID_ASK_COLUMNS.intersection(future.columns)
    if available_quote_columns and available_quote_columns != _BID_ASK_COLUMNS:
        missing = sorted(_BID_ASK_COLUMNS - available_quote_columns)
        raise ValueError(
            "incomplete bid/ask OHLC schema; missing columns: " + ", ".join(missing)
        )

    if available_quote_columns == _BID_ASK_COLUMNS:
        return _simulate_bid_ask(
            symbol=symbol,
            direction=direction,
            risk_plan=risk_plan,
            future=future,
            signal_time=signal_time,
        )
    return _simulate_single_price(
        symbol=symbol,
        direction=direction,
        risk_plan=risk_plan,
        future=future,
        signal_time=signal_time,
        cost_pips=cost_pips,
        spread_price=spread_price,
    )


def _simulate_bid_ask(
    *,
    symbol: str,
    direction: DirectionBias,
    risk_plan: RiskPlan,
    future: pd.DataFrame,
    signal_time: pd.Timestamp | datetime,
) -> ExecutionSimulation | None:
    entry_side = "ask" if direction == DirectionBias.LONG else "bid"
    exit_side = "bid" if direction == DirectionBias.LONG else "ask"
    entry_level = float(risk_plan.entry)

    activation_index = _find_activation(future, entry_level, prefix=entry_side)
    if activation_index is None:
        return None

    bars_to_activation = activation_index + 1
    active_future = future.iloc[activation_index:]
    exit_reason: ExitReason = "end_of_data"
    exit_price = entry_level
    exit_time = _as_datetime(signal_time)
    exit_bar_count = 0
    intrabar_ambiguous = False
    exit_position = len(active_future) - 1

    for position, (timestamp, row) in enumerate(active_future.iterrows()):
        bar_number = position + 1
        high = float(row[f"{exit_side}_high"])
        low = float(row[f"{exit_side}_low"])
        stop_hit, target_hit = _terminal_hits(
            direction=direction,
            low=low,
            high=high,
            risk_plan=risk_plan,
        )
        if (bar_number == 1 and (stop_hit or target_hit)) or (stop_hit and target_hit):
            intrabar_ambiguous = True
        if stop_hit:
            exit_reason = "stop_loss"
            exit_price = float(risk_plan.stop_loss)
            exit_time = _as_datetime(timestamp)
            exit_bar_count = bar_number
            exit_position = position
            break
        if target_hit:
            exit_reason = "take_profit"
            exit_price = float(risk_plan.take_profit)
            exit_time = _as_datetime(timestamp)
            exit_bar_count = bar_number
            exit_position = position
            break
    else:
        if not active_future.empty:
            last = active_future.iloc[-1]
            exit_price = float(last[f"{exit_side}_close"])
            exit_time = _as_datetime(active_future.index[-1])
            exit_reason = "time_exit"
            exit_bar_count = len(active_future)

    entry_price = entry_level
    risk_distance = abs(entry_price - float(risk_plan.stop_loss))
    net_profit = _directional_profit(direction, entry_price, exit_price)

    activation_row = active_future.iloc[0]
    exit_row = active_future.iloc[exit_position]
    entry_spread = _row_spread(activation_row)
    exit_spread = _row_spread(exit_row)
    cost_price = max(0.0, 0.5 * (entry_spread + exit_spread))
    gross_profit = net_profit + cost_price

    path_source = active_future.head(exit_bar_count) if exit_bar_count else active_future
    path_frame = _liquidation_path(path_source, exit_side)

    return ExecutionSimulation(
        entry_price=entry_price,
        exit_price=exit_price,
        exit_time=exit_time,
        exit_reason=exit_reason,
        bars_to_activation=bars_to_activation,
        exit_bar_count=exit_bar_count,
        gross_r=gross_profit / max(risk_distance, 1e-12),
        net_r=net_profit / max(risk_distance, 1e-12),
        cost_price=cost_price,
        cost_pips=round(price_to_pips(symbol, cost_price), 4),
        execution_model="bid_ask_ohlc",
        intrabar_ambiguous=intrabar_ambiguous,
        path_frame=path_frame,
    )


def _simulate_single_price(
    *,
    symbol: str,
    direction: DirectionBias,
    risk_plan: RiskPlan,
    future: pd.DataFrame,
    signal_time: pd.Timestamp | datetime,
    cost_pips: float,
    spread_price: float | None,
) -> ExecutionSimulation | None:
    required = {"high", "low", "close"}
    missing = sorted(required - set(future.columns))
    if missing:
        raise ValueError("single-price OHLC schema is missing: " + ", ".join(missing))

    entry_level = float(risk_plan.entry)
    activation_index = _find_activation(future, entry_level, prefix=None)
    if activation_index is None:
        return None

    bars_to_activation = activation_index + 1
    active_future = future.iloc[activation_index:]
    exit_reason: ExitReason = "end_of_data"
    exit_price = entry_level
    exit_time = _as_datetime(signal_time)
    exit_bar_count = 0
    intrabar_ambiguous = False

    for position, (timestamp, row) in enumerate(active_future.iterrows()):
        bar_number = position + 1
        high = float(row["high"])
        low = float(row["low"])
        stop_hit, target_hit = _terminal_hits(
            direction=direction,
            low=low,
            high=high,
            risk_plan=risk_plan,
        )
        if (bar_number == 1 and (stop_hit or target_hit)) or (stop_hit and target_hit):
            intrabar_ambiguous = True
        if stop_hit:
            exit_reason = "stop_loss"
            exit_price = float(risk_plan.stop_loss)
            exit_time = _as_datetime(timestamp)
            exit_bar_count = bar_number
            break
        if target_hit:
            exit_reason = "take_profit"
            exit_price = float(risk_plan.take_profit)
            exit_time = _as_datetime(timestamp)
            exit_bar_count = bar_number
            break
    else:
        if not active_future.empty:
            last = active_future.iloc[-1]
            exit_price = float(last["close"])
            exit_time = _as_datetime(active_future.index[-1])
            exit_reason = "time_exit"
            exit_bar_count = len(active_future)

    risk_distance = abs(entry_level - float(risk_plan.stop_loss))
    gross_profit = _directional_profit(direction, entry_level, exit_price)
    if spread_price is not None and spread_price > 0.0:
        cost_price = float(spread_price)
        effective_cost_pips = round(price_to_pips(symbol, cost_price), 4)
    else:
        cost_price = pips_to_price(symbol, cost_pips)
        effective_cost_pips = float(cost_pips)

    path_frame = active_future.head(exit_bar_count) if exit_bar_count else active_future
    return ExecutionSimulation(
        entry_price=entry_level,
        exit_price=exit_price,
        exit_time=exit_time,
        exit_reason=exit_reason,
        bars_to_activation=bars_to_activation,
        exit_bar_count=exit_bar_count,
        gross_r=gross_profit / max(risk_distance, 1e-12),
        net_r=(gross_profit - cost_price) / max(risk_distance, 1e-12),
        cost_price=cost_price,
        cost_pips=effective_cost_pips,
        execution_model="single_price_plus_spread",
        intrabar_ambiguous=intrabar_ambiguous,
        path_frame=path_frame,
    )


def _find_activation(
    future: pd.DataFrame,
    entry_level: float,
    *,
    prefix: str | None,
) -> int | None:
    high_column = f"{prefix}_high" if prefix else "high"
    low_column = f"{prefix}_low" if prefix else "low"
    for position, (_timestamp, row) in enumerate(future.iterrows()):
        low = float(row[low_column])
        high = float(row[high_column])
        if low <= entry_level <= high:
            return position
    return None


def _terminal_hits(
    *,
    direction: DirectionBias,
    low: float,
    high: float,
    risk_plan: RiskPlan,
) -> tuple[bool, bool]:
    if direction == DirectionBias.LONG:
        return low <= float(risk_plan.stop_loss), high >= float(risk_plan.take_profit)
    return high >= float(risk_plan.stop_loss), low <= float(risk_plan.take_profit)


def _directional_profit(direction: DirectionBias, entry_price: float, exit_price: float) -> float:
    if direction == DirectionBias.LONG:
        return exit_price - entry_price
    return entry_price - exit_price


def _row_spread(row: pd.Series) -> float:
    ask = float(row["ask_close"])
    bid = float(row["bid_close"])
    return max(0.0, ask - bid)


def _liquidation_path(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    path = pd.DataFrame(
        {
            "open": frame[f"{prefix}_close"],
            "high": frame[f"{prefix}_high"],
            "low": frame[f"{prefix}_low"],
            "close": frame[f"{prefix}_close"],
        },
        index=frame.index,
    )
    if "volume" in frame.columns:
        path["volume"] = frame["volume"]
    return path


def _as_datetime(value: pd.Timestamp | datetime) -> datetime:
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    return value
