#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from app.safety.env_doctor import evaluate_environment, export_report, report_to_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safety environment diagnostic for forex scanner.")
    parser.add_argument("--mode", required=True, choices=["paper", "mt5-readonly", "mt5-demo-precheck"])
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = evaluate_environment(mode=args.mode)
    print(report_to_text(report), end="")

    paths = export_report(
        report=report,
        export_json=args.export_json,
        export_txt=args.export_txt,
        output_dir=Path("reports"),
    )
    for path in paths:
        print(f"Exported: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
