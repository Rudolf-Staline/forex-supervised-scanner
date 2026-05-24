"""Generate backtest comparison report from existing files (informational-only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.backtest_comparison import WARNING_MESSAGE, compare_summaries, export_summary, load_records, summarize_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest/forward-test report comparator (informational-only).")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    reports_dir = PROJECT_ROOT / args.reports_dir
    baseline_path = Path(args.baseline) if args.baseline else reports_dir / "backtest_multi_asset_summary.json"
    candidate_path = Path(args.candidate) if args.candidate else reports_dir / "forward_test_summary.json"

    baseline_records = load_records(baseline_path)
    candidate_records = load_records(candidate_path)

    baseline = summarize_records(baseline_records, dataset_name=baseline_path.name)
    candidate = summarize_records(candidate_records, dataset_name=candidate_path.name)
    report = compare_summaries(baseline, candidate, top_n=args.top_n)

    print("backtest_comparison_report=informational_only")
    print(WARNING_MESSAGE)
    print(json.dumps(report, indent=2, sort_keys=True))

    if args.export_json or args.export_csv:
        export_summary(report, reports_dir, export_json=args.export_json, export_csv=args.export_csv)


if __name__ == "__main__":
    main()
