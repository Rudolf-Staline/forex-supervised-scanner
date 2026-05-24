from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.reporting.data_health import DataHealthOptions, build_data_health_report, render_text_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a read-only data health report from reports directory.")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--max-age-hours", type=int, default=48)
    parser.add_argument("--min-records", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reports_dir = Path(args.reports_dir)
    report = build_data_health_report(
        DataHealthOptions(reports_dir=reports_dir, max_age_hours=args.max_age_hours, min_records=args.min_records)
    )
    text = render_text_report(report)
    print(text, end="")

    if args.export_json:
        out_json = reports_dir / "data_health_report.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.export_txt:
        out_txt = reports_dir / "data_health_report.txt"
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        out_txt.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
