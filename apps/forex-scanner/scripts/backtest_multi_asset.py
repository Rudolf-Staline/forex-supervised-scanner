"""Session-aware multi-asset backtest report. Reporting only; no orders are sent."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.engine import Backtester
from app.backtest.metrics import calculate_metrics
from app.config.env import load_dotenv
from app.config.instruments import AssetClass, filter_symbols_by_asset_class, instrument_for_symbol
from app.config.settings import load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.core.types import TradeRecord, TradingStyle
from app.data.providers import build_provider
from app.execution.rejected_signals import RejectedSignalRecord
from app.market.sessions import get_market_session
from app.storage.database import Database

WARNING = "Backtest results are historical simulations and do not guarantee future performance."
REPORTS_DIR = PROJECT_ROOT / "reports"
CSV_PATH = REPORTS_DIR / "backtest_multi_asset.csv"
SUMMARY_PATH = REPORTS_DIR / "backtest_multi_asset_summary.json"
SETUPS_OF_INTEREST = [
    "ema50_pullback",
    "retest_continuation",
    "momentum_breakout",
    "range_edge_reversal",
    "bollinger_snapback",
]


@dataclass(frozen=True)
class BacktestMultiAssetRow:
    """One grouped multi-asset backtest row."""

    asset_class: str
    symbol: str
    setup: str
    session: str
    total_signals: int
    total_trades_simulated: int
    win_rate: float
    average_R: float
    expectancy_R: float
    profit_factor: float
    max_drawdown_R: float
    best_trade_R: float
    worst_trade_R: float
    average_spread_atr: float | None
    rejected_count: int
    rejection_reasons_top: str


def main() -> None:
    """Run a multi-asset backtest and print a diagnostic report."""

    parser = argparse.ArgumentParser(description="Backtest multi-asset setups by symbol, setup, and session. No orders are sent.")
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "auto", "mt5"])
    parser.add_argument("--watchlist", default="multi_asset_demo", choices=watchlist_names())
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--only-tradable-session", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    _quiet_expected_provider_failures()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)
    style = TradingStyle(args.style)
    end = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
    start = _parse_date(args.from_date) if args.from_date else end - timedelta(days=14)
    symbols = filter_symbols_by_asset_class(get_watchlist(args.watchlist), args.asset_class)

    print("backtest_multi_asset=no_orders")
    print(f"warning={WARNING}")
    print(
        f"provider={provider.name} watchlist={args.watchlist} asset_class={args.asset_class} "
        f"style={style.value} from={start.date()} to={end.date()} min_score={args.min_score:.1f} "
        f"only_tradable_session={str(args.only_tradable_session).lower()}"
    )

    result = Backtester(settings, provider, database).run(symbols, style, "all", start, end)
    trades = filter_backtest_trades(
        result.trades,
        min_score=args.min_score,
        only_tradable_session=args.only_tradable_session,
    )
    rejected = filter_rejected_records(
        database.load_rejected_signals(),
        symbols=symbols,
        style=style.value,
        start=start,
        end=end,
    )
    rows = build_backtest_rows(trades, rejected)
    summary = build_backtest_summary(trades, rows)
    _print_report(result.trades, trades, rejected, rows, summary, limitations=result.limitations)
    if args.export_csv:
        export_backtest_csv(rows, CSV_PATH)
        export_summary_json(summary, SUMMARY_PATH)
        print(f"csv_export={CSV_PATH}")
        print(f"summary_json_export={SUMMARY_PATH}")


def filter_backtest_trades(
    trades: list[TradeRecord],
    *,
    min_score: float,
    only_tradable_session: bool,
) -> list[TradeRecord]:
    """Apply score and optional asset-class session filters to simulated trades."""

    filtered = [trade for trade in trades if trade.final_score is not None and trade.final_score >= min_score]
    if not only_tradable_session:
        return filtered
    tradable: list[TradeRecord] = []
    for trade in filtered:
        instrument = instrument_for_symbol(trade.symbol)
        session = get_market_session(trade.entry_time, instrument.asset_class, trade.symbol)
        if session.is_tradable_session:
            tradable.append(trade)
    return tradable


def filter_rejected_records(
    records: list[RejectedSignalRecord],
    *,
    symbols: list[str],
    style: str,
    start: datetime,
    end: datetime,
) -> list[RejectedSignalRecord]:
    """Filter stored rejected signals for the same analysis window."""

    symbol_set = set(symbols)
    return [
        record
        for record in records
        if record.symbol in symbol_set
        and (record.style is None or record.style == style)
        and start <= record.timestamp.astimezone(timezone.utc) <= end
    ]


def build_backtest_rows(
    trades: list[TradeRecord],
    rejected: list[RejectedSignalRecord],
) -> list[BacktestMultiAssetRow]:
    """Build grouped rows by asset class, symbol, setup, and session."""

    grouped_trades: dict[tuple[str, str, str, str], list[TradeRecord]] = defaultdict(list)
    grouped_rejected: dict[tuple[str, str, str, str], list[RejectedSignalRecord]] = defaultdict(list)
    for trade in trades:
        key = _trade_key(trade)
        grouped_trades[key].append(trade)
    for record in rejected:
        key = _rejected_key(record)
        grouped_rejected[key].append(record)
    rows: list[BacktestMultiAssetRow] = []
    for key in sorted(set(grouped_trades) | set(grouped_rejected)):
        asset_class, symbol, setup, session = key
        group_trades = grouped_trades.get(key, [])
        group_rejected = grouped_rejected.get(key, [])
        rows.append(_row(asset_class, symbol, setup, session, group_trades, group_rejected))
    return rows


def build_backtest_summary(trades: list[TradeRecord], rows: list[BacktestMultiAssetRow]) -> dict:
    """Build terminal/JSON summary sections."""

    return {
        "warning": WARNING,
        "best_markets_by_expectancy": _best_markets_by_expectancy(rows),
        "best_sessions": _best_sessions(rows),
        "setup_quality": _setup_quality(trades),
    }


def export_backtest_csv(rows: list[BacktestMultiAssetRow], path: Path = CSV_PATH) -> None:
    """Export grouped backtest rows to CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(BacktestMultiAssetRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def export_summary_json(summary: dict, path: Path = SUMMARY_PATH) -> None:
    """Export summary sections to JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def _row(
    asset_class: str,
    symbol: str,
    setup: str,
    session: str,
    trades: list[TradeRecord],
    rejected: list[RejectedSignalRecord],
) -> BacktestMultiAssetRow:
    metrics = calculate_metrics(trades)
    net_rs = [trade.net_r for trade in trades]
    spreads = [float(record.spread_atr) for record in rejected if record.spread_atr is not None]
    return BacktestMultiAssetRow(
        asset_class=asset_class,
        symbol=symbol,
        setup=setup,
        session=session,
        total_signals=len(trades) + len(rejected),
        total_trades_simulated=len(trades),
        win_rate=metrics.win_rate,
        average_R=round(sum(net_rs) / len(net_rs), 4) if net_rs else 0.0,
        expectancy_R=metrics.expectancy,
        profit_factor=metrics.profit_factor,
        max_drawdown_R=metrics.max_drawdown,
        best_trade_R=round(max(net_rs), 4) if net_rs else 0.0,
        worst_trade_R=round(min(net_rs), 4) if net_rs else 0.0,
        average_spread_atr=round(sum(spreads) / len(spreads), 4) if spreads else None,
        rejected_count=len(rejected),
        rejection_reasons_top=_format_counter(Counter(reason for record in rejected for reason in record.rejection_reasons), limit=3),
    )


def _best_markets_by_expectancy(rows: list[BacktestMultiAssetRow]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for asset in AssetClass:
        symbol_values: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if row.asset_class == asset.value and row.total_trades_simulated > 0:
                symbol_values[row.symbol].append(row.expectancy_R)
        ranked = sorted(
            ((symbol, sum(values) / len(values)) for symbol, values in symbol_values.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        result[asset.value] = [f"{symbol} expectancy_R={value:.4f}" for symbol, value in ranked[:3]]
    return result


def _best_sessions(rows: list[BacktestMultiAssetRow]) -> dict[str, str]:
    result: dict[str, str] = {}
    for asset in AssetClass:
        session_values: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if row.asset_class == asset.value and row.total_trades_simulated > 0:
                session_values[row.session].append(row.expectancy_R)
        if not session_values:
            result[asset.value] = "n/a"
            continue
        session, value = max(
            ((session, sum(values) / len(values)) for session, values in session_values.items()),
            key=lambda item: item[1],
        )
        result[asset.value] = f"{session} expectancy_R={value:.4f}"
    return result


def _setup_quality(trades: list[TradeRecord]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for setup in SETUPS_OF_INTEREST:
        group = [trade for trade in trades if trade.setup_subtype.value == setup]
        metrics = calculate_metrics(group)
        result[setup] = {
            "occurrences": len(group),
            "win_rate": metrics.win_rate,
            "expectancy_R": metrics.expectancy,
            "best_asset_class": _best_asset_class_for_setup(group),
            "failing_symbols": _failing_symbols(group),
        }
    return result


def _best_asset_class_for_setup(trades: list[TradeRecord]) -> str:
    values: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        values[instrument_for_symbol(trade.symbol).asset_class.value].append(trade.net_r)
    if not values:
        return "n/a"
    asset, value = max(((asset, sum(items) / len(items)) for asset, items in values.items()), key=lambda item: item[1])
    return f"{asset} expectancy_R={value:.4f}"


def _failing_symbols(trades: list[TradeRecord]) -> list[str]:
    values: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        values[trade.symbol].append(trade.net_r)
    failing = [
        symbol
        for symbol, items in sorted(values.items())
        if items and (sum(items) / len(items)) < 0.0
    ]
    return failing[:5]


def _trade_key(trade: TradeRecord) -> tuple[str, str, str, str]:
    asset_class = instrument_for_symbol(trade.symbol).asset_class.value
    session = _asset_session_name(trade.entry_time, asset_class, trade.symbol)
    return asset_class, trade.symbol, trade.setup_subtype.value, session


def _rejected_key(record: RejectedSignalRecord) -> tuple[str, str, str, str]:
    asset_class = instrument_for_symbol(record.symbol).asset_class.value
    session = _asset_session_name(record.timestamp, asset_class, record.symbol)
    return asset_class, record.symbol, record.setup or "none", session


def _asset_session_name(timestamp: datetime, asset_class: str, symbol: str) -> str:
    return get_market_session(timestamp, asset_class, symbol).session_name


def _print_report(
    all_trades: list[TradeRecord],
    filtered_trades: list[TradeRecord],
    rejected: list[RejectedSignalRecord],
    rows: list[BacktestMultiAssetRow],
    summary: dict,
    *,
    limitations: list[str],
) -> None:
    print(f"total_backtest_trades={len(all_trades)}")
    print(f"filtered_backtest_trades={len(filtered_trades)}")
    print(f"rejected_signal_records_used={len(rejected)}")
    if limitations:
        print("limitations:")
        for item in limitations:
            print(f"- {item}")
    print("grouped_results:")
    for row in rows[:40]:
        print(
            "group "
            f"asset_class={row.asset_class} symbol={row.symbol} setup={row.setup} session={row.session} "
            f"total_signals={row.total_signals} total_trades_simulated={row.total_trades_simulated} "
            f"win_rate={row.win_rate:.2f} average_R={row.average_R:.4f} expectancy_R={row.expectancy_R:.4f} "
            f"profit_factor={row.profit_factor:.4f} max_drawdown_R={row.max_drawdown_R:.4f} "
            f"best_trade_R={row.best_trade_R:.4f} worst_trade_R={row.worst_trade_R:.4f} "
            f"average_spread_atr={_fmt_optional(row.average_spread_atr)} rejected_count={row.rejected_count} "
            f"rejection_reasons_top=\"{row.rejection_reasons_top}\""
        )
    _print_summary(summary)


def _print_summary(summary: dict) -> None:
    print("best_markets_by_expectancy:")
    for asset in AssetClass:
        values = summary["best_markets_by_expectancy"].get(asset.value, [])
        print(f"- {asset.value}: {', '.join(values) or 'n/a'}")
    print("best_sessions:")
    for asset in AssetClass:
        print(f"- {asset.value}: {summary['best_sessions'].get(asset.value, 'n/a')}")
    print("setup_quality:")
    for setup, data in summary["setup_quality"].items():
        print(
            f"- {setup}: occurrences={data['occurrences']} win_rate={data['win_rate']:.2f} "
            f"expectancy_R={data['expectancy_R']:.4f} best_asset_class=\"{data['best_asset_class']}\" "
            f"failing_symbols={','.join(data['failing_symbols']) or '-'}"
        )


def _format_counter(counter: Counter[str], *, limit: int) -> str:
    if not counter:
        return "-"
    return "; ".join(f"{key}={count}" for key, count in counter.most_common(limit))


def _fmt_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _quiet_expected_provider_failures() -> None:
    logging.getLogger("app.backtest.engine").setLevel(logging.CRITICAL)
    logging.getLogger("app.data.providers").setLevel(logging.CRITICAL)


if __name__ == "__main__":
    main()
