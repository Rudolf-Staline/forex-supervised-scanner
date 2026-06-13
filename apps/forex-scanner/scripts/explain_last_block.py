#!/usr/bin/env python
"""Explain the most recent hard blocker from local paper/demo reports.

Read-only. Never runs trading logic, never calls MT5 or ``order_send``, never
mutates ``.env``. Tolerates a missing reports directory and malformed files.
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
    build_last_block,
    build_operator_diagnostics,
    render_last_block_txt,
)

DEFAULT_JSON = "last_block_explanation.json"
DEFAULT_TXT = "last_block_explanation.txt"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain the latest hard blocker from available reports (read-only).")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing report artifacts")
    parser.add_argument("--max-age-hours", type=float, default=24.0, help="Reports older than this are treated as stale")
    parser.add_argument("--export-json", action="store_true", help="Write reports/last_block_explanation.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/last_block_explanation.txt")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when a hard blocker exists")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    diag = build_operator_diagnostics(reports_dir, max_age_hours=args.max_age_hours)
    block = build_last_block(diag)

    if args.export_json:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / DEFAULT_JSON
        path.write_text(json.dumps(block, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"json_report={path}")
    if args.export_txt:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / DEFAULT_TXT
        path.write_text(render_last_block_txt(block), encoding="utf-8")
        print(f"txt_report={path}")

    print(render_last_block_txt(block))

    if args.strict and block["has_block"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
