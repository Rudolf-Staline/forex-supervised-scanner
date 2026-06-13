#!/usr/bin/env python
"""Analyze local paper/demo session history trends."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.paper_session_trends import (
    STATUS_READY,
    STATUS_WARN,
    PaperSessionTrendsConfig,
    PaperSessionTrendsService,
    render_trends_txt,
)

SAFETY_BANNER = "SAFETY: paper session trends are read-only and paper/demo only; history analysis only."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze local paper/demo session history trends")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing paper_session_history.jsonl")
    parser.add_argument("--window", type=int, default=10, help="Number of most recent sessions to analyze")
    parser.add_argument("--export-json", action="store_true", help="Write reports/paper_session_trends_summary.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/paper_session_trends_report.txt")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero unless the trends status is READY or WARN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    if not reports_dir.is_absolute():
        reports_dir = PROJECT_ROOT / reports_dir

    try:
        config = PaperSessionTrendsConfig(
            reports_dir=reports_dir,
            window=args.window,
            export_json=args.export_json,
            export_txt=args.export_txt,
            strict=args.strict,
        )
    except ValueError as error:
        print(f"error: {error}")
        return 2

    try:
        summary = PaperSessionTrendsService(config).run()
    except ValueError as error:
        print(f"error: {error}")
        return 2

    print(render_trends_txt(summary))
    if args.strict and summary["final_trends_status"] not in {STATUS_READY, STATUS_WARN}:
        print(f"strict mode: failing because final_trends_status={summary['final_trends_status']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
