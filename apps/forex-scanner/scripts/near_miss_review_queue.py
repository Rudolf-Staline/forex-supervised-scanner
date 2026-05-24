"""Generate near-miss review queue (informational-only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.near_miss_queue import WARNING_MESSAGE, build_near_miss_review_queue, export_summary, load_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Near-miss review queue (informational-only).")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--asset-class", choices=["forex", "commodities", "indices", "all"], default="all")
    parser.add_argument("--symbol")
    parser.add_argument("--session")
    parser.add_argument("--min-score", type=float, default=65)
    parser.add_argument("--top-n", type=int, default=25)
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    args = parser.parse_args()

    reports_dir = PROJECT_ROOT / args.reports_dir
    records = load_records(reports_dir)
    report = build_near_miss_review_queue(
        records,
        asset_class=args.asset_class,
        symbol=args.symbol,
        session=args.session,
        min_score=args.min_score,
        top_n=args.top_n,
    )

    print("near_miss_review_queue=informational_only")
    print(WARNING_MESSAGE)
    print(json.dumps(report, indent=2, sort_keys=True))

    if args.export_json or args.export_csv:
        export_summary(report, reports_dir, export_json=args.export_json, export_csv=args.export_csv)


if __name__ == "__main__":
    main()
