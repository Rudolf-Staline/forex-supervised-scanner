"""CLI for static command catalog generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ops.command_catalog import export_catalog_json, export_catalog_md, filter_entries, scan_commands


def main() -> None:
    parser = argparse.ArgumentParser(description="Build static catalog of scripts commands.")
    parser.add_argument("--export-json", action="store_true", help="Export reports/command_catalog.json")
    parser.add_argument("--export-md", action="store_true", help="Export reports/command_catalog.md")
    parser.add_argument("--category", default="all", choices=["all", "reports", "validation", "mt5", "paper", "ops"])
    parser.add_argument("--show-unsafe", action="store_true", help="Include UNKNOWN safety entries")
    args = parser.parse_args()

    entries = scan_commands(PROJECT_ROOT / "scripts")
    filtered = filter_entries(entries, args.category, args.show_unsafe)
    reports_dir = PROJECT_ROOT / "reports"

    if args.export_json:
        p = export_catalog_json(filtered, reports_dir)
        print(f"json_export={p}")
    if args.export_md:
        p = export_catalog_md(filtered, reports_dir)
        print(f"md_export={p}")

    print(f"commands_total={len(entries)}")
    print(f"commands_filtered={len(filtered)}")


if __name__ == "__main__":
    main()
