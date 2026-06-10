#!/usr/bin/env python3
"""Build the read-only Autonomous Readiness Gate report."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.execution.autonomous_readiness import (
    AutonomousReadinessConfig,
    build_readiness_report,
    export_autonomous_readiness_json,
    export_autonomous_readiness_txt,
)
from app.storage.database import Database
from app.utils.logging import configure_logging


def _install_safe_process_defaults() -> None:
    # Cloud-safe diagnostics default to the project's paper/demo lock without mutating .env.
    os.environ.setdefault("EXECUTION_MODE", "paper")
    os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
    os.environ.setdefault("BROKER_MODE", "paper")
    os.environ.setdefault("AUTO_BOT_ENABLED", "false")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Autonomous Readiness Gate report without MT5 or order execution.")
    parser.add_argument("--reports-dir", default="reports", help="Report directory. Default: reports.")
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True, help="Evaluate dry-run allowance. Default: true.")
    parser.add_argument("--export-json", action="store_true", help="Write reports/autonomous_readiness_report.json.")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/autonomous_readiness_report.txt.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv()
    _install_safe_process_defaults()
    configure_logging()
    settings = load_settings().model_copy(deep=True)
    database = Database(settings.database_absolute_path)
    config = AutonomousReadinessConfig.from_environment(reports_dir=Path(args.reports_dir), dry_run=args.dry_run)
    report = build_readiness_report(settings, database, config)
    print(f"autonomous_readiness={report.final_status.value}")
    print(f"dry_run_allowed={str(report.dry_run_allowed).lower()} paper_run_allowed={str(report.paper_run_allowed).lower()}")
    for reason in report.blocking_reasons:
        print(f"block={reason}")
    for reason in report.warning_reasons:
        print(f"warning={reason}")
    if args.export_json:
        print(f"json_export={export_autonomous_readiness_json(report, config.reports_dir)}")
    if args.export_txt:
        print(f"txt_export={export_autonomous_readiness_txt(report, config.reports_dir)}")
    return 0 if report.dry_run_allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
