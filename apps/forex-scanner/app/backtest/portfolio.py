"""Portfolio-level allocation for already generated historical trade candidates.

The single-symbol backtester can emit several trades whose lifetimes overlap. This
module replays those candidates chronologically and applies portfolio constraints
before computing headline metrics. It does not generate signals and never sends
orders.

The simulation assumes every accepted trade risks one equal R unit. Currency
exposure is represented in signed units: a long EUR/USD contributes +1 EUR and
-1 USD; a short contributes the inverse.
"""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import groupby
from typing import Literal

from app.backtest.metrics import calculate_metrics
from app.core.types import BacktestMetrics, DirectionBias, TradeRecord

PortfolioRejectionReason = Literal[
    "daily_loss_limit",
    "same_symbol_limit",
    "max_concurrent_positions",
    "currency_exposure_limit",
]


@dataclass(frozen=True)
class PortfolioConstraints:
    """Risk constraints applied to the chronological candidate stream."""

    max_concurrent_positions: int = 3
    max_same_symbol_positions: int = 1
    max_abs_currency_exposure: int | None = 2
    daily_loss_limit_r: float | None = 3.0

    def __post_init__(self) -> None:
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be at least 1")
        if self.max_same_symbol_positions < 1:
            raise ValueError("max_same_symbol_positions must be at least 1")
        if self.max_abs_currency_exposure is not None and self.max_abs_currency_exposure < 1:
            raise ValueError("max_abs_currency_exposure must be positive when enabled")
        if self.daily_loss_limit_r is not None and self.daily_loss_limit_r <= 0.0:
            raise ValueError("daily_loss_limit_r must be positive when enabled")


@dataclass(frozen=True)
class PortfolioRejection:
    """A candidate rejected using information available at its entry time."""

    trade: TradeRecord
    reason: PortfolioRejectionReason
    detail: str
    active_symbols: tuple[str, ...]
    currency_exposure: dict[str, int]
    realized_day_r: float


@dataclass(frozen=True)
class PortfolioSimulation:
    """Accepted portfolio path, rejections, and equal-risk performance metrics."""

    constraints: PortfolioConstraints
    accepted_trades: list[TradeRecord]
    rejected_trades: list[PortfolioRejection]
    metrics: BacktestMetrics
    equity_curve: list[tuple[datetime, float]]
    rejection_counts: dict[str, int] = field(default_factory=dict)


def simulate_portfolio(
    candidates: list[TradeRecord],
    constraints: PortfolioConstraints | None = None,
) -> PortfolioSimulation:
    """Allocate trade candidates subject to portfolio-level risk constraints.

    Candidates are processed by entry time. When several candidates share the same
    timestamp, higher ``final_score`` wins first; ties are deterministic. Positions
    whose exit time is equal to the new entry time are considered closed before the
    new decision.

    The daily loss lock uses only losses already realized by the candidate entry
    timestamp. It therefore does not leak future outcomes into an allocation
    decision.
    """

    rules = constraints or PortfolioConstraints()
    ordered = list(candidates)
    _validate_candidates(ordered)
    ordered.sort(key=lambda trade: (trade.entry_time, *_priority_key(trade)))

    accepted: list[TradeRecord] = []
    rejected: list[PortfolioRejection] = []
    active: list[TradeRecord] = []
    pending_exits: list[tuple[datetime, int, TradeRecord]] = []
    realized_by_day: dict[date, float] = defaultdict(float)
    sequence = 0

    for entry_time, timestamp_group in groupby(ordered, key=lambda trade: trade.entry_time):
        # Realize only positions known to be closed by this timestamp.
        while pending_exits and pending_exits[0][0] <= entry_time:
            _exit_time, _sequence, closed = heapq.heappop(pending_exits)
            realized_by_day[closed.exit_time.date()] += float(closed.net_r)
        active = [trade for trade in active if trade.exit_time > entry_time]

        group = sorted(timestamp_group, key=_priority_key)
        for trade in group:
            realized_day_r = float(realized_by_day.get(entry_time.date(), 0.0))
            exposure_before = currency_exposure(active)
            active_symbols = tuple(sorted(position.symbol for position in active))

            if (
                rules.daily_loss_limit_r is not None
                and realized_day_r <= -float(rules.daily_loss_limit_r)
            ):
                rejected.append(
                    _rejection(
                        trade,
                        "daily_loss_limit",
                        f"realized day P&L is {realized_day_r:.4f}R, at or below "
                        f"-{rules.daily_loss_limit_r:.4f}R",
                        active_symbols,
                        exposure_before,
                        realized_day_r,
                    )
                )
                continue

            same_symbol = sum(position.symbol == trade.symbol for position in active)
            if same_symbol >= rules.max_same_symbol_positions:
                rejected.append(
                    _rejection(
                        trade,
                        "same_symbol_limit",
                        f"{same_symbol} active {trade.symbol} position(s); limit is "
                        f"{rules.max_same_symbol_positions}",
                        active_symbols,
                        exposure_before,
                        realized_day_r,
                    )
                )
                continue

            if len(active) >= rules.max_concurrent_positions:
                rejected.append(
                    _rejection(
                        trade,
                        "max_concurrent_positions",
                        f"{len(active)} positions are active; limit is "
                        f"{rules.max_concurrent_positions}",
                        active_symbols,
                        exposure_before,
                        realized_day_r,
                    )
                )
                continue

            proposed_exposure = exposure_before.copy()
            for currency, units in trade_currency_exposure(trade).items():
                proposed_exposure[currency] = proposed_exposure.get(currency, 0) + units
            breached = _exposure_breaches(proposed_exposure, rules.max_abs_currency_exposure)
            if breached:
                detail = ", ".join(
                    f"{currency}={proposed_exposure[currency]:+d}" for currency in breached
                )
                rejected.append(
                    _rejection(
                        trade,
                        "currency_exposure_limit",
                        f"proposed exposure exceeds ±{rules.max_abs_currency_exposure}: {detail}",
                        active_symbols,
                        exposure_before,
                        realized_day_r,
                    )
                )
                continue

            accepted.append(trade)
            active.append(trade)
            sequence += 1
            heapq.heappush(pending_exits, (trade.exit_time, sequence, trade))

    accepted_by_exit = sorted(
        accepted,
        key=lambda trade: (trade.exit_time, trade.symbol, trade.entry_time),
    )
    equity_curve: list[tuple[datetime, float]] = []
    cumulative = 0.0
    for trade in accepted_by_exit:
        cumulative += float(trade.net_r)
        equity_curve.append((trade.exit_time, round(cumulative, 4)))

    counts = Counter(item.reason for item in rejected)
    return PortfolioSimulation(
        constraints=rules,
        accepted_trades=accepted,
        rejected_trades=rejected,
        metrics=calculate_metrics(accepted_by_exit),
        equity_curve=equity_curve,
        rejection_counts=dict(sorted(counts.items())),
    )


