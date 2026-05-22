"""Report current demo market sessions without placing orders."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.instruments import AssetClass, filter_symbols_by_asset_class, instrument_for_symbol
from app.config.watchlists import get_watchlist, watchlist_names
from app.market.sessions import best_session_for_asset_class, explain_off_hours, get_market_session


def main() -> None:
    """Print an asset-class session report for a demo watchlist."""

    parser = argparse.ArgumentParser(description="Show current Forex Supervisor demo market sessions.")
    parser.add_argument("--watchlist", default="multi_asset_demo", choices=watchlist_names())
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    symbols = filter_symbols_by_asset_class(get_watchlist(args.watchlist), args.asset_class)
    rows = [(symbol, get_market_session(now, instrument_for_symbol(symbol).asset_class, symbol)) for symbol in symbols]
    tradable = [symbol for symbol, info in rows if info.is_tradable_session]
    off_hours = [symbol for symbol, info in rows if not info.is_tradable_session]

    print("market_sessions_report=no_orders")
    print(f"timestamp_utc={now.isoformat(timespec='seconds')}")
    print(f"watchlist={args.watchlist} asset_class={args.asset_class}")
    print(f"currently_tradable={','.join(tradable) or '-'}")
    print(f"off_hours={','.join(off_hours) or '-'}")
    print(f"best_session_forex={best_session_for_asset_class(AssetClass.FOREX)}")
    print(f"best_session_commodities={best_session_for_asset_class(AssetClass.COMMODITIES)}")
    print(f"best_session_indices={best_session_for_asset_class(AssetClass.INDICES)}")
    for symbol, info in rows:
        print(
            "session "
            f"symbol={symbol} asset_class={info.asset_class} session_name={info.session_name} "
            f"is_tradable_session={str(info.is_tradable_session).lower()} "
            f"next_tradable_window=\"{info.next_tradable_window}\" reason=\"{info.reason}\""
        )
        if not info.is_tradable_session:
            print(f"off_hours_explanation symbol={symbol} explanation=\"{explain_off_hours(symbol, info.asset_class, now)}\"")


if __name__ == "__main__":
    main()
