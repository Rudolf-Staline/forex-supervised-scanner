"""Generate a read-only session health summary from local paper/report artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.reporting.session_health import (
    SAFETY_WARNING,
    build_session_health_summary,
    collect_session_health_records,
    export_session_health_csv,
    export_session_health_json,
)

REPORTS_DIR = Path("reports")
PATHS = {
    "signal_journal": REPORTS_DIR / "signal_journal.jsonl",
    "forward_test": REPORTS_DIR / "forward_test_paper.csv",
    "backtest_summary": REPORTS_DIR / "backtest_multi_asset_summary.json",
    "multi_asset_summary": REPORTS_DIR / "multi_asset_signal_report_summary.json",
}
EXPORT_JSON_PATH = REPORTS_DIR / "session_health_summary.json"
EXPORT_CSV_PATH = REPORTS_DIR / "session_health_summary.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only session health summary (no broker execution).")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--symbol")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = collect_session_health_records(PATHS, asset_class=args.asset_class, symbol=args.symbol)
    summary = build_session_health_summary(records, top_n=args.top_n)

    if args.export_json:
        export_session_health_json(summary, EXPORT_JSON_PATH)
    if args.export_csv:
        export_session_health_csv(summary, EXPORT_CSV_PATH)

    print(SAFETY_WARNING)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
