"""Summarize the local decision audit journal without placing orders."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.instruments import filter_symbols_by_asset_class
from app.config.watchlists import get_watchlist
from app.journal.trade_journal import FIELDNAMES, TRADE_JOURNAL_PATH, load_trade_journal

EXPORT_PATH = PROJECT_ROOT / "reports" / "trade_journal_filtered.csv"


def main() -> None:
    """Print a compact summary of bot decisions."""

    parser = argparse.ArgumentParser(description="Summarize reports/trade_journal.csv. No orders are sent.")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--export-csv", action="store_true")
    args = parser.parse_args()

    rows = filter_journal_rows(
        load_trade_journal(),
        asset_class=args.asset_class,
        symbol=args.symbol,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    summary = summarize_journal(rows)
    print_summary(summary)
    if args.export_csv:
        export_journal_rows(rows, EXPORT_PATH)
        print(f"csv_export={EXPORT_PATH}")


def filter_journal_rows(
    rows: list[dict[str, str]],
    *,
    asset_class: str = "all",
    symbol: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, str]]:
    """Filter journal rows for reporting."""

    filtered = rows
    if asset_class != "all":
        allowed = set(filter_symbols_by_asset_class([row.get("logical_symbol", "") for row in filtered], asset_class))
        filtered = [row for row in filtered if row.get("logical_symbol") in allowed or row.get("asset_class") == asset_class]
    if symbol:
        wanted = symbol.strip().upper()
        filtered = [row for row in filtered if row.get("logical_symbol", "").upper() == wanted]
    if from_date:
        start = _parse_date(from_date)
        filtered = [row for row in filtered if _row_time(row) >= start]
    if to_date:
        end = _parse_date(to_date)
        filtered = [row for row in filtered if _row_time(row) <= end]
    return filtered


def summarize_journal(rows: list[dict[str, str]]) -> dict:
    """Build summary metrics for journal rows."""

    accepted = [row for row in rows if row.get("decision") == "ACCEPT"]
    rejected = [row for row in rows if row.get("decision") == "REJECT"]
    watchlist = [row for row in rows if row.get("status") == "watchlist"]
    near_miss = [row for row in rows if _is_near_miss(row)]
    return {
        "total_decisions": len(rows),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "watchlist": len(watchlist),
        "near_miss": len(near_miss),
        "best_symbols": _rank_by_average_score(rows, "logical_symbol", reverse=True),
        "worst_symbols": _rank_by_average_score(rows, "logical_symbol", reverse=False),
        "most_common_rejection_reasons": Counter(reason for row in rows for reason in _split(row.get("rejection_reasons", ""))),
        "best_sessions": _rank_by_average_score(rows, "session_name", reverse=True),
        "best_setups": _rank_by_average_score(rows, "setup", reverse=True),
    }


def print_summary(summary: dict) -> None:
    """Print journal summary."""

    print("journal_summary=no_orders")
    print(f"total_decisions={summary['total_decisions']}")
    print(f"accepted={summary['accepted']}")
    print(f"rejected={summary['rejected']}")
    print(f"watchlist={summary['watchlist']}")
    print(f"near_miss={summary['near_miss']}")
    _print_rank("best_symbols", summary["best_symbols"])
    _print_rank("worst_symbols", summary["worst_symbols"])
    _print_counter("most_common_rejection_reasons", summary["most_common_rejection_reasons"])
    _print_rank("best_sessions", summary["best_sessions"])
    _print_rank("best_setups", summary["best_setups"])


def export_journal_rows(rows: list[dict[str, str]], path: Path = EXPORT_PATH) -> None:
    """Export filtered journal rows."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def _rank_by_average_score(rows: list[dict[str, str]], key: str, *, reverse: bool) -> list[tuple[str, float]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(key) or "unknown"
        score = _float_or_none(row.get("score"))
        if score is not None:
            grouped[value].append(score)
    ranked = sorted(((value, sum(scores) / len(scores)) for value, scores in grouped.items() if scores), key=lambda item: item[1], reverse=reverse)
    return [(value, round(score, 2)) for value, score in ranked[:10]]


def _is_near_miss(row: dict[str, str]) -> bool:
    score = _float_or_none(row.get("score")) or 0.0
    pattern_score = _float_or_none(row.get("pattern_score")) or 0.0
    setup = row.get("setup") or "none"
    reasons = row.get("rejection_reasons", "").lower()
    return score >= 55.0 or pattern_score > 0.0 or row.get("status") in {"watchlist", "detected"} or (setup != "none" and ("session" in reasons or "off-hours" in reasons or "scan_only" in reasons))


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _print_rank(title: str, rows: list[tuple[str, float]]) -> None:
    print(f"{title}:")
    if not rows:
        print("- n/a")
        return
    for value, score in rows:
        print(f"- {value}: avg_score={score:.2f}")


def _print_counter(title: str, counter: Counter[str]) -> None:
    print(f"{title}:")
    if not counter:
        print("- n/a")
        return
    for reason, count in counter.most_common(10):
        print(f"- {reason}: {count}")


def _float_or_none(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _row_time(row: dict[str, str]) -> datetime:
    return _parse_date(row["timestamp"])


if __name__ == "__main__":
    main()