def trade_currency_exposure(trade: TradeRecord) -> dict[str, int]:
    """Return signed base/quote exposure units for one directional FX trade."""

    base, quote = split_symbol(trade.symbol)
    if trade.direction == DirectionBias.LONG:
        return {base: 1, quote: -1}
    if trade.direction == DirectionBias.SHORT:
        return {base: -1, quote: 1}
    raise ValueError(f"portfolio candidate {trade.symbol} has non-directional bias")


def currency_exposure(active_trades: list[TradeRecord]) -> dict[str, int]:
    """Aggregate signed currency units across active positions."""

    exposure: dict[str, int] = defaultdict(int)
    for trade in active_trades:
        for currency, units in trade_currency_exposure(trade).items():
            exposure[currency] += units
    return dict(sorted((currency, units) for currency, units in exposure.items() if units))


def split_symbol(symbol: str) -> tuple[str, str]:
    """Parse conventional six-character FX and metal symbols."""

    normalized = "".join(character for character in symbol.upper() if character.isalpha())
    if len(normalized) != 6:
        raise ValueError(f"cannot infer base/quote currencies from symbol {symbol!r}")
    return normalized[:3], normalized[3:]


def simulation_to_dict(result: PortfolioSimulation) -> dict[str, object]:
    """Serialize a portfolio simulation to a JSON-friendly payload."""

    return {
        "constraints": {
            "max_concurrent_positions": result.constraints.max_concurrent_positions,
            "max_same_symbol_positions": result.constraints.max_same_symbol_positions,
            "max_abs_currency_exposure": result.constraints.max_abs_currency_exposure,
            "daily_loss_limit_r": result.constraints.daily_loss_limit_r,
        },
        "summary": {
            "candidate_trades": len(result.accepted_trades) + len(result.rejected_trades),
            "accepted_trades": len(result.accepted_trades),
            "rejected_trades": len(result.rejected_trades),
            "rejection_counts": dict(result.rejection_counts),
            "metrics": result.metrics.model_dump(mode="json"),
        },
        "accepted_trades": [trade.model_dump(mode="json") for trade in result.accepted_trades],
        "rejections": [
            {
                "trade": rejection.trade.model_dump(mode="json"),
                "reason": rejection.reason,
                "detail": rejection.detail,
                "active_symbols": list(rejection.active_symbols),
                "currency_exposure": dict(rejection.currency_exposure),
                "realized_day_r": rejection.realized_day_r,
            }
            for rejection in result.rejected_trades
        ],
        "equity_curve": [
            [timestamp.isoformat(), value] for timestamp, value in result.equity_curve
        ],
    }


def _validate_candidates(candidates: list[TradeRecord]) -> None:
    awareness: bool | None = None
    for trade in candidates:
        if trade.exit_time < trade.entry_time:
            raise ValueError(
                f"trade {trade.symbol} exits before entry: "
                f"{trade.entry_time.isoformat()} -> {trade.exit_time.isoformat()}"
            )
        trade_currency_exposure(trade)
        current_awareness = trade.entry_time.tzinfo is not None
        if awareness is None:
            awareness = current_awareness
        elif awareness != current_awareness:
            raise ValueError("candidate datetimes must not mix timezone-aware and naive values")
        if (trade.exit_time.tzinfo is not None) != current_awareness:
            raise ValueError("each candidate entry and exit must use the same timezone awareness")


def _priority_key(trade: TradeRecord) -> tuple[float, float, str, datetime]:
    final_score = float(trade.final_score) if trade.final_score is not None else float("-inf")
    technical_score = (
        float(trade.technical_score) if trade.technical_score is not None else float("-inf")
    )
    return -final_score, -technical_score, trade.symbol, trade.exit_time


def _exposure_breaches(
    exposure: dict[str, int],
    limit: int | None,
) -> list[str]:
    if limit is None:
        return []
    return sorted(currency for currency, units in exposure.items() if abs(units) > limit)


def _rejection(
    trade: TradeRecord,
    reason: PortfolioRejectionReason,
    detail: str,
    active_symbols: tuple[str, ...],
    exposure: dict[str, int],
    realized_day_r: float,
) -> PortfolioRejection:
    return PortfolioRejection(
        trade=trade,
        reason=reason,
        detail=detail,
        active_symbols=active_symbols,
        currency_exposure=dict(exposure),
        realized_day_r=round(realized_day_r, 4),
    )
