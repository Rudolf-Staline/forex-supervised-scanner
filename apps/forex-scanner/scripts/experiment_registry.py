"""CLI for local experiment registry (metadata only, no trading execution)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.experiments.experiment_registry import ALLOWED_STATUS, ExperimentRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local experiment registry")
    parser.add_argument("--create", action="store_true", help="Create experiment")
    parser.add_argument("--list", action="store_true", help="List experiments")
    parser.add_argument("--show", metavar="EXPERIMENT_ID", help="Show experiment by id")
    parser.add_argument("--name", default="", help="Experiment name")
    parser.add_argument("--description", default="", help="Experiment description")
    parser.add_argument("--tag", action="append", default=[], help="Tag (repeatable)")
    parser.add_argument("--status", choices=sorted(ALLOWED_STATUS), help="Set experiment status")
    parser.add_argument("--attach-report", metavar="PATH", help="Attach report path to experiment")
    parser.add_argument("--export-summary", action="store_true", help="Export summary file")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    registry = ExperimentRegistry(reports_dir=PROJECT_ROOT / "reports")

    if args.create:
        payload = registry.create_experiment(
            name=args.name,
            description=args.description,
            tags=args.tag,
            status=args.status,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.list:
        payload = registry.list_experiments()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.show:
        payload = registry.show_experiment(args.show)
        if args.attach_report:
            payload = registry.attach_report(args.show, args.attach_report)
        if args.status:
            payload = registry.set_status(args.show, args.status)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.export_summary:
        payload = registry.export_summary()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    raise SystemExit("No action selected. Use --create, --list, --show, or --export-summary")


if __name__ == "__main__":
    main()
