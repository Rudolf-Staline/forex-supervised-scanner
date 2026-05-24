"""CLI to aggregate demo-readiness from existing reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.reporting.demo_readiness import (
    DemoReadinessOptions,
    build_demo_readiness_summary,
    export_demo_readiness_json,
    export_demo_readiness_txt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build demo readiness aggregation report.")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    summary = build_demo_readiness_summary(DemoReadinessOptions(reports_dir=reports_dir, strict=args.strict))

    print(f"final_status={summary['final_status']}")
    print(summary["execution_authorization"])

    if args.export_json:
        json_path = export_demo_readiness_json(summary, reports_dir)
        print(f"json_export={json_path}")
    if args.export_txt:
        txt_path = export_demo_readiness_txt(summary, reports_dir)
        print(f"txt_export={txt_path}")


if __name__ == "__main__":
    main()
