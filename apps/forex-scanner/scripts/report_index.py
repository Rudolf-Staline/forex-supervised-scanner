"""CLI for generating a central index of existing reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.report_index import (
    ReportIndexOptions,
    build_report_index,
    export_report_index_json,
    export_report_index_txt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build central index of generated reports.")
    parser.add_argument("--reports-dir", default="reports", help="Reports directory (default: reports)")
    parser.add_argument("--export-json", action="store_true", help="Export reports/report_index.json")
    parser.add_argument("--export-txt", action="store_true", help="Export reports/report_index.txt")
    parser.add_argument("--show-missing", action="store_true", help="Include missing reports")
    parser.add_argument("--show-stale", action="store_true", help="Include stale reports")
    parser.add_argument("--max-age-hours", type=int, default=48, help="Stale threshold in hours")
    args = parser.parse_args()

    options = ReportIndexOptions(
        reports_dir=PROJECT_ROOT / args.reports_dir,
        show_missing=args.show_missing,
        show_stale=args.show_stale,
        max_age_hours=args.max_age_hours,
    )
    index_payload = build_report_index(options)

    if args.export_json:
        json_path = export_report_index_json(index_payload, options.reports_dir)
        print(f"json_export={json_path}")
    if args.export_txt:
        txt_path = export_report_index_txt(index_payload, options.reports_dir)
        print(f"txt_export={txt_path}")

    print(f"reports_found={len(index_payload['reports_found'])}")
    print(f"reports_missing={len(index_payload['reports_missing'])}")
    print(f"stale_reports={len(index_payload['stale_reports'])}")


if __name__ == "__main__":
    main()
