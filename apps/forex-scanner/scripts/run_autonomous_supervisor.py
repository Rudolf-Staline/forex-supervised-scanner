#!/usr/bin/env python3
"""Run Autonomous Supervisor v0 in foreground-only paper/demo mode."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import watchlist_names
from app.core.types import TradingStyle
from app.data.providers import build_provider
from app.execution.autonomous_supervisor import AutonomousSupervisorConfig, AutonomousSupervisorFinalStatus, AutonomousSupervisorService
from app.storage.database import Database
from app.utils.logging import configure_logging

SAFETY_BANNER = """
======================================================================
Autonomous Supervisor v0 — PAPER/DEMO ONLY
- Does not authorize live trading.
- Does not enable broker-live execution.
- Does not start a hidden daemon or unbounded loop.
- Uses bounded foreground cycles and paper/demo safety checks.
======================================================================
""".strip()

EXPECTED_STOP_STATUSES = {
    AutonomousSupervisorFinalStatus.COMPLETED,
    AutonomousSupervisorFinalStatus.STOPPED_BY_RISK,
    AutonomousSupervisorFinalStatus.STOPPED_BY_OPERATOR_CONTROL,
    AutonomousSupervisorFinalStatus.DRY_RUN,
    AutonomousSupervisorFinalStatus.BLOCKED_BY_SAFETY,
    AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS,
}


def _install_safe_process_defaults() -> None:
    # Cloud-safe diagnostics default to the project's paper/demo lock without mutating .env.
    os.environ.setdefault("EXECUTION_MODE", "paper")
    os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
    os.environ.setdefault("BROKER_MODE", "paper")
    os.environ.setdefault("AUTO_BOT_ENABLED", "false")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run bounded Autonomous Supervisor v0 for strictly paper/demo operation."
    )
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "yahoo", "auto"])
    parser.add_argument("--symbols", nargs="+", default=["EUR/USD", "GBP/USD", "USD/CHF"], help="Symbols to scan. Ignored when --watchlist is supplied.")
    parser.add_argument("--watchlist", choices=watchlist_names(), default=None, help="Named paper/demo watchlist to scan.")
    parser.add_argument("--once", action="store_true", help="Run one bounded cycle regardless of --max-cycles.")
    parser.add_argument("--max-cycles", "--cycles", dest="max_cycles", type=int, default=None, help="Maximum foreground cycles. Conservative default: 3. --cycles is a backward-compatible alias.")
    parser.add_argument("--interval-seconds", type=float, default=None, help="Seconds between cycles. Conservative default: 300.")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=None, help="Validate without creating paper orders. Conservative default: true.")
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=None, help="Explicitly enable bounded paper/demo cycles. Conservative default: false.")
    parser.add_argument("--export-json", action="store_true", help="Write reports/autonomous_supervisor_summary.json.")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/autonomous_supervisor_report.txt.")
    parser.add_argument("--skip-readiness-gate", action="store_true", help="Diagnostic-only: skip readiness gate for dry-run validation only; never enables paper or live execution.")
    parser.add_argument("--readiness-only", action="store_true", help="Build readiness report and exit without running supervisor cycles.")
    parser.add_argument("--export-readiness-json", action="store_true", help="Write reports/autonomous_readiness_report.json.")
    parser.add_argument("--export-readiness-txt", action="store_true", help="Write reports/autonomous_readiness_report.txt.")
    parser.add_argument("--no-export", action="store_true", help="Backward-compatible no-op alias; exports remain opt-in unless --export-json/--export-txt are provided.")
    parser.add_argument("--no-sleep", action="store_true", help="Backward-compatible alias that sets --interval-seconds 0 for bounded loops.")
    parser.add_argument("--reports-dir", default="reports", help="Report directory. Default: apps/forex-scanner/reports.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(SAFETY_BANNER)
    load_dotenv()
    _install_safe_process_defaults()
    configure_logging()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)
    config = AutonomousSupervisorConfig.from_environment(
        enabled=args.enabled,
        style=TradingStyle(args.style),
        symbols=args.symbols,
        watchlist=args.watchlist,
        max_cycles=1 if args.once else args.max_cycles,
        interval_seconds=0 if args.no_sleep else args.interval_seconds,
        dry_run=args.dry_run,
        export_json=False if args.no_export else args.export_json,
        export_txt=False if args.no_export else args.export_txt,
        reports_dir=Path(args.reports_dir),
        skip_readiness_gate=args.skip_readiness_gate,
        readiness_only=args.readiness_only,
        export_readiness_json=args.export_readiness_json,
        export_readiness_txt=args.export_readiness_txt,
    )
    service = AutonomousSupervisorService(settings, provider, database)
    result = service.run_once(config) if args.once else service.run_loop(config)
    print(
        "autonomous_supervisor="
        f"{result.final_status.value} run_id={result.run_id} cycles={result.cycle_count}/{config.max_cycles} "
        f"orders_created={result.orders_created} dry_run={str(result.dry_run).lower()} "
        f"stop_reason={result.stop_reason or '-'}"
    )
    print("safety=paper_demo_only live_execution_allowed=false broker_order_submission_allowed=false")
    if result.readiness_report is not None:
        print(
            "readiness="
            f"{result.readiness_report.final_status.value} "
            f"dry_run_allowed={str(result.readiness_report.dry_run_allowed).lower()} "
            f"paper_run_allowed={str(result.readiness_report.paper_run_allowed).lower()}"
        )
        for reason in result.readiness_report.blocking_reasons:
            print(f"readiness_block={reason}")
        for reason in result.readiness_report.warning_reasons:
            print(f"readiness_warning={reason}")
    for path in result.export_paths:
        print(f"report={path}")
    if result.final_status == AutonomousSupervisorFinalStatus.STOPPED_BY_FAILURES:
        return 1
    if result.final_status in EXPECTED_STOP_STATUSES:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
