"""Build a local decision-level calibration dataset. Reporting only; no orders are sent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.reporting.decision_dataset import (
    DecisionDatasetConfig,
    build_decision_dataset,
    write_decision_dataset,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build one leakage-aware row per scanner/demo decision and attach "
            "paper outcomes only when the match is unique."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite database path. Defaults to the configured scanner database.",
    )
    parser.add_argument(
        "--signal-journal",
        type=Path,
        default=PROJECT_ROOT / "reports" / "signal_journal.jsonl",
        help="Optional demo-bot signal journal JSONL used for cycle/order identifiers.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "decision_calibration",
    )
    parser.add_argument(
        "--match-window-seconds",
        type=int,
        default=300,
        help="Maximum temporal fallback distance after exact identifiers are exhausted.",
    )
    parser.add_argument(
        "--exclude-no-trade",
        action="store_true",
        help="Exclude no-raw-setup rows from the complete dataset.",
    )
    parser.add_argument(
        "--no-supplemental-journal-rows",
        action="store_true",
        help=(
            "Do not retain signal-journal decisions that cannot be uniquely enriched "
            "with a scan_results row."
        ),
    )
    args = parser.parse_args()

    settings = load_settings()
    database_path = args.database or settings.database_absolute_path
    journal_path = args.signal_journal if args.signal_journal.is_file() else None
    config = DecisionDatasetConfig(
        match_window_seconds=args.match_window_seconds,
        include_no_trade=not args.exclude_no_trade,
        include_supplemental_journal_rows=not args.no_supplemental_journal_rows,
    )
    dataset = build_decision_dataset(
        database_path,
        signal_journal_path=journal_path,
        config=config,
    )
    outputs = write_decision_dataset(dataset, args.output_dir)

    print("decision_calibration_dataset paper_only=true live_trading=false")
    print(f"database={database_path}")
    print(f"signal_journal={journal_path or 'not_found'}")
    print(f"total_decisions={dataset.summary['total_decisions']}")
    print(f"trainable_candidates={dataset.summary['trainable_candidates']}")
    print(f"return_labeled_rows={dataset.summary['return_labeled_rows']}")
    print(f"unmatched_paper_orders={dataset.summary['unmatched_paper_orders']}")
    print(f"ambiguous_order_matches={dataset.summary['ambiguous_order_matches']}")
    for label, path in outputs.items():
        print(f"{label}_export={path}")


if __name__ == "__main__":
    main()
