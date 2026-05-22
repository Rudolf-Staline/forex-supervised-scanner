"""Report current demo market sessions without placing orders."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.instruments import AssetClass, filter_symbols_by_asset_class, instrument_for_symbol
from app.config.watchlists import get_watchlist, watchlist_names
from app.market.sessions import best_session_for_asset_class, explain_off_hours, get_market_session

REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_CSV_PATH = REPORTS_DIR / "market_sessions_report.csv"


def main() -> None:
    """Print an asset-class session report for a demo watchlist."""

    parser = argparse.ArgumentParser(description="Show current Forex Supervisor demo market sessions.")
    parser.add_argument("--watchlist", default="multi_asset_demo", choices=watchlist_names())
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--export-csv", action="store_true", help="Export the detailed session rows to reports/market_sessions_report.csv.")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    report = build_market_sessions_report(args.watchlist, args.asset_class, now)

    print("market_sessions_report=no_orders")
    print(f"current_utc_time={report['current_utc_time']}")
    print(f"watchlist={args.watchlist} asset_class={args.asset_class}")
    print(f"tradable_now={','.join(report['tradable_now']) or '-'}")
    print(f"off_hours_now={','.join(report['off_hours_now']) or '-'}")
    print(f"recommended_watchlist_now={','.join(report['recommended_watchlist_now']) or '-'}")
    print(f"next_windows_by_asset_class={_format_next_windows(report['next_windows_by_asset_class'])}")
    print(f"recommended_next_run_time={report['recommended_next_run_time']}")
    print(f"best_session_forex={best_session_for_asset_class(AssetClass.FOREX)}")
    print(f"best_session_commodities={best_session_for_asset_class(AssetClass.COMMODITIES)}")
    print(f"best_session_indices={best_session_for_asset_class(AssetClass.INDICES)}")
    for row in report["rows"]:
        print(
            "session "
            f"symbol={row['symbol']} asset_class={row['asset_class']} session_name={row['session_name']} "
            f"is_tradable_session={str(row['is_tradable_session']).lower()} "
            f"next_tradable_window=\"{row['next_tradable_window']}\" reason=\"{row['reason']}\""
        )
        if not row["is_tradable_session"]:
            print(f"off_hours_explanation symbol={row['symbol']} explanation=\"{row['off_hours_explanation']}\"")
    if args.export_csv:
        export_market_sessions_csv(report["rows"], DEFAULT_CSV_PATH)
        print(f"csv_exported={DEFAULT_CSV_PATH}")


def build_market_sessions_report(watchlist: str, asset_class: str, now: datetime) -> dict:
    """Build a no-orders market-session report for a watchlist."""

    symbols = filter_symbols_by_asset_class(get_watchlist(watchlist), asset_class)
    rows = []
    next_windows_by_asset_class: dict[str, str] = {}
    for symbol in symbols:
        instrument = instrument_for_symbol(symbol)
        info = get_market_session(now, instrument.asset_class, symbol)
        next_windows_by_asset_class.setdefault(info.asset_class, info.next_tradable_window)
        rows.append(
            {
                "current_utc_time": now.isoformat(timespec="seconds"),
                "symbol": symbol,
                "asset_class": info.asset_class,
                "session_name": info.session_name,
                "is_tradable_session": info.is_tradable_session,
                "next_tradable_window": info.next_tradable_window,
                "reason": info.reason,
                "off_hours_explanation": "" if info.is_tradable_session else explain_off_hours(symbol, info.asset_class, now),
            }
        )

    for asset in AssetClass:
        sample = _sample_symbol(asset, symbols)
        next_windows_by_asset_class.setdefault(asset.value, get_market_session(now, asset, sample).next_tradable_window)

    tradable_now = [row["symbol"] for row in rows if row["is_tradable_session"]]
    off_hours_now = [row["symbol"] for row in rows if not row["is_tradable_session"]]
    recommended_watchlist_now = list(tradable_now)
    return {
        "current_utc_time": now.isoformat(timespec="seconds"),
        "tradable_now": tradable_now,
        "off_hours_now": off_hours_now,
        "recommended_watchlist_now": recommended_watchlist_now,
        "next_windows_by_asset_class": next_windows_by_asset_class,
        "recommended_next_run_time": _recommended_next_run_time(list(next_windows_by_asset_class.values())),
        "rows": rows,
    }


def export_market_sessions_csv(rows: list[dict], path: Path = DEFAULT_CSV_PATH) -> None:
    """Export detailed market-session rows to CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "current_utc_time",
        "symbol",
        "asset_class",
        "session_name",
        "is_tradable_session",
        "next_tradable_window",
        "reason",
        "off_hours_explanation",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _format_next_windows(windows: dict[str, str]) -> str:
    return "; ".join(f"{asset}={window}" for asset, window in sorted(windows.items()))


def _recommended_next_run_time(next_windows: list[str]) -> str:
    candidates: list[str] = []
    for window in next_windows:
        if not window or window.startswith("no configured"):
            continue
        if "now until " in window:
            return "now"
        for part in window.split():
            if "T" in part and "+" in part:
                candidates.append(part)
                break
    return min(candidates) if candidates else "no configured tradable window found"


def _sample_symbol(asset_class: AssetClass, symbols: list[str]) -> str:
    for symbol in symbols:
        if instrument_for_symbol(symbol).asset_class == asset_class:
            return symbol
    return {
        AssetClass.FOREX: "EUR/USD",
        AssetClass.COMMODITIES: "XAU/USD",
        AssetClass.INDICES: "US500",
    }[asset_class]


if __name__ == "__main__":
    main()
