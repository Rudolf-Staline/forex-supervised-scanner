from __future__ import annotations

import argparse

from app.ops.runbook_generator import generate_runbook, write_runbook_reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local operational runbook")
    parser.add_argument("--mode", choices=["paper", "mt5-readonly", "forward-test"], default="paper")
    parser.add_argument("--export-md", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    doc = generate_runbook(args.mode)
    written = write_runbook_reports(doc, export_md=args.export_md, export_txt=args.export_txt)
    for path in written:
        print(f"generated: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
