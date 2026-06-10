#!/usr/bin/env python3
"""Run Autonomous Supervisor v0 in foreground-only paper/demo mode."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import watchlist_names
from app.core.types import TradingStyle
from app.data.providers import build_provider
from app.storage.database import Database
from app.supervisor.autonomous import AutonomousSupervisorConfig, AutonomousSupervisorService
from app.utils.logging import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded Autonomous Supervisor v0 session in paper/demo mode only. "
            "This command never starts a daemon, never enables live trading, and never submits broker orders."
        )
    )
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "yahoo", "auto"])
    parser.add_argument("--symbols", nargs="+", default=["EUR/USD", "GBP/USD", "USD/CHF"], help="Symbols to scan. Ignored when --watchlist is supplied.")
    parser.add_argument("--watchlist", choices=watchlist_names(), default=None, help="Named paper/demo watchlist to scan.")
    parser.add_argument("--cycles", type=int, default=1, help="Bounded foreground cycle count. Default: 1.")
    parser.add_argument("--interval-seconds", type=int, default=0, help="Foreground sleep between cycles when --cycles > 1. Default: 0.")
    parser.add_argument("--no-sleep", action="store_true", help="Do not sleep between bounded cycles.")
    parser.add_argument("--reports-dir", default="reports", help="Directory for autonomous supervisor reports.")
    parser.add_argument("--no-export", action="store_true", help="Print only; do not write report artifacts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv()
    configure_logging()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)
    config = AutonomousSupervisorConfig(
        style=TradingStyle(args.style),
        symbols=args.symbols,
        watchlist=args.watchlist,
        cycles=args.cycles,
        interval_seconds=args.interval_seconds,
        sleep_between_cycles=not args.no_sleep,
        export_reports=not args.no_export,
        reports_dir=Path(args.reports_dir),
    )
    result = AutonomousSupervisorService(settings, provider, database).run(config)
    print(
        "autonomous_supervisor="
        f"{result.status} run_id={result.run_id} safety_status={result.safety_status} "
        f"cycles={result.completed_cycles}/{result.requested_cycles} "
        f"paper_orders_created={result.total_paper_orders_created} "
        "broker_mode=paper live_trading_enabled=false mt5_called=false broker_orders_sent=false hidden_daemon_created=false"
    )
    for path in result.report_paths:
        print(f"report={path}")
    for reason in result.blocking_reasons:
        print(f"blocking_reason={reason}")
    return 0 if result.status != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
