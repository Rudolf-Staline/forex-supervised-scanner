from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.reporting.watchlist_coverage import WatchlistCoverageOptions, build_watchlist_coverage_report, write_watchlist_coverage_csv


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a read-only watchlist coverage report from local report artifacts.")
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--watchlist", default="multi_asset_demo")
    p.add_argument("--asset-class", choices=["forex", "commodities", "indices", "all"], default="all")
    p.add_argument("--export-json", action="store_true")
    p.add_argument("--export-csv", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    reports_dir = Path(args.reports_dir)
    report = build_watchlist_coverage_report(
        WatchlistCoverageOptions(reports_dir=reports_dir, watchlist=args.watchlist, asset_class=args.asset_class)
    )
    print(json.dumps(report, indent=2))

    out_json = reports_dir / "watchlist_coverage_summary.json"
    out_csv = reports_dir / "watchlist_coverage_report.csv"
    if args.export_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.export_csv:
        write_watchlist_coverage_csv(report, out_csv)


if __name__ == "__main__":
    main()
