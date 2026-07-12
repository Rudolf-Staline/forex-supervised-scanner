"""Walk-forward / out-of-sample backtest report. Reporting only; no orders are sent.

Thresholds are tuned exclusively on each in-sample fold. By default, the
canonical de-duplicated OOS candidates are then passed through portfolio
constraints before headline metrics are published. Use ``--no-portfolio`` only
for candidate-level diagnostics or historical reproducibility.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtest.engine import Backtester
from app.backtest.portfolio import PortfolioConstraints
from app.backtest.portfolio_walk_forward import (
    apply_portfolio_constraints,
    portfolio_report_to_text,
    write_portfolio_walk_forward_reports,
)
from app.backtest.walk_forward import (
    WalkForwardConfig,
    backtester_segment_runner,
    report_to_text,
    run_walk_forward,
    run_walk_forward_parallel,
    write_reports,
)
from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.core.types import SetupFamily, TradingStyle
from app.data.providers import build_provider


def main() -> None:
    parser = _build_parser()
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
    n_jobs = args.jobs if args.jobs is not None else (os.cpu_count() or 1)

    provider = build_provider(settings)
    backtester = Backtester(settings, provider, database=None)
    runner = backtester_segment_runner(backtester)

    print(
        "walk_forward "
        f"provider={provider.name} style={style.value} symbols={','.join(symbols)} "
        f"from={start.date()} to={end.date()} is={config.in_sample_days}d "
        f"oos={config.out_of_sample_days}d step={config.step_days}d "
        f"setup={args.setup} jobs={n_jobs} portfolio={args.portfolio}"
    )
    print(
        "warning=Walk-forward backtest; resultats passes sans garantie de performance future; "
        "aucune execution broker."
    )

    if n_jobs == 1:
        report = run_walk_forward(runner, symbols, style, setup_filter, start, end, config)
    else:
        report = run_walk_forward_parallel(
            settings,
            symbols,
            style,
            setup_filter,
            start,
            end,
            config,
            n_jobs=n_jobs,
        )

    output_dir = Path(args.output_dir)
    if args.portfolio:
        constraints = _portfolio_constraints(args)
        portfolio_report = apply_portfolio_constraints(report, constraints)
        outputs = write_portfolio_walk_forward_reports(portfolio_report, output_dir)
        print(portfolio_report_to_text(portfolio_report))
    else:
        outputs = write_reports(report, output_dir)
        print(report_to_text(report))
        print(
            "warning=Portfolio constraints disabled; headline metrics describe canonical "
            "candidates, not an attainable multi-pair allocation."
        )

    for label, path in outputs.items():
        print(f"{label}_export={path}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Walk-forward backtest. Reporting only; no orders are sent."
    )
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "auto", "mt5", "csv"])
    parser.add_argument("--watchlist", default=None, choices=watchlist_names())
    parser.add_argument("--symbols", nargs="+", default=None, help="Explicit symbols. Overrides --watchlist.")
    parser.add_argument(
        "--style",
        default=TradingStyle.DAY_TRADING.value,
        choices=[style.value for style in TradingStyle],
    )
    parser.add_argument("--setup", default="all", help="Setup family filter or 'all'.")
    parser.add_argument("--from-date", default=None, help="UTC start date, e.g. 2026-01-01.")
    parser.add_argument("--to-date", default=None, help="UTC end date, e.g. 2026-06-01.")
    parser.add_argument("--in-sample-days", type=int, default=45)
    parser.add_argument("--out-of-sample-days", type=int, default=15)
    parser.add_argument("--step-days", type=int, default=15)
    parser.add_argument(
        "--score-grid",
        default="0,55,60,65,70,75,80",
        help="Comma-separated min-score candidates.",
    )
    parser.add_argument("--min-in-sample-trades", type=int, default=5)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "reports"))
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Parallel worker processes for the walk-forward folds "
            "(default: all CPU cores; --jobs 1 uses the exact sequential path)."
        ),
    )

    portfolio_group = parser.add_mutually_exclusive_group()
    portfolio_group.add_argument(
        "--portfolio",
        dest="portfolio",
        action="store_true",
        default=True,
        help="Publish portfolio-constrained headline OOS metrics (default).",
    )
    portfolio_group.add_argument(
        "--no-portfolio",
        dest="portfolio",
        action="store_false",
        help="Publish candidate-level OOS metrics without multi-pair allocation.",
    )
    parser.add_argument("--max-concurrent-positions", type=int, default=3)
    parser.add_argument("--max-same-symbol-positions", type=int, default=1)
    parser.add_argument("--max-abs-currency-exposure", type=int, default=2)
    parser.add_argument("--daily-loss-limit-r", type=float, default=3.0)
    parser.add_argument(
        "--disable-currency-exposure-limit",
        action="store_true",
        help="Disable the signed per-currency exposure cap.",
    )
    parser.add_argument(
        "--disable-daily-loss-limit",
        action="store_true",
        help="Disable the realized daily-loss lockout.",
    )
    return parser


def _portfolio_constraints(args: argparse.Namespace) -> PortfolioConstraints:
    return PortfolioConstraints(
        max_concurrent_positions=args.max_concurrent_positions,
        max_same_symbol_positions=args.max_same_symbol_positions,
        max_abs_currency_exposure=(
            None
            if args.disable_currency_exposure_limit
            else args.max_abs_currency_exposure
        ),
        daily_loss_limit_r=(None if args.disable_daily_loss_limit else args.daily_loss_limit_r),
    )


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
