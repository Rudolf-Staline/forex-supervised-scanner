"""Build a Deriv-Demo recommended watchlist from MT5 symbol health."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import WATCHLISTS, watchlist_names
from app.data.mt5_symbols_health import build_recommended_watchlist, diagnose_watchlist_symbols, resolve_symbols
from app.data.providers import DataProviderError
from app.utils.logging import configure_logging

WATCHLIST_FILE = PROJECT_ROOT / "app" / "config" / "watchlists.py"


def main() -> None:
    """Diagnose MT5 symbols and update only the requested recommended watchlist."""

    parser = argparse.ArgumentParser(description="Build a safe recommended Deriv-Demo watchlist. No orders are sent.")
    parser.add_argument("--source-watchlist", default="all_forex_demo", choices=watchlist_names())
    parser.add_argument("--output-watchlist", default="deriv_demo_recommended")
    parser.add_argument("--export-csv", action="store_true", help="Export reports/deriv_demo_recommended_watchlist.csv.")
    args = parser.parse_args()

    load_dotenv()
    configure_logging()
    settings = load_settings()
    symbols = resolve_symbols(None, args.source_watchlist)
    print("build_recommended_watchlist=started no_orders=true")
    print(f"source_watchlist={args.source_watchlist} symbols={','.join(symbols)}")
    try:
        health = diagnose_watchlist_symbols(symbols, settings=settings)
    except DataProviderError as exc:
        print(f"build_recommended_watchlist=error reason={exc}")
        raise SystemExit(1) from exc
    recommendation = build_recommended_watchlist(health)
    recommended = list(recommendation["recommended_watchlist_for_demo"])
    if not recommended:
        print("build_recommended_watchlist=error reason=no recommended symbols from MT5 health")
        _print_recommendation(recommendation)
        raise SystemExit(1)

    _write_watchlists_py(args.output_watchlist, recommended)
    _print_recommendation(recommendation)
    print(f"watchlist_updated name={args.output_watchlist} file={WATCHLIST_FILE}")
    if args.export_csv:
        path = _export_csv(args.output_watchlist, health, recommendation)
        print(f"csv_export={path}")


def _print_recommendation(recommendation: dict[str, object]) -> None:
    print(f"healthy_symbols={','.join(recommendation['healthy_symbols']) or '-'}")
    print(f"excluded_symbols={','.join(recommendation['excluded_symbols']) or '-'}")
    print(f"recommended_watchlist_for_demo={','.join(recommendation['recommended_watchlist_for_demo']) or '-'}")
    print(f"spread_atr_peer_threshold={_fmt(recommendation['spread_atr_peer_threshold'])}")
    print(f"top_5_clean_symbols={','.join(recommendation['top_clean_symbols']) or '-'}")
    print(f"top_5_expensive_spread_atr_symbols={','.join(recommendation['top_expensive_symbols']) or '-'}")
    print("reason_by_symbol:")
    reasons = recommendation["reason_by_symbol"]
    if not reasons:
        print("- n/a")
        return
    for symbol, reason in dict(reasons).items():
        print(f"- {symbol}: {reason}")


def _write_watchlists_py(name: str, symbols: list[str]) -> None:
    watchlists = {key: list(value) for key, value in WATCHLISTS.items()}
    watchlists[name] = symbols
    body = [
        '"""Named Forex watchlists for local scanner and demo bot runs."""',
        "",
        "from __future__ import annotations",
        "",
        "WATCHLISTS: dict[str, list[str]] = {",
    ]
    for key in sorted(watchlists):
        body.append(f'    "{key}": [')
        for symbol in watchlists[key]:
            body.append(f'        "{symbol}",')
        body.append("    ],")
    body.extend(
        [
            "}",
            "",
            "",
            "def watchlist_names() -> list[str]:",
            '    """Return configured watchlist profile names."""',
            "",
            "    return sorted(WATCHLISTS)",
            "",
            "",
            "def get_watchlist(name: str) -> list[str]:",
            '    """Return a copy of a configured watchlist or raise a clear error."""',
            "",
            "    try:",
            "        return list(WATCHLISTS[name])",
            "    except KeyError as exc:",
            "        available = \", \".join(watchlist_names())",
            "        raise ValueError(f\"unknown watchlist {name!r}; available watchlists: {available}\") from exc",
            "",
        ]
    )
    WATCHLIST_FILE.write_text("\n".join(body), encoding="utf-8")


def _export_csv(name: str, health, recommendation: dict[str, object]) -> Path:
    path = PROJECT_ROOT / "reports" / f"{name}_watchlist.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    reasons = dict(recommendation["reason_by_symbol"])
    recommended = set(recommendation["recommended_watchlist_for_demo"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "mt5_symbol", "recommended", "status", "reason", "spread_atr", "visible", "tradable"],
        )
        writer.writeheader()
        for item in health:
            writer.writerow(
                {
                    "symbol": item.symbol,
                    "mt5_symbol": item.mt5_symbol,
                    "recommended": item.symbol in recommended,
                    "status": item.status,
                    "reason": "recommended" if item.symbol in recommended else reasons.get(item.symbol, item.reason),
                    "spread_atr": item.spread_atr,
                    "visible": item.visible,
                    "tradable": item.tradable,
                }
            )
    return path


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


if __name__ == "__main__":
    main()
