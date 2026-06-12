#!/usr/bin/env python
"""Read-only operator dashboard CLI for paper/demo report artifacts.

Reads existing files from ``reports/`` and summarizes the paper/demo system
state. It runs no trading logic, never calls MT5 or ``order_send``, never
mutates ``.env``, and works fully offline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.operator_dashboard import (
    STATUS_READY,
    STATUS_WARN,
    build_operator_dashboard,
    export_operator_dashboard_json,
    export_operator_dashboard_txt,
    render_operator_dashboard_txt,
)

SAFETY_BANNER = "SAFETY: operator dashboard is read-only and paper/demo only; no trading logic, no MT5, no order_send, no .env mutation."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize local paper/demo report artifacts (read-only)")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing report artifacts")
    parser.add_argument("--max-age-hours", type=float, default=24.0, help="Reports older than this are treated as stale")
    parser.add_argument("--export-json", action="store_true", help="Write reports/operator_dashboard_summary.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/operator_dashboard_report.txt")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero unless status is READY or WARN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    summary = build_operator_dashboard(reports_dir, max_age_hours=args.max_age_hours)

    if args.export_json:
        path = export_operator_dashboard_json(summary, reports_dir)
        print(f"json_report={path}")
    if args.export_txt:
        path = export_operator_dashboard_txt(summary, reports_dir)
        print(f"txt_report={path}")

    print(render_operator_dashboard_txt(summary))

    if args.strict and summary["final_operator_status"] not in {STATUS_READY, STATUS_WARN}:
        print(f"strict mode: failing because final_operator_status={summary['final_operator_status']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
