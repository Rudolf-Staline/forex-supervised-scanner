#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from app.reporting.mt5_symbol_mapping_audit import MappingAuditOptions, export_audit_csv, export_audit_json, run_mapping_audit


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only MT5 symbol mapping audit.")
    p.add_argument("--watchlist", default="multi_asset_demo")
    p.add_argument("--check-reports", action="store_true")
    p.add_argument("--check-static", action="store_true")
    p.add_argument("--export-json", action="store_true")
    p.add_argument("--export-csv", action="store_true")
    args = p.parse_args()

    report = run_mapping_audit(
        MappingAuditOptions(watchlist=args.watchlist, check_reports=bool(args.check_reports), check_static=bool(args.check_static))
    )

    if args.export_json:
        path = export_audit_json(report, Path("reports/mt5_symbol_mapping_audit.json"))
        print(f"export_json={path}")
    if args.export_csv:
        path = export_audit_csv(report, Path("reports/mt5_symbol_mapping_audit.csv"))
        print(f"export_csv={path}")

    print(f"mapping_status={report['mapping_status']}")
    print(report["safety_warning"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
