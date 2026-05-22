"""Backtest Forex Supervisor setups and chart-pattern confluence separately."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.engine import Backtester
from app.backtest.metrics import calculate_metrics
from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.core.types import TradeRecord, TradingStyle
from app.data.providers import build_provider
from app.storage.database import Database


@dataclass(frozen=True)
class BacktestGroupSummary:
    """Aggregated metrics for one backtest grouping dimension."""

    group_type: str
    group_value: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    average_R: float
    expectancy: float
    profit_factor: float
    max_drawdown: float
    best_symbol: str
    worst_symbol: str
    best_setup: str
    worst_setup: str


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest setups/patterns. Reporting only; no orders are sent.")
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "auto", "mt5"])
    parser.add_argument("--watchlist", default=None, choices=watchlist_names())
    parser.add_argument("--symbols", nargs="+", default=None, help="Explicit symbols. Overrides --watchlist.")
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--from-date", default=None, help="UTC start date, e.g. 2026-05-01.")
    parser.add_argument("--to-date", default=None, help="UTC end date, e.g. 2026-05-21.")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--setup", default=None, help="Optional setup subtype/family, e.g. ema50_pullback.")
    parser.add_argument("--export-csv", action="store_true", help="Export reports/backtest_setups.csv.")
    args = parser.parse_args()

    load_dotenv()
    _quiet_expected_provider_failures()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    style = TradingStyle(args.style)
    end = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
    start = _parse_date(args.from_date) if args.from_date else end - timedelta(days=14)
    symbols = _resolve_symbols(args.symbols, args.watchlist)
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)

    print(
        "backtest_setups "
        f"provider={provider.name} style={style.value} symbols={','.join(symbols)} "
        f"from={start.date()} to={end.date()} min_score={args.min_score if args.min_score is not None else 'engine_default'} "
        f"setup={args.setup or 'all'} watchlist={args.watchlist or '-'}"
    )
    print("warning=Backtest simplifie; resultats passes sans garantie de performance future; aucune execution broker.")
    result = Backtester(settings, provider, database).run(symbols, style, "all", start, end)
    trades = _filter_trades(result.trades, setup=args.setup, min_score=args.min_score)
    summaries = _summarize_all_groups(trades)
    _print_summary(result.trades, trades, summaries, limitations=result.limitations)
    if args.export_csv:
        output = _export_csv(summaries, PROJECT_ROOT / "reports" / "backtest_setups.csv")
        print(f"csv_export={output}")


def _resolve_symbols(symbols: list[str] | None, watchlist: str | None) -> list[str]:
    if symbols:
        resolved: list[str] = []
        for raw in symbols:
            resolved.extend(symbol.strip().upper() for symbol in raw.split(",") if symbol.strip())
        return resolved
    if watchlist:
        return get_watchlist(watchlist)
    return ["EUR/USD", "GBP/USD", "USD/CHF"]


def _quiet_expected_provider_failures() -> None:
    logging.getLogger("app.backtest.engine").setLevel(logging.CRITICAL)
    logging.getLogger("app.data.providers").setLevel(logging.CRITICAL)


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _filter_trades(trades: list[TradeRecord], *, setup: str | None, min_score: float | None) -> list[TradeRecord]:
    filtered = trades
    if setup:
        wanted = setup.strip().lower()
        filtered = [trade for trade in filtered if trade.setup_subtype.value == wanted or trade.setup_family.value == wanted]
    if min_score is not None:
        filtered = [trade for trade in filtered if trade.final_score is not None and trade.final_score >= min_score]
    return filtered


def _summarize_all_groups(trades: list[TradeRecord]) -> list[BacktestGroupSummary]:
    rows: list[BacktestGroupSummary] = []
    rows.extend(_group_summaries(trades, "symbol", lambda trade: trade.symbol))
    rows.extend(_group_summaries(trades, "setup", lambda trade: trade.setup_subtype.value))
    rows.extend(_group_summaries(trades, "pattern", _patterns_for_trade))
    rows.extend(_group_summaries(trades, "session", lambda trade: trade.session.value if trade.session else "unknown"))
    rows.extend(_group_summaries(trades, "style", lambda trade: trade.style.value))
    return rows


def _group_summaries(trades: list[TradeRecord], group_type: str, key_fn) -> list[BacktestGroupSummary]:
    grouped: dict[str, list[TradeRecord]] = defaultdict(list)
    for trade in trades:
        keys = key_fn(trade)
        if isinstance(keys, list):
            values = keys or ["none"]
        else:
            values = [keys]
        for value in values:
            grouped[str(value)].append(trade)
    return [_summary(group_type, value, group_trades) for value, group_trades in sorted(grouped.items())]


def _summary(group_type: str, group_value: str, trades: list[TradeRecord]) -> BacktestGroupSummary:
    metrics = calculate_metrics(trades)
    wins = sum(1 for trade in trades if trade.net_r > 0.0)
    losses = sum(1 for trade in trades if trade.net_r < 0.0)
    average_r = round(sum(trade.net_r for trade in trades) / len(trades), 4) if trades else 0.0
    return BacktestGroupSummary(
        group_type=group_type,
        group_value=group_value,
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        win_rate=metrics.win_rate,
        average_R=average_r,
        expectancy=metrics.expectancy,
        profit_factor=metrics.profit_factor,
        max_drawdown=metrics.max_drawdown,
        best_symbol=_best_dimension(trades, lambda trade: trade.symbol, best=True),
        worst_symbol=_best_dimension(trades, lambda trade: trade.symbol, best=False),
        best_setup=_best_dimension(trades, lambda trade: trade.setup_subtype.value, best=True),
        worst_setup=_best_dimension(trades, lambda trade: trade.setup_subtype.value, best=False),
    )


def _best_dimension(trades: list[TradeRecord], key_fn, *, best: bool) -> str:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[str(key_fn(trade))].append(trade.net_r)
    if not grouped:
        return "-"
    ranked = sorted(((key, sum(values) / len(values)) for key, values in grouped.items()), key=lambda item: item[1], reverse=best)
    return ranked[0][0]


def _patterns_for_trade(trade: TradeRecord) -> list[str]:
    return list(trade.detected_patterns) if trade.detected_patterns else ["none"]


def _print_summary(
    all_trades: list[TradeRecord],
    filtered_trades: list[TradeRecord],
    summaries: list[BacktestGroupSummary],
    *,
    limitations: list[str],
) -> None:
    print(f"total_backtest_trades={len(all_trades)}")
    print(f"filtered_backtest_trades={len(filtered_trades)}")
    if limitations:
        print("limitations:")
        for item in limitations:
            print(f"- {item}")
    for row in summaries:
        print(
            "group "
            f"type={row.group_type} value={row.group_value} total_trades={row.total_trades} "
            f"wins={row.wins} losses={row.losses} win_rate={row.win_rate:.2f} "
            f"average_R={row.average_R:.4f} expectancy={row.expectancy:.4f} "
            f"profit_factor={row.profit_factor:.4f} max_drawdown={row.max_drawdown:.4f} "
            f"best_symbol={row.best_symbol} worst_symbol={row.worst_symbol} "
            f"best_setup={row.best_setup} worst_setup={row.worst_setup}"
        )


def _export_csv(summaries: list[BacktestGroupSummary], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(BacktestGroupSummary.__dataclass_fields__)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in summaries:
            writer.writerow({field: getattr(row, field) for field in fields})
    return output


if __name__ == "__main__":
    main()
