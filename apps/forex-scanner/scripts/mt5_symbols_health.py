"""Diagnose MT5 symbol quality for Forex Supervisor demo watchlists."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import watchlist_names
from app.data.mt5_symbols_health import (
    diagnose_watchlist_symbols,
    export_symbol_health_csv,
    resolve_symbols_for_asset_class,
    summarize_symbol_health,
)
from app.data.providers import DataProviderError
from app.utils.logging import configure_logging


def main() -> None:
    """Run MT5 market-data diagnostics without submitting any order."""

    parser = argparse.ArgumentParser(description="Check MT5 symbol health for demo watchlists. No orders are sent.")
    parser.add_argument("--watchlist", default=None, choices=watchlist_names())
    parser.add_argument("--symbols", nargs="+", default=None, help="Explicit symbols. Overrides --watchlist.")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--export-csv", action="store_true", help="Export reports/mt5_symbols_health.csv.")
    args = parser.parse_args()

    load_dotenv()
    configure_logging()
    settings = load_settings()
    symbols = resolve_symbols_for_asset_class(args.symbols, args.watchlist, args.asset_class)
    print("mt5_symbols_health=started no_orders=true")
    print(f"symbols={','.join(symbols)}")
    try:
        results = diagnose_watchlist_symbols(symbols, settings=settings)
    except DataProviderError as exc:
        print(f"mt5_symbols_health=error reason={exc}")
        raise SystemExit(1) from exc

    for result in results:
        _print_symbol(result)
    _print_summary(results)
    if args.export_csv:
        path = export_symbol_health_csv(results, PROJECT_ROOT / "reports" / "mt5_symbols_health.csv")
        print(f"csv_export={path}")


def _print_symbol(result) -> None:
    print(
        "symbol_health "
        f"symbol={result.symbol} mt5_symbol={result.mt5_symbol} asset_class={result.asset_class} status={result.status} reason={result.reason} "
        f"visible={result.visible} selected={result.selected} tradable={result.tradable} "
        f"spread={_fmt(result.spread)} atr={_fmt(result.atr)} spread_atr={_fmt(result.spread_atr)} "
        f"trade_mode={result.trade_mode} volume_min={_fmt(result.volume_min)} volume_step={_fmt(result.volume_step)} "
        f"stops_level={result.stops_level} freeze_level={result.freeze_level} last_error={result.last_error}"
    )
    for timeframe in result.timeframes:
        print(
            "timeframe_health "
            f"symbol={result.symbol} timeframe={timeframe.timeframe.value} bars={timeframe.bars} "
            f"last_candle={timeframe.last_candle or '-'} error={timeframe.error or '-'}"
        )
    if not result.healthy:
        print(f"recommendation symbol={result.symbol} action=exclude_from_active_watchlist reason={result.reason}")


def _print_summary(results) -> None:
    summary = summarize_symbol_health(results)
    print("Symbol health summary:")
    print(f"- healthy_symbols: {','.join(summary['healthy_symbols']) or '-'}")
    print(f"- unhealthy_symbols: {','.join(summary['unhealthy_symbols']) or '-'}")
    print(f"- highest_spread_atr_symbols: {','.join(summary['highest_spread_atr_symbols']) or '-'}")
    print(f"- lowest_spread_atr_symbols: {','.join(summary['lowest_spread_atr_symbols']) or '-'}")
    print(f"- recommended_watchlist_for_demo: {','.join(summary['recommended_watchlist_for_demo']) or '-'}")


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.5f}"
    return str(value)


if __name__ == "__main__":
    main()
