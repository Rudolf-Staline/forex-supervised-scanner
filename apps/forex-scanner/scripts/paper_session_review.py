#!/usr/bin/env python
"""Run a read-only paper/demo post-session review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.paper_session_review import (
    STATUS_READY,
    STATUS_WARN,
    PaperSessionReviewConfig,
    PaperSessionReviewService,
    render_paper_session_review_txt,
)

SAFETY_BANNER = "SAFETY: paper session review is read-only and paper/demo only; no trading logic, no MT5, no order_send, no .env mutation."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an offline paper/demo post-session review")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing existing local report artifacts")
    parser.add_argument("--export-json", action="store_true", help="Write reports/paper_session_review_summary.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/paper_session_review_report.txt")
    parser.add_argument("--export-bundle", action="store_true", help="Also export a paper session bundle")
    parser.add_argument("--bundle-output-dir", default="reports/bundles", help="Bundle output directory when --export-bundle is used")
    parser.add_argument("--session-name", default="paper-session-review", help="Bundle session name when --export-bundle is used")
    parser.add_argument("--max-age-hours", type=float, default=24.0, help="Reports older than this are treated as stale")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero unless the review is READY or WARN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    bundle_output_dir = Path(args.bundle_output_dir)
    if not reports_dir.is_absolute():
        reports_dir = PROJECT_ROOT / reports_dir
    if not bundle_output_dir.is_absolute():
        bundle_output_dir = PROJECT_ROOT / bundle_output_dir

    summary = PaperSessionReviewService(
        PaperSessionReviewConfig(
            reports_dir=reports_dir,
            export_json=args.export_json,
            export_txt=args.export_txt,
            export_bundle=args.export_bundle,
            bundle_output_dir=bundle_output_dir,
            session_name=args.session_name,
            strict=args.strict,
            max_age_hours=args.max_age_hours,
        )
    ).build_summary()

    print(render_paper_session_review_txt(summary), end="")
    if args.strict and summary.final_review_status not in {STATUS_READY, STATUS_WARN}:
        print(f"strict mode: failing because final_review_status={summary.final_review_status}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
