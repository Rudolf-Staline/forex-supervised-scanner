"""Score -> expectancy calibration report from a backtest. Reporting only; no orders.

Runs the existing Backtester over a date range, then buckets the resulting trades by
``final_score`` decile and measures realized expectancy (net R) with a bootstrap CI
per bucket, a monotonicity test, and per-component separation diagnostics.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.engine import Backtester
from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.core.types import SetupFamily, TradingStyle
from app.data.providers import build_provider
from app.reporting.score_expectancy import build_report, report_to_text, write_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Score->expectancy calibration. Reporting only; no orders are sent.")
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "auto", "mt5"])
    parser.add_argument("--watchlist", default=None, choices=watchlist_names())
    parser.add_argument("--symbols", nargs="+", default=None, help="Explicit symbols. Overrides --watchlist.")
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--setup", default="all", help="Setup family filter or 'all'.")
    parser.add_argument("--from-date", default=None, help="UTC start date, e.g. 2026-01-01.")
    parser.add_argument("--to-date", default=None, help="UTC end date, e.g. 2026-06-01.")
    parser.add_argument("--buckets", type=int, default=10)
    parser.add_argument("--bootstrap-resamples", type=int, default=2000)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "reports"))
    args = parser.parse_args()

    load_dotenv()
    _quiet_expected_provider_failures()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    style = TradingStyle(args.style)
    setup_filter = _parse_setup_filter(args.setup)
    end = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
    start = _parse_date(args.from_date) if args.from_date else end - timedelta(days=60)
    symbols = _resolve_symbols(args.symbols, args.watchlist)

    provider = build_provider(settings)
    result = Backtester(settings, provider, database=None).run(symbols, style, setup_filter, start, end)

    print(
        "score_expectancy_calibration "
        f"provider={provider.name} style={style.value} symbols={','.join(symbols)} "
        f"from={start.date()} to={end.date()} setup={args.setup} trades={len(result.trades)}"
    )
    print("warning=Calibration sur backtest passe; aucune garantie future; aucune execution broker.")

    report = build_report(result.trades, n_buckets=args.buckets, bootstrap_resamples=args.bootstrap_resamples)
    outputs = write_reports(report, Path(args.output_dir))
    print(report_to_text(report))
    print(f"json_export={outputs['json']}")
    print(f"txt_export={outputs['txt']}")


def _parse_setup_filter(value: str) -> SetupFamily | str:
    cleaned = value.strip().lower()
    if cleaned in {"all", ""}:
        return "all"
    return SetupFamily(cleaned)


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


if __name__ == "__main__":
    main()
