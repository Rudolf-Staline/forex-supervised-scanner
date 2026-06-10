"""Autonomous Policy Report — evaluate policy decisions (PAPER/DEMO ONLY).

SAFETY: This script does NOT enable live trading.
It does not call MT5, does not submit orders, does not mutate .env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path  # noqa: E402

from app.execution.autonomous_policy import (  # noqa: E402
    AutonomousPolicyConfig,
    AutonomousPolicyContext,
    AutonomousPolicyDecisionType,
    AutonomousPolicyEngine,
    AutonomousPolicyMode,
    export_autonomous_policy_json,
    export_autonomous_policy_txt,
)

SAFETY_BANNER = """
=== Autonomous Policy Report (PAPER/DEMO ONLY) ===
This script evaluates policy decisions. It does NOT enable live trading.
No MT5 calls, no broker orders, no .env mutation, no daemon.
"""

MODE_MAP = {
    "dry-run": AutonomousPolicyMode.DRY_RUN,
    "read-only": AutonomousPolicyMode.READ_ONLY,
    "paper": AutonomousPolicyMode.PAPER,
    "diagnostic": AutonomousPolicyMode.DIAGNOSTIC,
}


def _read_status_from_report(reports_dir: Path, filename: str, key: str) -> str:
    """Attempt to read a status value from a local JSON report."""

    path = reports_dir / filename
    if not path.exists():
        return "UNKNOWN"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return str(payload.get(key) or "UNKNOWN").upper()
    except (OSError, json.JSONDecodeError):
        pass
    return "UNKNOWN"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate autonomous policy decisions (PAPER/DEMO ONLY).",
    )
    parser.add_argument(
        "--action",
        required=True,
        help="Action to evaluate (build_evidence, run_readiness, execute_recovery_action, "
             "run_supervisor, run_supervisor_cycle, skip_readiness_gate)",
    )
    parser.add_argument(
        "--mode",
        default="dry-run",
        choices=["dry-run", "read-only", "paper", "diagnostic"],
        help="Policy mode (default: dry-run)",
    )
    parser.add_argument("--reports-dir", default="reports", help="Reports directory")
    parser.add_argument("--export-json", action="store_true", help="Export JSON report")
    parser.add_argument("--export-txt", action="store_true", help="Export TXT report")
    parser.add_argument(
        "--include-current-state",
        action="store_true",
        help="Read existing report files to populate context",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Dry-run mode (default: true)",
    )
    parser.add_argument("--operator-mode", default="normal", help="Operator mode")
    parser.add_argument("--readiness-status", default="UNKNOWN", help="Readiness status override")
    parser.add_argument("--evidence-status", default="UNKNOWN", help="Evidence status override")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(SAFETY_BANNER)

    mode = MODE_MAP[args.mode]
    reports_dir = Path(args.reports_dir)

    readiness_status = args.readiness_status
    evidence_status = args.evidence_status

    if args.include_current_state:
        if readiness_status == "UNKNOWN":
            readiness_status = _read_status_from_report(
                reports_dir, "autonomous_readiness_report.json", "final_status",
            )
        if evidence_status == "UNKNOWN":
            evidence_status = _read_status_from_report(
                reports_dir, "autonomous_evidence_summary.json", "final_status",
            )

    config = AutonomousPolicyConfig(
        mode=mode,
        dry_run=args.dry_run,
        operator_mode=args.operator_mode,
        readiness_status=readiness_status,
        evidence_status=evidence_status,
    )
    engine = AutonomousPolicyEngine(config)

    context = AutonomousPolicyContext(
        action=args.action,
        mode=mode,
        dry_run=args.dry_run,
        operator_mode=args.operator_mode,
        readiness_status=readiness_status,
        evidence_status=evidence_status,
    )
    decision = engine.evaluate_action(args.action, context)

    print(f"action: {decision.action}")
    print(f"mode: {decision.mode.value}")
    print(f"decision: {decision.decision.value}")
    print(f"allowed: {str(decision.allowed).lower()}")
    print("reasons:")
    for reason in decision.reasons:
        print(f"  - {reason}")
    if decision.warnings:
        print("warnings:")
        for warning in decision.warnings:
            print(f"  - {warning}")
    if decision.blocking_reasons:
        print("blocking_reasons:")
        for blocking in decision.blocking_reasons:
            print(f"  - {blocking}")
    if decision.recommended_next_action:
        print(f"recommended_next_action: {decision.recommended_next_action}")

    print()
    print("safety_flags:")
    for key, value in decision.safety_flags.items():
        print(f"  - {key}: {value}")

    export_paths: list[str] = []
    if args.export_json:
        path = export_autonomous_policy_json(decision, reports_dir)
        export_paths.append(str(path))
    if args.export_txt:
        path = export_autonomous_policy_txt(decision, reports_dir)
        export_paths.append(str(path))

    if export_paths:
        print()
        print("export_paths:")
        for path in export_paths:
            print(f"  - {path}")

    print()
    print("live_execution_allowed=false")

    if decision.decision == AutonomousPolicyDecisionType.DENY:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
