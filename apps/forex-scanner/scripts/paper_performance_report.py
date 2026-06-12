"""Export read-only paper/demo performance analytics from local reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.paper_performance import PaperPerformanceConfig, PaperPerformanceService, render_text_report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only paper/demo performance analytics from existing local report artifacts."
    )
    parser.add_argument("--reports-dir", default="reports", help="Directory containing existing local paper/demo reports.")
    parser.add_argument("--export-json", action="store_true", help="Write reports/paper_performance_summary.json.")
    parser.add_argument("--export-txt", action="store_true", help="Write reports/paper_performance_report.txt.")
    parser.add_argument("--strict", action="store_true", help="Treat missing/stale/incomplete inputs as incomplete-data status.")
    parser.add_argument("--export-csv", action="store_true", help=argparse.SUPPRESS)  # legacy no-op compatibility
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    if not reports_dir.is_absolute():
        reports_dir = PROJECT_ROOT / reports_dir
    summary = PaperPerformanceService(
        PaperPerformanceConfig(
            reports_dir=reports_dir,
            export_json=args.export_json,
            export_txt=args.export_txt,
            strict=args.strict,
        )
    ).build_summary()

    print(render_text_report(summary), end="")
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
