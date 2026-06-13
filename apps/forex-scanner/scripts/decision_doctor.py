#!/usr/bin/env python
"""Read-only operator decision doctor for the paper/demo bot.

Runs no trading logic, never calls MT5 or ``order_send``, never mutates
``.env``, and works fully offline from files in ``reports/``. It answers, in one
command, whether the bot can run a paper/demo diagnostic, what is blocking it,
what is missing, and what the next safe bounded command is.
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
    STATUS_READY,
    STATUS_WARN,
    build_operator_diagnostics,
    render_decision_doctor_txt,
)

DEFAULT_JSON = "decision_doctor_summary.json"
DEFAULT_TXT = "decision_doctor_report.txt"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose paper/demo bot state and recommend the next safe command (read-only).")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing report artifacts")
    parser.add_argument("--max-age-hours", type=float, default=24.0, help="Reports older than this are treated as stale")
    parser.add_argument("--export-json", action="store_true", help="Write reports/decision_doctor_summary.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/decision_doctor_report.txt")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when a hard blocker exists")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    diag = build_operator_diagnostics(reports_dir, max_age_hours=args.max_age_hours)

    if args.export_json:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / DEFAULT_JSON
        path.write_text(json.dumps(diag, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"json_report={path}")
    if args.export_txt:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / DEFAULT_TXT
        path.write_text(render_decision_doctor_txt(diag), encoding="utf-8")
        print(f"txt_report={path}")

    print(render_decision_doctor_txt(diag))

    if args.strict and diag["overall_status"] not in {STATUS_READY, STATUS_WARN}:
        print(f"strict mode: failing because overall_status={diag['overall_status']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
