"""Report the strongest observed multi-asset demo signals without trading."""

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
from app.config.instruments import AssetClass, filter_symbols_by_asset_class, instrument_for_symbol
from app.config.settings import load_settings
from app.config.watchlists import get_watchlist, watchlist_names
from app.execution.rejected_signals import RejectedSignalRecord
from app.storage.database import Database

REPORT_PATH = PROJECT_ROOT / "reports" / "multi_asset_signal_report.csv"


def main() -> None:
    """Print a multi-asset signal-quality report from stored rejected signals."""

    parser = argparse.ArgumentParser(description="Report best observed multi-asset demo signals without placing orders.")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--min-score", type=float, default=55.0)
    parser.add_argument("--export-csv", action="store_true", help="Export near-miss rows to reports/multi_asset_signal_report.csv.")
    parser.add_argument("--watchlist", default="multi_asset_demo", choices=watchlist_names())
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    records = Database(settings.database_absolute_path).load_rejected_signals()
    filtered = filter_report_records(records, asset_class=args.asset_class, watchlist=args.watchlist)
    report = build_multi_asset_signal_report(filtered, min_score=args.min_score)
    print_multi_asset_signal_report(report, min_score=args.min_score)
    if args.export_csv:
        export_near_miss_csv(report["near_miss_records"], REPORT_PATH)
        print(f"csv_export={REPORT_PATH}")


def filter_report_records(
    records: list[RejectedSignalRecord],
    *,
    asset_class: str,
    watchlist: str,
) -> list[RejectedSignalRecord]:
    """Filter records by requested watchlist and asset class."""

    watchlist_symbols = set(get_watchlist(watchlist))
    filtered = [record for record in records if record.symbol in watchlist_symbols]
    if asset_class != "all":
        allowed = set(filter_symbols_by_asset_class(list(watchlist_symbols), asset_class))
        filtered = [record for record in filtered if record.symbol in allowed]
    return filtered


def build_multi_asset_signal_report(records: list[RejectedSignalRecord], *, min_score: float) -> dict:
    """Build aggregations for observed multi-asset signals."""

    records_by_asset: dict[str, list[RejectedSignalRecord]] = defaultdict(list)
    for record in records:
        records_by_asset[instrument_for_symbol(record.symbol).asset_class.value].append(record)

    near_misses = [record for record in records if is_near_miss(record, min_score=min_score)]
    return {
        "total_signals": len(records),
        "signals_by_asset_class": {asset.value: len(records_by_asset.get(asset.value, [])) for asset in AssetClass},
        "best_score_by_asset_class": _best_score_by_asset_class(records_by_asset),
        "best_score_by_symbol": _best_score_by_symbol(records),
        "best_setup_by_asset_class": _best_setup_by_asset_class(records_by_asset),
        "detected_patterns_by_asset_class": _patterns_by_asset_class(records_by_asset),
        "average_spread_atr_by_symbol": _average_spread_atr_by_symbol(records),
        "rejection_reasons_top": Counter(reason for record in records for reason in record.rejection_reasons),
        "near_miss_signals": len(near_misses),
        "near_miss_records": sorted(near_misses, key=lambda record: _score(record), reverse=True),
        "recommended_focus": _recommended_focus(records),
    }


def is_near_miss(record: RejectedSignalRecord, *, min_score: float) -> bool:
    """Return whether a rejected signal is useful enough to study."""

    reasons = " ".join(record.rejection_reasons).lower()
    has_setup = bool(record.setup and record.setup != "none")
    return (
        _score(record) >= min_score
        or (record.pattern_score or 0.0) > 0.0
        or record.status in {"watchlist", "detected"}
        or (has_setup and ("session" in reasons or "off-hours" in reasons or "scan_only" in reasons))
    )


def print_multi_asset_signal_report(report: dict, *, min_score: float) -> None:
    """Print a readable terminal report."""

    print("multi_asset_signal_report=no_orders")
    print(f"total_signals={report['total_signals']}")
    _print_mapping("signals_by_asset_class", report["signals_by_asset_class"])
    _print_mapping("best_score_by_asset_class", report["best_score_by_asset_class"])
    _print_mapping("best_score_by_symbol", report["best_score_by_symbol"], limit=12)
    _print_mapping("best_setup_by_asset_class", report["best_setup_by_asset_class"])
    _print_counter_mapping("detected_patterns_by_asset_class", report["detected_patterns_by_asset_class"])
    _print_mapping("average_spread_atr_by_symbol", report["average_spread_atr_by_symbol"], limit=12)
    _print_counter("rejection_reasons_top", report["rejection_reasons_top"])
    print(f"near_miss_signals={report['near_miss_signals']}")
    print(
        "near_miss_definition="
        f"score>={min_score:.1f} OR pattern_score>0 OR status=watchlist/detected OR setup rejected for session/off-hours/scan_only"
    )
    _print_near_misses(report["near_miss_records"])
    _print_focus(report["recommended_focus"])


