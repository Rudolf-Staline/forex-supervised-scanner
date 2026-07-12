"""Reusable quote-aware trade execution simulation for historical backtests.

Three deterministic data models are supported:

* explicit bid/ask OHLC columns;
* bid OHLC plus a per-bar spread column, from which ask prices are derived;
* legacy single-price OHLC plus one round-trip spread deduction.

No order is sent. This module is paper/backtest only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from app.core.types import DirectionBias, RiskPlan
from app.data.validation import pips_to_price, price_to_pips

ExitReason = Literal["take_profit", "stop_loss", "time_exit", "end_of_data"]
ExecutionModel = Literal[
    "bid_ask_ohlc",
    "bid_ohlc_plus_spread",
    "single_price_plus_spread",
]

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


@dataclass(frozen=True)
class _QuoteAdapter:
    """Expose executable bid/ask ranges from either explicit or derived quotes."""

    model: Literal["bid_ask_ohlc", "bid_ohlc_plus_spread"]
    fallback_spread: float = 0.0

    def spread(self, row: pd.Series) -> float:
        if self.model == "bid_ask_ohlc":
            return max(0.0, float(row["ask_close"]) - float(row["bid_close"]))
        value = row.get("spread")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = float("nan")
        if pd.isna(numeric) or numeric <= 0.0:
            return max(0.0, self.fallback_spread)
        return numeric

    def entry_range(self, row: pd.Series, direction: DirectionBias) -> tuple[float, float]:
        if self.model == "bid_ask_ohlc":
            prefix = "ask" if direction == DirectionBias.LONG else "bid"
            return float(row[f"{prefix}_low"]), float(row[f"{prefix}_high"])
        spread = self.spread(row)
        if direction == DirectionBias.LONG:
            return float(row["low"]) + spread, float(row["high"]) + spread
        return float(row["low"]), float(row["high"])

    def exit_range(self, row: pd.Series, direction: DirectionBias) -> tuple[float, float]:
        if self.model == "bid_ask_ohlc":
            prefix = "bid" if direction == DirectionBias.LONG else "ask"
            return float(row[f"{prefix}_low"]), float(row[f"{prefix}_high"])
        spread = self.spread(row)
        if direction == DirectionBias.LONG:
            return float(row["low"]), float(row["high"])
        return float(row["low"]) + spread, float(row["high"]) + spread

    def exit_close(self, row: pd.Series, direction: DirectionBias) -> float:
        if self.model == "bid_ask_ohlc":
            prefix = "bid" if direction == DirectionBias.LONG else "ask"
            return float(row[f"{prefix}_close"])
        close = float(row["close"])
        return close if direction == DirectionBias.LONG else close + self.spread(row)

    def path_frame(self, frame: pd.DataFrame, direction: DirectionBias) -> pd.DataFrame:
        if self.model == "bid_ask_ohlc":
            prefix = "bid" if direction == DirectionBias.LONG else "ask"
            path = pd.DataFrame(
                {
                    "open": frame[f"{prefix}_close"],
                    "high": frame[f"{prefix}_high"],
                    "low": frame[f"{prefix}_low"],
                    "close": frame[f"{prefix}_close"],
                },
                index=frame.index,
            )
        else:
            path = _base_path(frame)
            if direction == DirectionBias.SHORT:
                spreads = frame.apply(self.spread, axis=1)
                for column in ("open", "high", "low", "close"):
                    path[column] = path[column].astype(float) + spreads
        if "volume" in frame.columns:
            path["volume"] = frame["volume"]
        return path


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

    ``risk_plan.entry`` is the executable resting-order level. Long entries use
    ask and long exits use bid; short entries use bid and short exits use ask.

    When only single-price OHLC is available, the historical model is preserved:
    fills and exits use that price path and one full spread is deducted.

    Stop wins when a candle contains both stop and target. Any terminal event on
    the activation candle is flagged as intrabar-ambiguous because OHLC does not
    reveal the event order.
    """

    if direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
        raise ValueError("execution requires a long or short direction")
    if future.empty:
        return None

    quote_columns = _BID_ASK_COLUMNS.intersection(future.columns)
    if quote_columns and quote_columns != _BID_ASK_COLUMNS:
        missing = sorted(_BID_ASK_COLUMNS - quote_columns)
        raise ValueError(
            "incomplete bid/ask OHLC schema; missing columns: " + ", ".join(missing)
        )

    fallback_spread = (
        float(spread_price)
        if spread_price is not None and spread_price > 0.0
        else pips_to_price(symbol, cost_pips)
    )
    if quote_columns == _BID_ASK_COLUMNS:
        return _simulate_quote_path(
            symbol=symbol,
            direction=direction,
            risk_plan=risk_plan,
            future=future,
            signal_time=signal_time,
            adapter=_QuoteAdapter("bid_ask_ohlc"),
        )

    if _has_usable_bar_spread(future):
        return _simulate_quote_path(
            symbol=symbol,
            direction=direction,
            risk_plan=risk_plan,
            future=future,
            signal_time=signal_time,
            adapter=_QuoteAdapter("bid_ohlc_plus_spread", fallback_spread),
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


def _simulate_quote_path(
    *,
    symbol: str,
    direction: DirectionBias,
    risk_plan: RiskPlan,
    future: pd.DataFrame,
    signal_time: pd.Timestamp | datetime,
    adapter: _QuoteAdapter,
) -> ExecutionSimulation | None:
    entry_level = float(risk_plan.entry)
    activation_index: int | None = None
    for position, (_timestamp, row) in enumerate(future.iterrows()):
        low, high = adapter.entry_range(row, direction)
        if low <= entry_level <= high:
            activation_index = position
            break
    if activation_index is None:
        return None

    active_future = future.iloc[activation_index:]
    exit_reason: ExitReason = "end_of_data"
    exit_price = entry_level
    exit_time = _as_datetime(signal_time)
    exit_bar_count = 0
    exit_position = len(active_future) - 1
    intrabar_ambiguous = False

    for position, (timestamp, row) in enumerate(active_future.iterrows()):
        bar_number = position + 1
        low, high = adapter.exit_range(row, direction)
        stop_hit, target_hit = _terminal_hits(direction, low, high, risk_plan)
        if (bar_number == 1 and (stop_hit or target_hit)) or (stop_hit and target_hit):
            intrabar_ambiguous = True
        if stop_hit:
            exit_reason = "stop_loss"
            exit_price = float(risk_plan.stop_loss)
        elif target_hit:
            exit_reason = "take_profit"
            exit_price = float(risk_plan.take_profit)
        else:
            continue
        exit_time = _as_datetime(timestamp)
        exit_bar_count = bar_number
        exit_position = position
        break
    else:
        last = active_future.iloc[-1]
        exit_price = adapter.exit_close(last, direction)
        exit_time = _as_datetime(active_future.index[-1])
        exit_reason = "time_exit"
        exit_bar_count = len(active_future)

    risk_distance = abs(entry_level - float(risk_plan.stop_loss))
    net_profit = _directional_profit(direction, entry_level, exit_price)
    entry_spread = adapter.spread(active_future.iloc[0])
    exit_spread = adapter.spread(active_future.iloc[exit_position])
    cost_price = max(0.0, 0.5 * (entry_spread + exit_spread))
    gross_profit = net_profit + cost_price

    path_source = active_future.head(exit_bar_count) if exit_bar_count else active_future
    return ExecutionSimulation(
        entry_price=entry_level,
        exit_price=exit_price,
        exit_time=exit_time,
        exit_reason=exit_reason,
        bars_to_activation=activation_index + 1,
        exit_bar_count=exit_bar_count,
        gross_r=gross_profit / max(risk_distance, 1e-12),
        net_r=net_profit / max(risk_distance, 1e-12),
        cost_price=cost_price,
        cost_pips=round(price_to_pips(symbol, cost_price), 4),
        execution_model=adapter.model,
        intrabar_ambiguous=intrabar_ambiguous,
        path_frame=adapter.path_frame(path_source, direction),
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
    activation_index = _find_single_price_activation(future, entry_level)
    if activation_index is None:
        return None

    active_future = future.iloc[activation_index:]
    exit_reason: ExitReason = "end_of_data"
    exit_price = entry_level
    exit_time = _as_datetime(signal_time)
    exit_bar_count = 0
    intrabar_ambiguous = False

    for position, (timestamp, row) in enumerate(active_future.iterrows()):
        bar_number = position + 1
        stop_hit, target_hit = _terminal_hits(
            direction,
            float(row["low"]),
            float(row["high"]),
            risk_plan,
        )
        if (bar_number == 1 and (stop_hit or target_hit)) or (stop_hit and target_hit):
            intrabar_ambiguous = True
        if stop_hit:
            exit_reason = "stop_loss"
            exit_price = float(risk_plan.stop_loss)
        elif target_hit:
            exit_reason = "take_profit"
            exit_price = float(risk_plan.take_profit)
        else:
            continue
        exit_time = _as_datetime(timestamp)
        exit_bar_count = bar_number
        break
    else:
        exit_price = float(active_future.iloc[-1]["close"])
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

    path_source = active_future.head(exit_bar_count) if exit_bar_count else active_future
    return ExecutionSimulation(
        entry_price=entry_level,
        exit_price=exit_price,
        exit_time=exit_time,
        exit_reason=exit_reason,
        bars_to_activation=activation_index + 1,
        exit_bar_count=exit_bar_count,
        gross_r=gross_profit / max(risk_distance, 1e-12),
        net_r=(gross_profit - cost_price) / max(risk_distance, 1e-12),
        cost_price=cost_price,
        cost_pips=effective_cost_pips,
        execution_model="single_price_plus_spread",
        intrabar_ambiguous=intrabar_ambiguous,
        path_frame=path_source,
    )


def _has_usable_bar_spread(frame: pd.DataFrame) -> bool:
    if "spread" not in frame.columns:
        return False
    spread = pd.to_numeric(frame["spread"], errors="coerce").fillna(0.0)
    return bool((spread > 0.0).any())


def _find_single_price_activation(future: pd.DataFrame, entry_level: float) -> int | None:
    for position, (_timestamp, row) in enumerate(future.iterrows()):
        if float(row["low"]) <= entry_level <= float(row["high"]):
            return position
    return None


def _terminal_hits(
    direction: DirectionBias,
    low: float,
    high: float,
    risk_plan: RiskPlan,
) -> tuple[bool, bool]:
    if direction == DirectionBias.LONG:
        return low <= float(risk_plan.stop_loss), high >= float(risk_plan.take_profit)
    return high >= float(risk_plan.stop_loss), low <= float(risk_plan.take_profit)


def _directional_profit(direction: DirectionBias, entry_price: float, exit_price: float) -> float:
    return exit_price - entry_price if direction == DirectionBias.LONG else entry_price - exit_price


def _base_path(frame: pd.DataFrame) -> pd.DataFrame:
    if "open" in frame.columns:
        return frame[["open", "high", "low", "close"]].copy()
    return pd.DataFrame(
        {
            "open": frame["close"],
            "high": frame["high"],
            "low": frame["low"],
            "close": frame["close"],
        },
        index=frame.index,
    )


def _as_datetime(value: pd.Timestamp | datetime) -> datetime:
    return value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
