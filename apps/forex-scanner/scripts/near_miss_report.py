"""Report rejected signals that were close to demo-bot acceptance."""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.execution.rejected_signals import RejectedSignalRecord
from app.storage.database import Database

LOWER_NEAR_MISS_SCORE = 60.0
APPROVAL_SCORE = 75.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Report near-miss rejected Forex Supervisor signals.")
    parser.parse_args()

    load_dotenv()
    settings = load_settings()
    records = Database(settings.database_absolute_path).load_rejected_signals()
    near_misses = _near_miss_records(records)
    _print_report(near_misses)


def _near_miss_records(records: list[RejectedSignalRecord]) -> list[RejectedSignalRecord]:
    return [
        record
        for record in records
        if (record.score is not None and LOWER_NEAR_MISS_SCORE <= record.score < APPROVAL_SCORE)
        or (record.status == "watchlist" and record.score is not None and record.score >= LOWER_NEAR_MISS_SCORE - 5.0)
    ]


def _print_report(records: list[RejectedSignalRecord]) -> None:
    print(f"near_miss_total={len(records)}")
    if not records:
        print("No near-miss rejected signals found yet.")
        return
    score_band = [record for record in records if record.score is not None and LOWER_NEAR_MISS_SCORE <= record.score < APPROVAL_SCORE]
    watchlist_close = [record for record in records if record.status == "watchlist"]
    print(f"score_60_to_75={len(score_band)}")
    print(f"watchlist_close_to_approved={len(watchlist_close)}")
    _print_counter("main_blocking_reasons", _reason_counter(records))
    _print_counter("symbols_closest_to_threshold", _symbols_closest(records))
    _print_counter("promising_setups", Counter(record.setup for record in records))
    _print_average("average_score_by_setup", _average_by(records, lambda record: record.setup, lambda record: record.score))
    _print_average("average_spread_atr_by_symbol", _average_by(records, lambda record: record.symbol, lambda record: record.spread_atr))


def _reason_counter(records: list[RejectedSignalRecord]) -> Counter[str]:
    return Counter(reason for record in records for reason in record.rejection_reasons)


def _symbols_closest(records: list[RejectedSignalRecord]) -> Counter[str]:
    best_distance_by_symbol: dict[str, float] = {}
    for record in records:
        if record.score is None:
            continue
        distance = max(0.0, APPROVAL_SCORE - record.score)
        best_distance_by_symbol[record.symbol] = min(distance, best_distance_by_symbol.get(record.symbol, distance))
    ranked = sorted(best_distance_by_symbol.items(), key=lambda item: item[1])
    return Counter({symbol: round(APPROVAL_SCORE - distance, 2) for symbol, distance in ranked})


def _average_by(records: list[RejectedSignalRecord], key_fn, value_fn) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        value = value_fn(record)
        if value is None:
            continue
        grouped[key_fn(record)].append(float(value))
    return {key: round(sum(values) / len(values), 4) for key, values in grouped.items() if values}


def _print_counter(title: str, counter: Counter[str]) -> None:
    print(f"{title}:")
    if not counter:
        print("- n/a")
        return
    for key, count in counter.most_common(10):
        print(f"- {key}: {count}")


def _print_average(title: str, values: dict[str, float]) -> None:
    print(f"{title}:")
    if not values:
        print("- n/a")
        return
    for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True)[:10]:
        print(f"- {key}: {value:.4f}")


if __name__ == "__main__":
    main()
