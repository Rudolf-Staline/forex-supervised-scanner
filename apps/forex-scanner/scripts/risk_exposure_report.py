"""Risk exposure report (read-only, no execution)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.reporting.risk_exposure import analyze_risk_exposure, export_report_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only risk exposure report")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--symbol")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--top-n", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reports_dir = Path(args.reports_dir)
    summary = analyze_risk_exposure(
        reports_dir,
        asset_class=args.asset_class,
        symbol=args.symbol,
        top_n=args.top_n,
    )

    out_json = reports_dir / "risk_exposure_summary.json"
    out_csv = reports_dir / "risk_exposure_report.csv"

    if args.export_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if args.export_csv:
        export_report_csv(summary, out_csv)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
