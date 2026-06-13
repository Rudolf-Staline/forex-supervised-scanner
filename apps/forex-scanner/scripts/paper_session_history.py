#!/usr/bin/env python
"""Maintain the local paper/demo session history ledger (read-only inputs).

Appends compact snapshots of completed paper/demo session reviews to
``reports/paper_session_history.jsonl`` and exports aggregate JSON/TXT
reports. No trading logic, no MT5, no order_send, no .env mutation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.paper_session_history import (
    STATUS_READY,
    STATUS_WARN,
    PaperSessionHistoryConfig,
    PaperSessionHistoryService,
    render_history_txt,
)

SAFETY_BANNER = "SAFETY: paper session history is read-only and paper/demo only; no trading logic, no MT5, no order_send, no .env mutation."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local paper/demo session history ledger (offline)")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing report artifacts and the history ledger")
    parser.add_argument("--append-latest", action="store_true", help="Append the latest paper_session_review_summary.json snapshot to the ledger")
    parser.add_argument("--session-name", default="paper-session-review", help="Session name recorded with the appended entry")
    parser.add_argument("--export-json", action="store_true", help="Write reports/paper_session_history_summary.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/paper_session_history_report.txt")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero unless the history status is READY or WARN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    if not reports_dir.is_absolute():
        reports_dir = PROJECT_ROOT / reports_dir

    try:
        config = PaperSessionHistoryConfig(
            reports_dir=reports_dir,
            append_latest=args.append_latest,
            session_name=args.session_name,
            export_json=args.export_json,
            export_txt=args.export_txt,
            strict=args.strict,
        )
    except ValueError as error:
        print(f"error: {error}")
        return 2

    try:
        summary = PaperSessionHistoryService(config).run()
    except ValueError as error:
        print(f"error: {error}")
        return 2
    print(render_history_txt(summary))

    if args.strict and summary["final_history_status"] not in {STATUS_READY, STATUS_WARN}:
        print(f"strict mode: failing because final_history_status={summary['final_history_status']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
