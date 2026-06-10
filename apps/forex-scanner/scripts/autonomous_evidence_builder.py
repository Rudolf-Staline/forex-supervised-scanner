#!/usr/bin/env python3
"""Build Autonomous Readiness Gate evidence in cloud-safe read-only modes."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.execution.autonomous_recovery import (
    AutonomousRecoveryConfig,
    build_recovery_plan,
    export_autonomous_recovery_json,
    export_autonomous_recovery_txt,
)
from app.execution.autonomous_evidence import (
    AutonomousEvidenceConfig,
    AutonomousEvidenceFinalStatus,
    AutonomousEvidenceMode,
    build_evidence,
)
from app.storage.database import Database
from app.utils.logging import configure_logging

SAFETY_WARNING = (
    "safety=paper_demo_read_only live_execution_allowed=false "
    "broker_order_submission_allowed=false mt5_order_execution_allowed=false order_send_called=false"
)


def _install_safe_process_defaults() -> None:
    os.environ.setdefault("EXECUTION_MODE", "paper")
    os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
    os.environ.setdefault("BROKER_MODE", "paper")
    os.environ.setdefault("AUTO_BOT_ENABLED", "false")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build bounded read-only evidence for the Autonomous Readiness Gate.")
    parser.add_argument("--mode", default="read-only", choices=["dry-run", "read-only", "refresh"])
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--watchlist", default="multi_asset_demo")
    parser.add_argument("--symbols", nargs="*", default=[])
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--include-readiness", action="store_true")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--allow-subprocess", action="store_true")
    parser.add_argument("--plan-recovery-on-block", action="store_true")
    parser.add_argument("--export-recovery-json", action="store_true")
    parser.add_argument("--export-recovery-txt", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv()
    _install_safe_process_defaults()
    configure_logging()
    settings = load_settings().model_copy(deep=True)
    database = Database(settings.database_absolute_path)
    config = AutonomousEvidenceConfig(
        mode=args.mode,
        reports_dir=Path(args.reports_dir),
        watchlist=args.watchlist,
        symbols=args.symbols,
        asset_class=args.asset_class,
        include_readiness=args.include_readiness,
        export_json=args.export_json,
        export_txt=args.export_txt,
        fail_fast=args.fail_fast,
        allow_subprocess=args.allow_subprocess,
    )
    report = build_evidence(settings=settings, database=database, config=config)
    print(f"autonomous_evidence={report.final_status.value}")
    print(
        "tasks="
        f"total={report.tasks_total} passed={report.tasks_passed} warned={report.tasks_warned} "
        f"failed={report.tasks_failed} skipped={report.tasks_skipped}"
    )
    for result in report.task_results:
        print(f"task={result.task_name} status={result.status.value} blocking={str(result.blocking).lower()} reason={result.reason}")
    for path in report.output_paths:
        print(f"report={path}")
    print(f"readiness_re_evaluated={str(report.readiness_report is not None).lower()}")
    if report.readiness_report is not None:
        print(f"readiness={report.readiness_report.get('final_status')}")
    if args.plan_recovery_on_block and report.final_status == AutonomousEvidenceFinalStatus.BLOCKED_EVIDENCE:
        plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=Path(args.reports_dir)))
        print(f"recovery_plan={plan.final_status.value} causes={len(plan.causes)} actions={len(plan.actions)}")
        if args.export_recovery_json:
            print(f"recovery_json_export={export_autonomous_recovery_json(plan, Path(args.reports_dir))}")
        if args.export_recovery_txt:
            print(f"recovery_txt_export={export_autonomous_recovery_txt(plan, Path(args.reports_dir))}")
    print(SAFETY_WARNING)
    return 1 if report.final_status == AutonomousEvidenceFinalStatus.BLOCKED_EVIDENCE else 0


if __name__ == "__main__":
    raise SystemExit(main())
