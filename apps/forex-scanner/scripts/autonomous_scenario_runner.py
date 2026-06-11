"""Autonomous Scenario Runner CLI (PAPER/DEMO ONLY)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.execution.autonomous_scenarios import (  # noqa: E402
    AutonomousScenarioConfig,
    AutonomousScenarioRunnerService,
    load_builtin_scenarios,
)

SAFETY_BANNER = """
=== Autonomous Scenario Runner (PAPER/DEMO/READ-ONLY ONLY) ===
This tool runs synthetic autonomy scenarios. It does NOT enable live trading.
No MT5 calls, no broker orders, no .env mutation, no daemon, no network required.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run autonomous policy/readiness scenario simulations.")
    parser.add_argument("--list", action="store_true", help="List built-in scenarios")
    parser.add_argument("--scenario", help="Run one scenario id")
    parser.add_argument("--all", action="store_true", help="Run all built-in scenarios")
    parser.add_argument("--reports-dir", default="reports", help="Directory for scenario reports")
    parser.add_argument("--export-json", action="store_true", help="Export suite JSON report")
    parser.add_argument("--export-txt", action="store_true", help="Export suite text report")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after first failed scenario")
    parser.add_argument("--strict", action="store_true", help="Treat expectation mismatches as FAIL instead of WARN")
    parser.add_argument("--include-policy-report", action="store_true", help="Embed policy decisions in suite JSON")
    parser.add_argument("--include-recovery-plan", action="store_true", help="Embed recovery plans and write recovery JSON files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(SAFETY_BANNER)
    scenarios = load_builtin_scenarios()

    if args.list:
        print(f"total scenarios: {len(scenarios)}")
        for scenario in scenarios:
            print(f"- {scenario.scenario_id}: {scenario.title} [{scenario.mode.value}] action={scenario.action}")
        return 0

    if args.scenario:
        scenarios = [scenario for scenario in scenarios if scenario.scenario_id == args.scenario]
        if not scenarios:
            print(f"unknown scenario id: {args.scenario}", file=sys.stderr)
            return 2
    elif not args.all:
        print("Choose --list, --scenario <id>, or --all.", file=sys.stderr)
        return 2

    reports_dir = Path(args.reports_dir)
    config = AutonomousScenarioConfig(
        reports_dir=reports_dir,
        strict=args.strict,
        include_policy_report=args.include_policy_report,
        include_recovery_plan=args.include_recovery_plan,
    )
    service = AutonomousScenarioRunnerService(config)
    suite = service.run_scenario_suite(scenarios, fail_fast=args.fail_fast)

    exported: list[Path] = []
    if args.export_json:
        exported.append(service.export_json(suite, reports_dir))
    if args.export_txt:
        exported.append(service.export_txt(suite, reports_dir))

    failed = [result.scenario_id for result in suite.scenario_results if result.status.value == "FAIL"]
    print(f"total scenarios: {suite.scenarios_total}")
    print(f"passed: {suite.scenarios_passed}")
    print(f"failed: {suite.scenarios_failed}")
    print(f"warned: {suite.scenarios_warned}")
    print(f"skipped: {suite.scenarios_skipped}")
    print(f"final_status: {suite.final_status.value}")
    print("failed scenario ids: " + (", ".join(failed) if failed else "none"))
    if exported:
        print("exported reports:")
        for path in exported:
            print(f"- {path}")
    print("live_execution_allowed=false")
    return 1 if suite.final_status.value == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
