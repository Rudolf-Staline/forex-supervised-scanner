#!/usr/bin/env python
"""Analyze local paper/demo session history trends (offline only).

Reads ``reports/paper_session_history.jsonl`` and can export JSON/TXT trend
reports. No trading logic, no MT5, no order_send, no .env mutation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.paper_session_trends import (
    STATUS_EMPTY,
    STATUS_READY,
    STATUS_WARN,
    PaperSessionTrendsConfig,
    PaperSessionTrendsService,
    render_trends_txt,
)

SAFETY_BANNER = "SAFETY: paper session trends read existing history only; no trading logic, no MT5, no order_send, no .env mutation."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline paper/demo session trend analyzer")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing paper_session_history.jsonl")
    parser.add_argument("--window", type=int, default=10, help="Number of recent sessions to analyze")
    parser.add_argument("--export-json", action="store_true", help="Write reports/paper_session_trends_summary.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/paper_session_trends_report.txt")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero for missing/empty/blocked trend analysis")
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
        summary = PaperSessionTrendsService(config).run()
    except ValueError as error:
        print(f"error: {error}")
        return 2

    print(render_trends_txt(summary))
    if args.strict and summary["final_trends_status"] in {STATUS_EMPTY}:
        print(f"strict mode: failing because final_trends_status={summary['final_trends_status']}")
        return 1
    if args.strict and summary["final_trends_status"] not in {STATUS_READY, STATUS_WARN}:
        print(f"strict mode: failing because final_trends_status={summary['final_trends_status']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
