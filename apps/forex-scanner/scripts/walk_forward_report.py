"""Walk-forward / out-of-sample backtest report. Reporting only; no orders are sent.

Thresholds are tuned exclusively on each in-sample fold and the reported metrics
come exclusively from the out-of-sample folds, giving an honest forward estimate.
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
from app.backtest.walk_forward import (
    WalkForwardConfig,
    backtester_segment_runner,
    report_to_text,
    run_walk_forward,
    write_reports,
)
from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.core.types import SetupFamily, TradingStyle
from app.data.providers import build_provider


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward backtest. Reporting only; no orders are sent.")
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "auto", "mt5"])
    parser.add_argument("--watchlist", default=None, choices=watchlist_names())
    parser.add_argument("--symbols", nargs="+", default=None, help="Explicit symbols. Overrides --watchlist.")
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--setup", default="all", help="Setup family filter or 'all'.")
    parser.add_argument("--from-date", default=None, help="UTC start date, e.g. 2026-01-01.")
    parser.add_argument("--to-date", default=None, help="UTC end date, e.g. 2026-06-01.")
    parser.add_argument("--in-sample-days", type=int, default=45)
    parser.add_argument("--out-of-sample-days", type=int, default=15)
    parser.add_argument("--step-days", type=int, default=15)
    parser.add_argument("--score-grid", default="0,55,60,65,70,75,80", help="Comma-separated min-score candidates.")
    parser.add_argument("--min-in-sample-trades", type=int, default=5)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "reports"))
    args = parser.parse_args()

    load_dotenv()
    _quiet_expected_provider_failures()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    style = TradingStyle(args.style)
    setup_filter = _parse_setup_filter(args.setup)
    end = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
    start = _parse_date(args.from_date) if args.from_date else end - timedelta(days=120)
    symbols = _resolve_symbols(args.symbols, args.watchlist)
    score_grid = tuple(float(value) for value in args.score_grid.split(",") if value.strip())

    config = WalkForwardConfig(
        in_sample_days=args.in_sample_days,
        out_of_sample_days=args.out_of_sample_days,
        step_days=args.step_days,
        score_grid=score_grid,
        min_in_sample_trades=args.min_in_sample_trades,
    )

    # database=None: walk-forward runs many short segments; we do not persist them.
    provider = build_provider(settings)
    backtester = Backtester(settings, provider, database=None)
    runner = backtester_segment_runner(backtester)

    print(
        "walk_forward "
        f"provider={provider.name} style={style.value} symbols={','.join(symbols)} "
        f"from={start.date()} to={end.date()} is={config.in_sample_days}d oos={config.out_of_sample_days}d "
        f"step={config.step_days}d setup={args.setup}"
    )
    print("warning=Walk-forward backtest; resultats passes sans garantie de performance future; aucune execution broker.")

    report = run_walk_forward(runner, symbols, style, setup_filter, start, end, config)
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
