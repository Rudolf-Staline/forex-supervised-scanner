"""Report rejected demo-bot signals for strategy diagnostics."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.execution.rejected_signals import RejectedSignalRecord
from app.storage.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Report rejected Forex Supervisor demo-bot signals.")
    parser.add_argument("--export-csv", action="store_true", help="Export rejected signals to reports/rejected_signals.csv.")
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    database = Database(settings.database_absolute_path)
    records = database.load_rejected_signals()
    _print_report(records)
    if args.export_csv:
        output = PROJECT_ROOT / "reports" / "rejected_signals.csv"
        _export_csv(records, output)
        print(f"csv_export={output}")


def _print_report(records: list[RejectedSignalRecord]) -> None:
    print(f"total_rejected_signals={len(records)}")
    if not records:
        print("No rejected signals recorded yet. Run a demo bot cycle first.")
        return
    reasons = Counter(reason for record in records for reason in record.rejection_reasons)
    symbols = Counter(record.symbol for record in records)
    setups = Counter(record.setup for record in records)
    scores = [record.score for record in records if record.score is not None]
    zero_score_records = [record for record in records if record.score == 0.0]
    nonzero_scores = [score for score in scores if score > 0.0]
    zero_reasons = Counter(reason for record in zero_score_records for reason in _zero_score_reason_labels(record))
    zero_symbols = Counter(record.symbol for record in zero_score_records)
    absent_or_invalid_setups = Counter(
        record.setup
        for record in zero_score_records
        if record.setup in {"none", ""}
        or any(
            reason in {
                "no_setup_detected",
                "missing_direction",
                "missing_entry",
                "missing_stop_loss",
                "missing_take_profit",
                "invalid_risk_reward",
                "data_quality_failed",
            }
            for reason in record.rejection_reasons
        )
    )
    close_to_threshold = sum(1 for score in scores if 70.0 <= score < 75.0)

    print(f"average_score={sum(scores) / len(scores):.2f}" if scores else "average_score=n/a")
    print(f"zero_score_rejected_signals={len(zero_score_records)}")
    print(f"average_nonzero_score={sum(nonzero_scores) / len(nonzero_scores):.2f}" if nonzero_scores else "average_nonzero_score=n/a")
    print(f"close_to_threshold_75={close_to_threshold}")
    _print_counter("top_zero_score_reasons", zero_reasons)
    _print_counter("zero_score_symbols", zero_symbols)
    _print_counter("absent_or_invalid_setups", absent_or_invalid_setups)
    _print_counter("top_rejection_reasons", reasons)
    _print_counter("top_rejected_symbols", symbols)
    _print_counter("top_rejected_setups", setups)


def _print_counter(title: str, counter: Counter[str]) -> None:
    print(f"{title}:")
    if not counter:
        print("- n/a")
        return
    for key, count in counter.most_common(10):
        print(f"- {key}: {count}")


def _zero_score_reason_labels(record: RejectedSignalRecord) -> list[str]:
    labels = set()
    if record.setup in {"none", ""}:
        labels.add("no_setup_detected")
    if record.entry is None:
        labels.add("missing_entry")
    if record.stop_loss is None:
        labels.add("missing_stop_loss")
    if record.tp1 is None and record.tp2 is None and record.tp3 is None:
        labels.add("missing_take_profit")
    if record.risk_reward is None or record.risk_reward <= 0.0:
        labels.add("invalid_risk_reward")
    for reason in record.rejection_reasons:
        if reason in {
            "no_setup_detected",
            "missing_direction",
            "missing_entry",
            "missing_stop_loss",
            "missing_take_profit",
            "invalid_risk_reward",
            "data_quality_failed",
        }:
            labels.add(reason)
        if "direction is not executable" in reason:
            labels.add("missing_direction")
        if "data quality" in reason and "below" in reason:
            labels.add("data_quality_failed")
    return sorted(labels) or list(record.rejection_reasons)


def _export_csv(records: list[RejectedSignalRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "cycle_id",
        "timestamp",
        "symbol",
        "setup",
        "status",
        "score",
        "risk_reward",
        "market_regime",
        "spread_atr",
        "rejection_reasons",
        "entry",
        "stop_loss",
        "tp1",
        "tp2",
        "tp3",
        "provider",
        "broker",
        "style",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = record.model_dump(mode="json")
            row["rejection_reasons"] = "; ".join(record.rejection_reasons)
            writer.writerow({field: row.get(field) for field in fields})


if __name__ == "__main__":
    main()
