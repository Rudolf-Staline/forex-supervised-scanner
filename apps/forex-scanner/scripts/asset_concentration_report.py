from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.reporting.asset_concentration import AssetConcentrationOptions, build_asset_concentration_report, write_asset_concentration_csv


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build an informational concentration report by asset/symbol/session.")
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--asset-class", choices=["forex", "commodities", "indices", "all"], default="all")
    p.add_argument("--export-json", action="store_true")
    p.add_argument("--export-csv", action="store_true")
    p.add_argument("--top-n", type=int, default=10)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    reports_dir = Path(args.reports_dir)
    report = build_asset_concentration_report(
        AssetConcentrationOptions(reports_dir=reports_dir, asset_class=args.asset_class, top_n=max(args.top_n, 1))
    )
    print(json.dumps(report, indent=2))

    json_out = reports_dir / "asset_concentration_summary.json"
    csv_out = reports_dir / "asset_concentration_report.csv"
    if args.export_json:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.export_csv:
        write_asset_concentration_csv(report, csv_out)


if __name__ == "__main__":
    main()
