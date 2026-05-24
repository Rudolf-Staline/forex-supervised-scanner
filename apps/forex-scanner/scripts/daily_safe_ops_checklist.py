"""CLI for daily safe operations checklist generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.ops.daily_checklist import (
    DailyChecklistOptions,
    GUIDANCE_BANNER,
    build_daily_checklist,
    export_daily_checklist_json,
    export_daily_checklist_md,
    export_daily_checklist_txt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily safe operations checklist.")
    parser.add_argument("--mode", choices=["paper", "mt5-readonly", "analysis-only"], default="paper")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-md", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    args = parser.parse_args()

    checklist = build_daily_checklist(DailyChecklistOptions(mode=args.mode))
    reports_dir = Path(args.reports_dir)

    print(GUIDANCE_BANNER)

    if args.export_json:
        print(f"json_export={export_daily_checklist_json(checklist, reports_dir)}")
    if args.export_md:
        print(f"md_export={export_daily_checklist_md(checklist, reports_dir)}")
    if args.export_txt:
        print(f"txt_export={export_daily_checklist_txt(checklist, reports_dir)}")


if __name__ == "__main__":
    main()
