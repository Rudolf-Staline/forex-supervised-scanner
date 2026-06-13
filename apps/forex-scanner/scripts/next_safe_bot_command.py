#!/usr/bin/env python
"""Recommend exactly one safe, bounded next command for the paper/demo bot.

Read-only. Never recommends live trading, never runs trading logic, never calls
MT5 or ``order_send``, and never mutates ``.env``. It bases its single
recommendation on the available files in ``reports/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.operator_diagnostics import (
    SAFETY_BANNER,
    STATUS_BLOCKED,
    STATUS_STOP_AND_REVIEW,
    build_operator_diagnostics,
)

DEFAULT_JSON = "next_safe_bot_command.json"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print exactly one safe bounded next command for the paper/demo bot (read-only).")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing report artifacts")
    parser.add_argument("--max-age-hours", type=float, default=24.0, help="Reports older than this are treated as stale")
    parser.add_argument("--export-json", action="store_true", help="Write reports/next_safe_bot_command.json")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the recommendation is STOP_AND_REVIEW or a hard blocker exists")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    diag = build_operator_diagnostics(reports_dir, max_age_hours=args.max_age_hours)

    command = diag["next_safe_command"]
    reason = diag["next_safe_command_reason"]
    print(f"next_safe_command: {command}")
    print(f"reason: {reason}")

    if args.export_json:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / DEFAULT_JSON
        payload = {
            "generated_at": diag["generated_at"],
            "overall_status": diag["overall_status"],
            "next_safe_command": command,
            "next_safe_command_reason": reason,
            "primary_blocker": diag["primary_blocker"],
            "blocker_category": diag["blocker_category"],
            "confidence": diag["confidence"],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"json_report={path}")

    if args.strict and diag["overall_status"] in {STATUS_BLOCKED, STATUS_STOP_AND_REVIEW}:
        print(f"strict mode: failing because overall_status={diag['overall_status']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
