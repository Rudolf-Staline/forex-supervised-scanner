"""Report rejected, watchlist, detected, and near-miss demo-bot signals."""

from __future__ import annotations

import argparse
import csv
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

APPROVAL_SCORE = 75.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Report rejected and near-miss Forex Supervisor demo signals.")
    parser.add_argument("--export-csv", action="store_true", help="Export filtered near-miss records to reports/near_miss_signals.csv.")
    parser.add_argument("--min-score", type=float, default=55.0, help="Minimum score for near-miss score analysis.")
    parser.add_argument("--symbol", default=None, help="Filter by symbol, for example EUR/USD.")
    parser.add_argument("--setup", default=None, help="Filter by setup, for example ema50_pullback.")
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    records = Database(settings.database_absolute_path).load_rejected_signals()
    filtered = _filter_records(records, symbol=args.symbol, setup=args.setup)
    near_misses = _near_miss_records(filtered, min_score=args.min_score)
    _print_report(records, filtered, near_misses, min_score=args.min_score)
    if args.export_csv:
        output = PROJECT_ROOT / "reports" / "near_miss_signals.csv"
        _export_csv(near_misses, output)
        print(f"csv_export={output}")


def _filter_records(
    records: list[RejectedSignalRecord],
    *,
    symbol: str | None = None,
    setup: str | None = None,
) -> list[RejectedSignalRecord]:
    filtered = records
    if symbol:
        normalized = symbol.strip().upper()
        filtered = [record for record in filtered if record.symbol.upper() == normalized]
    if setup:
        filtered = [record for record in filtered if record.setup == setup]
    return filtered


def _near_miss_records(records: list[RejectedSignalRecord], *, min_score: float) -> list[RejectedSignalRecord]:
    near_misses: list[RejectedSignalRecord] = []
    for record in records:
        score = record.score or 0.0
        pattern_score = record.pattern_score or 0.0
        risk_reward = record.risk_reward or 0.0
        reasons = _normalized_reasons(record)
        if score >= min_score or pattern_score > 0.0 or (risk_reward >= 1.5 and _has_spread_atr_reason(reasons)):
            near_misses.append(record)
    return near_misses


def _print_report(
    all_records: list[RejectedSignalRecord],
    filtered_records: list[RejectedSignalRecord],
    near_misses: list[RejectedSignalRecord],
    *,
    min_score: float,
) -> None:
    print(f"total_rejected_signals={len(all_records)}")
    print(f"filtered_rejected_signals={len(filtered_records)}")
    print(f"near_miss_signals={len(near_misses)}")
    print(f"near_miss_definition=score>={min_score:.1f} OR pattern_score>0 OR rr>=1.5 rejected_for_spread_atr")
    _print_best_scores(filtered_records)
    _print_counter("best_symbols", _symbol_score_counter(filtered_records))
    _print_counter("best_setups", _setup_score_counter(filtered_records))
    _print_counter("frequent_patterns", Counter(pattern for record in filtered_records for pattern in record.detected_patterns))
    _print_counter("main_rejection_reasons", _reason_counter(filtered_records))
    close_to_threshold = [record for record in filtered_records if record.score is not None and min_score <= record.score < APPROVAL_SCORE]
    print(f"signals_close_to_75={len(close_to_threshold)}")
    print(f"rejected_only_by_spread_atr={len([record for record in filtered_records if _only_spread_atr(record)])}")
    print(f"rejected_only_by_score={len([record for record in filtered_records if _only_score(record)])}")


def _print_best_scores(records: list[RejectedSignalRecord]) -> None:
    print("best_scores_observed:")
    ranked = sorted([record for record in records if record.score is not None], key=lambda record: float(record.score or 0.0), reverse=True)[:10]
    if not ranked:
        print("- n/a")
        return
    for record in ranked:
        patterns = ",".join(record.detected_patterns) if record.detected_patterns else "-"
        print(
            f"- {record.symbol} setup={record.setup} status={record.status} score={record.score:.2f} "
            f"rr={_fmt(record.risk_reward)} pattern_score={record.pattern_score:.2f} patterns={patterns}"
        )


def _reason_counter(records: list[RejectedSignalRecord]) -> Counter[str]:
    return Counter(reason for record in records for reason in record.rejection_reasons)


def _symbol_score_counter(records: list[RejectedSignalRecord]) -> Counter[str]:
    best: dict[str, float] = {}
    for record in records:
        if record.score is not None:
            best[record.symbol] = max(best.get(record.symbol, 0.0), float(record.score))
    return Counter(dict(sorted(best.items(), key=lambda item: item[1], reverse=True)[:10]))


def _setup_score_counter(records: list[RejectedSignalRecord]) -> Counter[str]:
    best: dict[str, float] = {}
    for record in records:
        if record.score is not None:
            best[record.setup] = max(best.get(record.setup, 0.0), float(record.score))
    return Counter(dict(sorted(best.items(), key=lambda item: item[1], reverse=True)[:10]))


def _normalized_reasons(record: RejectedSignalRecord) -> list[str]:
    return [reason.lower() for reason in record.rejection_reasons]


def _has_spread_atr_reason(reasons: list[str]) -> bool:
    return any("spread/atr" in reason or "spread" in reason for reason in reasons)


def _has_score_reason(reasons: list[str]) -> bool:
    return any("score" in reason and "below" in reason for reason in reasons)


def _only_spread_atr(record: RejectedSignalRecord) -> bool:
    reasons = _normalized_reasons(record)
    return bool(reasons) and all("spread/atr" in reason or "spread" in reason for reason in reasons)


def _only_score(record: RejectedSignalRecord) -> bool:
    reasons = _normalized_reasons(record)
    return bool(reasons) and all(_has_score_reason([reason]) for reason in reasons)


def _export_csv(records: list[RejectedSignalRecord], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "cycle_id",
        "symbol",
        "setup",
        "status",
        "score",
        "risk_reward",
        "pattern_score",
        "detected_patterns",
        "spread_atr",
        "market_regime",
        "rejection_reasons",
        "entry",
        "stop_loss",
        "tp1",
        "tp2",
        "tp3",
        "provider",
        "broker",
        "style",
        "watchlist",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "timestamp": record.timestamp.isoformat(),
                    "cycle_id": record.cycle_id,
                    "symbol": record.symbol,
                    "setup": record.setup,
                    "status": record.status,
                    "score": record.score,
                    "risk_reward": record.risk_reward,
                    "pattern_score": record.pattern_score,
                    "detected_patterns": "; ".join(record.detected_patterns),
                    "spread_atr": record.spread_atr,
                    "market_regime": record.market_regime,
                    "rejection_reasons": "; ".join(record.rejection_reasons),
                    "entry": record.entry,
                    "stop_loss": record.stop_loss,
                    "tp1": record.tp1,
                    "tp2": record.tp2,
                    "tp3": record.tp3,
                    "provider": record.provider,
                    "broker": record.broker,
                    "style": record.style,
                    "watchlist": record.watchlist,
                }
            )


def _print_counter(title: str, counter: Counter[str]) -> None:
    print(f"{title}:")
    if not counter:
        print("- n/a")
        return
    for key, count in counter.most_common(10):
        print(f"- {key}: {count}")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


if __name__ == "__main__":
    main()