def export_near_miss_csv(records: list[RejectedSignalRecord], path: Path = REPORT_PATH) -> None:
    """Export near-miss records to CSV."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "cycle_id",
        "asset_class",
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
        "provider",
        "broker",
        "style",
        "watchlist",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "timestamp": record.timestamp.isoformat(),
                    "cycle_id": record.cycle_id,
                    "asset_class": instrument_for_symbol(record.symbol).asset_class.value,
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
                    "provider": record.provider,
                    "broker": record.broker,
                    "style": record.style,
                    "watchlist": record.watchlist,
                }
            )


def _best_score_by_asset_class(records_by_asset: dict[str, list[RejectedSignalRecord]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for asset in AssetClass:
        records = records_by_asset.get(asset.value, [])
        best = max((_score(record) for record in records), default=None)
        result[asset.value] = "n/a" if best is None else f"{best:.2f}"
    return result


def _best_score_by_symbol(records: list[RejectedSignalRecord]) -> dict[str, str]:
    best: dict[str, float] = {}
    for record in records:
        best[record.symbol] = max(best.get(record.symbol, 0.0), _score(record))
    return {symbol: f"{score:.2f}" for symbol, score in sorted(best.items(), key=lambda item: item[1], reverse=True)}


def _best_setup_by_asset_class(records_by_asset: dict[str, list[RejectedSignalRecord]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for asset in AssetClass:
        setup_scores: dict[str, float] = {}
        for record in records_by_asset.get(asset.value, []):
            if record.setup and record.setup != "none":
                setup_scores[record.setup] = max(setup_scores.get(record.setup, 0.0), _score(record))
        if not setup_scores:
            result[asset.value] = "n/a"
            continue
        setup, score = max(setup_scores.items(), key=lambda item: item[1])
        result[asset.value] = f"{setup} score={score:.2f}"
    return result


def _patterns_by_asset_class(records_by_asset: dict[str, list[RejectedSignalRecord]]) -> dict[str, Counter[str]]:
    return {
        asset.value: Counter(pattern for record in records_by_asset.get(asset.value, []) for pattern in record.detected_patterns)
        for asset in AssetClass
    }


def _average_spread_atr_by_symbol(records: list[RejectedSignalRecord]) -> dict[str, str]:
    values: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.spread_atr is not None:
            values[record.symbol].append(float(record.spread_atr))
    averages = {symbol: sum(spreads) / len(spreads) for symbol, spreads in values.items() if spreads}
    return {symbol: f"{value:.4f}" for symbol, value in sorted(averages.items(), key=lambda item: item[1])}


def _recommended_focus(records: list[RejectedSignalRecord]) -> dict[str, list[str]]:
    focus: dict[str, list[str]] = {}
    for asset in AssetClass:
        candidates = [record for record in records if instrument_for_symbol(record.symbol).asset_class == asset]
        symbol_scores: dict[str, float] = {}
        for record in candidates:
            score = _focus_score(record)
            symbol_scores[record.symbol] = max(symbol_scores.get(record.symbol, 0.0), score)
        focus[asset.value] = [
            symbol
            for symbol, _score_value in sorted(symbol_scores.items(), key=lambda item: item[1], reverse=True)[:3]
        ]
    return focus


def _focus_score(record: RejectedSignalRecord) -> float:
    pattern_bonus = min(15.0, record.pattern_score or 0.0)
    status_bonus = 5.0 if record.status in {"watchlist", "detected"} else 0.0
    setup_bonus = 3.0 if record.setup and record.setup != "none" else 0.0
    return _score(record) + pattern_bonus + status_bonus + setup_bonus


def _score(record: RejectedSignalRecord) -> float:
    return float(record.score or 0.0)


def _print_mapping(title: str, mapping: dict[str, str | int], *, limit: int | None = None) -> None:
    print(f"{title}:")
    items = list(mapping.items())[:limit]
    if not items:
        print("- n/a")
        return
    for key, value in items:
        print(f"- {key}: {value}")


def _print_counter_mapping(title: str, mapping: dict[str, Counter[str]]) -> None:
    print(f"{title}:")
    for asset in AssetClass:
        counter = mapping.get(asset.value, Counter())
        if not counter:
            print(f"- {asset.value}: n/a")
            continue
        values = ", ".join(f"{name}={count}" for name, count in counter.most_common(5))
        print(f"- {asset.value}: {values}")


def _print_counter(title: str, counter: Counter[str]) -> None:
    print(f"{title}:")
    if not counter:
        print("- n/a")
        return
    for key, count in counter.most_common(10):
        print(f"- {key}: {count}")


def _print_near_misses(records: list[RejectedSignalRecord]) -> None:
    print("near_miss_signals_detail:")
    if not records:
        print("- n/a")
        return
    for record in records[:10]:
        patterns = ",".join(record.detected_patterns) if record.detected_patterns else "-"
        print(
            f"- {record.symbol} asset_class={instrument_for_symbol(record.symbol).asset_class.value} "
            f"status={record.status} setup={record.setup} score={_score(record):.2f} "
            f"pattern_score={record.pattern_score:.2f} patterns={patterns}"
        )


def _print_focus(focus: dict[str, list[str]]) -> None:
    print("recommended_focus:")
    print(f"- forex: {', '.join(focus.get('forex', [])) or '-'}")
    print(f"- commodities: {', '.join(focus.get('commodities', [])) or '-'}")
    print(f"- indices: {', '.join(focus.get('indices', [])) or '-'}")


if __name__ == "__main__":
    main()
