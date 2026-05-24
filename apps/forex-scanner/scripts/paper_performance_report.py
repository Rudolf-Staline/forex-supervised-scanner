"""Generate paper trading performance report. Informational-only, no orders sent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.paper_performance import WARNING_MESSAGE, build_paper_performance_report, export_summary, load_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper performance analyzer (informational-only).")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--asset-class", choices=["forex", "commodities", "indices", "all"], default="all")
    parser.add_argument("--symbol")
    parser.add_argument("--session")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    reports_dir = PROJECT_ROOT / args.reports_dir
    records = load_records(reports_dir)
    report = build_paper_performance_report(
        records, asset_class=args.asset_class, symbol=args.symbol, session=args.session, top_n=args.top_n
    )

    print("paper_performance_report=informational_only")
    print(WARNING_MESSAGE)
    print(json.dumps(report, indent=2, sort_keys=True))

    if args.export_json or args.export_csv:
        export_summary(report, reports_dir, export_csv=args.export_csv)


if __name__ == "__main__":
    main()
