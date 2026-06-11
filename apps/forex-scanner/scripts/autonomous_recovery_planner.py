#!/usr/bin/env python3
"""Build a paper/demo-safe autonomous recovery plan."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config.env import load_dotenv
from app.execution.autonomous_recovery import (
    AutonomousRecoveryConfig,
    AutonomousRecoveryPlannerService,
    export_autonomous_recovery_json,
    export_autonomous_recovery_txt,
)
from app.utils.logging import configure_logging

SAFETY_WARNING = (
    "safety=paper_demo_recovery_plan_only live_execution_allowed=false "
    "broker_order_submission_allowed=false mt5_order_execution_allowed=false "
    "readiness_bypass_allowed=false"
)


def _install_safe_process_defaults() -> None:
    os.environ.setdefault("EXECUTION_MODE", "paper")
    os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
    os.environ.setdefault("BROKER_MODE", "paper")
    os.environ.setdefault("AUTO_BOT_ENABLED", "false")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a bounded recovery plan for blocked readiness/evidence reports.")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--export-json", action="store_true", help="Write reports/autonomous_recovery_plan.json.")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/autonomous_recovery_plan.txt.")
    parser.add_argument("--execute-safe-actions", action="store_true", help="Execute only allow-listed dry-run/read-only actions.")
    parser.add_argument("--max-actions", type=int, default=None)
    parser.add_argument("--include-manual-actions", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="When executing, mark safe actions as simulated instead of launching commands.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv()
    _install_safe_process_defaults()
    configure_logging()
    config = AutonomousRecoveryConfig(
        reports_dir=Path(args.reports_dir),
        max_actions=args.max_actions,
        include_manual_actions=args.include_manual_actions,
        execute_safe_actions=args.execute_safe_actions,
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
    )
    service = AutonomousRecoveryPlannerService()
    plan = service.build_plan(config)
    if args.execute_safe_actions:
        plan = service.execute_plan(plan, config)
    print(SAFETY_WARNING)
    print(f"autonomous_recovery={plan.final_status.value}")
    print(f"causes={len(plan.causes)} safe_actions={len(plan.safe_actions)} manual_actions={len(plan.manual_actions)}")
    for cause in plan.causes:
        print(f"cause={cause.cause_type.value} source={cause.source_report} severity={cause.severity.value} reason={cause.reason}")
    for action in plan.actions:
        print(
            f"action={action.action_id.value} mode={action.execution_mode.value} "
            f"safe={str(action.safe_to_execute_automatically).lower()} status={action.execution_status.value}"
        )
    if plan.next_recommended_command:
        print(f"next_recommended_command={plan.next_recommended_command}")
    if args.export_json:
        print(f"json_export={export_autonomous_recovery_json(plan, config.reports_dir)}")
    if args.export_txt:
        print(f"txt_export={export_autonomous_recovery_txt(plan, config.reports_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
