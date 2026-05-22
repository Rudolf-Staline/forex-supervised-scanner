"""Diagnostic calibration report for rejected/near-miss demo bot signals."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import quantiles

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.execution.models import ExecutionOrder, TradeEvent, TradeEventType
from app.execution.rejected_signals import RejectedSignalRecord
from app.storage.database import Database

CURRENT_SPREAD_ATR_THRESHOLD = 0.220
NEAR_MISS_SCORE = 55.0


@dataclass(frozen=True)
class SignalSnapshot:
    """Minimal signal fields used for calibration diagnostics."""

    symbol: str
    setup: str
    status: str
    score: float | None
    spread_atr: float | None
    reasons: list[str]
    detected_patterns: list[str]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Forex Supervisor spread/ATR calibration and near-miss signals.")
    parser.add_argument("--export-csv", action="store_true", help="Export CSV diagnostics to reports/calibration.")
    parser.add_argument("--legacy", action="store_true", help="Run the older persisted calibration report generator.")
    parser.add_argument("--db", default=None, help="SQLite database path.")
    parser.add_argument("--out", default="reports/calibration", help="Output directory.")
    parser.add_argument("--top-k", nargs="+", type=int, default=[5, 10, 20], help="Legacy Top-K cutoffs.")
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    db_path = Path(args.db) if args.db else settings.database_absolute_path
    if args.legacy:
        from app.reporting.calibration import generate_calibration_report

        outputs = generate_calibration_report(db_path, Path(args.out), args.top_k)
        print("legacy_calibration_report=ok")
        for name, path in outputs.items():
            print(f"{name}={path}")
        return

    database = Database(db_path)
    rejected = database.load_rejected_signals()
    paper_orders = database.load_paper_orders()
    events = database.load_trade_events()
    snapshots = _signal_snapshots(rejected, paper_orders, events)
    alerts = _mt5_bars_zero_alerts(PROJECT_ROOT / "reports")
    _print_report(snapshots, alerts)
    if args.export_csv:
        output_dir = PROJECT_ROOT / args.out
        outputs = _export_csv(snapshots, alerts, output_dir)
        print("csv_export=ok")
        for name, path in outputs.items():
            print(f"{name}={path}")


def _signal_snapshots(
    rejected: list[RejectedSignalRecord],
    paper_orders: list[ExecutionOrder],
    events: list[TradeEvent],
) -> list[SignalSnapshot]:
    patterns_by_symbol_setup = _patterns_by_symbol_setup(events)
    snapshots = [
        SignalSnapshot(
            symbol=record.symbol,
            setup=record.setup,
            status=record.status,
            score=record.score,
            spread_atr=record.spread_atr,
            reasons=list(record.rejection_reasons),
            detected_patterns=patterns_by_symbol_setup.get((record.symbol, record.setup), []),
        )
        for record in rejected
    ]
    for order in paper_orders:
        source = str(order.execution_assumptions.get("source", ""))
        if source != "demo_bot":
            continue
        snapshots.append(
            SignalSnapshot(
                symbol=order.request.symbol,
                setup=order.request.setup_subtype.value,
                status=order.request.source_status or "approved",
                score=order.request.final_score,
                spread_atr=_spread_atr_from_order(order),
                reasons=[],
                detected_patterns=patterns_by_symbol_setup.get((order.request.symbol, order.request.setup_subtype.value), []),
            )
        )
    return snapshots


def _print_report(snapshots: list[SignalSnapshot], mt5_alerts: list[dict[str, object]]) -> None:
    print(f"total_signals={len(snapshots)}")
    _print_counter("status_counts", Counter(snapshot.status for snapshot in snapshots))
    _print_average("average_score_by_symbol", _average_by(snapshots, lambda item: item.symbol, lambda item: item.score))
    _print_average("best_score_by_symbol", _best_by(snapshots, lambda item: item.symbol, lambda item: item.score))
    _print_average("average_spread_atr_by_symbol", _average_by(snapshots, lambda item: item.symbol, lambda item: item.spread_atr))
    _print_min_max("spread_atr_min_max_by_symbol", snapshots)
    _print_counter("top_rejection_reasons", Counter(reason for item in snapshots for reason in item.reasons))

    near_misses = [item for item in snapshots if item.score is not None and item.score >= NEAR_MISS_SCORE and item.status in {"rejected", "watchlist", "detected"}]
    print(f"near_miss_score_ge_{int(NEAR_MISS_SCORE)}={len(near_misses)}")
    _print_counter("near_miss_symbols", Counter(item.symbol for item in near_misses))
    _print_counter("near_miss_setups", Counter(item.setup for item in near_misses))

    patterned = [item for item in snapshots if item.detected_patterns]
    print(f"signals_with_detected_patterns={len(patterned)}")
    _print_counter("detected_patterns", Counter(pattern for item in patterned for pattern in item.detected_patterns))
    _print_counter("expensive_spread_atr_symbols", _expensive_symbols(snapshots))
    _print_candidate_thresholds(snapshots)
    _print_mt5_alerts(mt5_alerts)


def _print_candidate_thresholds(snapshots: list[SignalSnapshot]) -> None:
    print("candidate_thresholds:")
    spreads = sorted(item.spread_atr for item in snapshots if item.spread_atr is not None)
    print(f"- current_spread_atr_threshold: {CURRENT_SPREAD_ATR_THRESHOLD:.3f}")
    if len(spreads) >= 4:
        q25, q50, q75 = quantiles(spreads, n=4, method="inclusive")
        print(f"- observed_percentile_25: {q25:.3f}")
        print(f"- observed_percentile_50: {q50:.3f}")
        print(f"- observed_percentile_75: {q75:.3f}")
    elif spreads:
        print(f"- observed_percentile_25: {spreads[0]:.3f}")
        print(f"- observed_percentile_50: {spreads[len(spreads) // 2]:.3f}")
        print(f"- observed_percentile_75: {spreads[-1]:.3f}")
    else:
        print("- observed_percentile_25: n/a")
        print("- observed_percentile_50: n/a")
        print("- observed_percentile_75: n/a")
    for symbol, threshold in _symbol_threshold_candidates(snapshots).items():
        print(f"- {symbol}: candidate_spread_atr={threshold:.3f}")


def _print_mt5_alerts(alerts: list[dict[str, object]]) -> None:
    print("mt5_data_alerts:")
    if not alerts:
        print("- no bars=0 MT5 alerts found in reports logs")
        return
    for alert in alerts[:20]:
        print(f"- symbol={alert['symbol']} mt5_symbol={alert['mt5_symbol']} status=bars_0 source={alert['source']}")


def _export_csv(snapshots: list[SignalSnapshot], alerts: list[dict[str, object]], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    signals_path = output_dir / "signal_calibration.csv"
    thresholds_path = output_dir / "candidate_thresholds.csv"
    alerts_path = output_dir / "mt5_data_alerts.csv"
    with signals_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "setup", "status", "score", "spread_atr", "reasons", "detected_patterns"])
        writer.writeheader()
        for item in snapshots:
            writer.writerow(
                {
                    "symbol": item.symbol,
                    "setup": item.setup,
                    "status": item.status,
                    "score": item.score,
                    "spread_atr": item.spread_atr,
                    "reasons": "; ".join(item.reasons),
                    "detected_patterns": "; ".join(item.detected_patterns),
                }
            )
    with thresholds_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "current_threshold", "candidate_threshold"])
        writer.writeheader()
        for symbol, threshold in _symbol_threshold_candidates(snapshots).items():
            writer.writerow({"symbol": symbol, "current_threshold": CURRENT_SPREAD_ATR_THRESHOLD, "candidate_threshold": threshold})
    with alerts_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "mt5_symbol", "source"])
        writer.writeheader()
        writer.writerows(alerts)
    return {"signals": signals_path, "thresholds": thresholds_path, "mt5_alerts": alerts_path}


def _patterns_by_symbol_setup(events: list[TradeEvent]) -> dict[tuple[str, str], list[str]]:
    mapping: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in events:
        if event.event_type not in {TradeEventType.DEMO_BOT_DECISION_ACCEPTED, TradeEventType.DEMO_BOT_DECISION_REJECTED}:
            continue
        setup = str(event.payload.get("setup_subtype") or "")
        raw_patterns = str(event.payload.get("detected_patterns") or "")
        for pattern in [item.strip() for item in raw_patterns.split(",") if item.strip()]:
            mapping[(event.symbol, setup)].add(pattern)
    return {key: sorted(value) for key, value in mapping.items()}


def _spread_atr_from_order(order: ExecutionOrder) -> float | None:
    spread = order.request.spread_at_signal
    atr = order.request.atr_at_signal
    if spread is None or atr is None or atr <= 0:
        return None
    return spread / atr


def _average_by(items: list[SignalSnapshot], key_fn, value_fn) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for item in items:
        value = value_fn(item)
        if value is not None:
            grouped[key_fn(item)].append(float(value))
    return {key: round(sum(values) / len(values), 4) for key, values in grouped.items() if values}


def _best_by(items: list[SignalSnapshot], key_fn, value_fn) -> dict[str, float]:
    grouped: dict[str, float] = {}
    for item in items:
        value = value_fn(item)
        if value is None:
            continue
        key = key_fn(item)
        grouped[key] = max(grouped.get(key, float("-inf")), float(value))
    return {key: round(value, 4) for key, value in grouped.items()}


def _symbol_threshold_candidates(items: list[SignalSnapshot]) -> dict[str, float]:
    candidates: dict[str, float] = {}
    for symbol, values in _values_by_symbol(items).items():
        ordered = sorted(values)
        if len(ordered) >= 4:
            _q25, q50, q75 = quantiles(ordered, n=4, method="inclusive")
            candidates[symbol] = round(min(max(q75, CURRENT_SPREAD_ATR_THRESHOLD), 0.75), 4)
        else:
            candidates[symbol] = round(min(max(ordered[-1], CURRENT_SPREAD_ATR_THRESHOLD), 0.75), 4)
    return candidates


def _values_by_symbol(items: list[SignalSnapshot]) -> dict[str, list[float]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for item in items:
        if item.spread_atr is not None:
            grouped[item.symbol].append(float(item.spread_atr))
    return grouped


def _expensive_symbols(items: list[SignalSnapshot]) -> Counter[str]:
    grouped = _values_by_symbol(items)
    return Counter(
        {
            symbol: len([value for value in values if value > CURRENT_SPREAD_ATR_THRESHOLD])
            for symbol, values in grouped.items()
            if values and sum(value > CURRENT_SPREAD_ATR_THRESHOLD for value in values) / len(values) >= 0.6
        }
    )


def _print_min_max(title: str, items: list[SignalSnapshot]) -> None:
    print(f"{title}:")
    grouped = _values_by_symbol(items)
    if not grouped:
        print("- n/a")
        return
    for symbol, values in sorted(grouped.items()):
        print(f"- {symbol}: min={min(values):.3f} max={max(values):.3f}")


def _print_counter(title: str, counter: Counter[str]) -> None:
    print(f"{title}:")
    if not counter:
        print("- n/a")
        return
    for key, count in counter.most_common(12):
        print(f"- {key}: {count}")


def _print_average(title: str, values: dict[str, float]) -> None:
    print(f"{title}:")
    if not values:
        print("- n/a")
        return
    for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True):
        print(f"- {key}: {value:.4f}")


def _mt5_bars_zero_alerts(log_dir: Path) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []
    if not log_dir.exists():
        return alerts
    pattern = re.compile(r"\{.*\}")
    for path in sorted(log_dir.glob("*.log")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = pattern.search(line)
            if not match:
                continue
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            extra = payload.get("extra") if isinstance(payload, dict) else None
            if not isinstance(extra, dict):
                continue
            if extra.get("bars") == 0 and str(payload.get("message", "")).startswith("MT5 market data"):
                alerts.append(
                    {
                        "symbol": str(extra.get("symbol", "")),
                        "mt5_symbol": str(extra.get("mt5_symbol", "")),
                        "source": str(path),
                    }
                )
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, object]] = []
    for alert in alerts:
        key = (str(alert["symbol"]), str(alert["mt5_symbol"]), str(alert["source"]))
        if key not in seen:
            seen.add(key)
            unique.append(alert)
    return unique


if __name__ == "__main__":
    main()
