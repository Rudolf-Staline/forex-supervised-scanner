#!/usr/bin/env python
"""Explain the last opportunity decision from local paper/demo reports.

Reads ``decision_trace.json`` if present and falls back to
``score_decomposition.json``, ``signal_journal.jsonl``, and
``autonomous_supervisor_summary.json``. It never crashes when
``decision_trace.json`` is missing. Read-only: no trading logic, no MT5, no
``order_send``, no ``.env`` mutation.
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
    build_last_decision,
    render_last_decision_txt,
)

DEFAULT_JSON = "last_decision_explanation.json"
DEFAULT_TXT = "last_decision_explanation.txt"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain the last opportunity decision, tolerating missing decision traces (read-only).")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing report artifacts")
    parser.add_argument("--export-json", action="store_true", help="Write reports/last_decision_explanation.json")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/last_decision_explanation.txt")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when no decision artifact is available")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    reports_dir = Path(args.reports_dir)
    decision = build_last_decision(reports_dir)

    if args.export_json:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / DEFAULT_JSON
        path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"json_report={path}")
    if args.export_txt:
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = reports_dir / DEFAULT_TXT
        path.write_text(render_last_decision_txt(decision), encoding="utf-8")
        print(f"txt_report={path}")

    print(render_last_decision_txt(decision))

    if args.strict and decision["source"] is None:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
