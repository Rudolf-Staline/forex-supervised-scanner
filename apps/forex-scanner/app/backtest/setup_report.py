"""Per-setup reporting helpers for backtest trade records."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.backtest.metrics import calculate_metrics
from app.core.types import SetupSubtype, TradeRecord


@dataclass(frozen=True)
class SetupBacktestSummary:
    """Aggregated backtest metrics for one setup subtype."""

    setup: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    average_R: float
    profit_factor: float
    max_drawdown: float
    expectancy: float
    best_symbol: str | None
    worst_symbol: str | None


def summarize_setups(
    trades: list[TradeRecord],
    *,
    setup_filter: str | None = None,
    min_score: float | None = None,
) -> list[SetupBacktestSummary]:
    """Return per-setup summaries from completed backtest trades."""

    filtered = [
        trade
        for trade in trades
        if _matches_setup(trade, setup_filter)
        and (min_score is None or trade.final_score is None or trade.final_score >= min_score)
    ]
    grouped: dict[str, list[TradeRecord]] = defaultdict(list)
    for trade in filtered:
        grouped[trade.setup_subtype.value].append(trade)
    return [
        _summary_for_setup(setup, setup_trades)
        for setup, setup_trades in sorted(grouped.items())
    ]


def export_setup_summaries_csv(summaries: list[SetupBacktestSummary], output: Path) -> Path:
    """Write setup summaries to CSV and return the path."""

    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "setup",
        "total_trades",
        "wins",
        "losses",
        "win_rate",
        "average_R",
        "profit_factor",
        "max_drawdown",
        "expectancy",
        "best_symbol",
        "worst_symbol",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            writer.writerow({field: getattr(summary, field) for field in fields})
    return output


def _summary_for_setup(setup: str, trades: list[TradeRecord]) -> SetupBacktestSummary:
    metrics = calculate_metrics(trades)
    wins = sum(1 for trade in trades if trade.net_r > 0.0)
    losses = sum(1 for trade in trades if trade.net_r < 0.0)
    return SetupBacktestSummary(
        setup=setup,
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        win_rate=metrics.win_rate,
        average_R=round(sum(trade.net_r for trade in trades) / len(trades), 4) if trades else 0.0,
        profit_factor=metrics.profit_factor,
        max_drawdown=metrics.max_drawdown,
        expectancy=metrics.expectancy,
        best_symbol=_symbol_by_expectancy(trades, best=True),
        worst_symbol=_symbol_by_expectancy(trades, best=False),
    )


def _symbol_by_expectancy(trades: list[TradeRecord], *, best: bool) -> str | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[trade.symbol].append(trade.net_r)
    if not grouped:
        return None
    ranked = sorted(
        ((symbol, sum(values) / len(values)) for symbol, values in grouped.items()),
        key=lambda item: item[1],
        reverse=best,
    )
    return ranked[0][0]


def _matches_setup(trade: TradeRecord, setup_filter: str | None) -> bool:
    if not setup_filter:
        return True
    normalized = setup_filter.strip().lower()
    valid = {item.value for item in SetupSubtype}
    if normalized in valid:
        return trade.setup_subtype.value == normalized
    return trade.setup_family.value == normalized
