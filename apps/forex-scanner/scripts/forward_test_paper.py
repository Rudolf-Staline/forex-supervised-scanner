"""Run a multi-day paper-only forward test for the demo bot.

This script never submits MT5 orders. It uses broker=paper, records scanner
decisions and execution-gate explanations, and writes local reports only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _demo_bot_cli import (  # noqa: E402
    evaluate_order_execution_gate,
    filter_tradable_session_symbols_if_requested,
    filter_unhealthy_symbols_if_requested,
    load_demo_runtime,
    normalize_symbols,
    print_next_session_windows,
)
from app.config.watchlists import watchlist_names  # noqa: E402
from app.core.types import TradingStyle  # noqa: E402
from app.execution.demo_bot import DemoBotCycleResult, DemoBotService  # noqa: E402
from app.journal.trade_journal import load_trade_journal  # noqa: E402
from app.notifications.notifier import safety_status_for_broker  # noqa: E402

FORWARD_TEST_CSV = PROJECT_ROOT / "reports" / "forward_test_paper.csv"
FORWARD_TEST_SUMMARY_JSON = PROJECT_ROOT / "reports" / "forward_test_summary.json"
FIXED_PROVIDER = "mt5"
FIXED_BROKER = "paper"
FIXED_WATCHLIST = "multi_asset_demo"


@dataclass(frozen=True)
class ForwardTestRow:
    """One forward-test observation row."""

    timestamp: str
    cycle_id: str
    asset_class: str
    logical_symbol: str
    mt5_symbol: str
    provider: str
    broker: str
    style: str
    session_name: str
    is_tradable_session: bool
    setup: str
    status: str
    score: float | None
    risk_reward: float | None
    pattern_score: float | None
    spread_atr: float | None
    decision: str
    near_miss: bool
    rejection_reasons: str
    execution_gate_status: str
    execution_gate_reasons: str
    created_order: bool
    order_id: str


FIELDNAMES = list(ForwardTestRow.__dataclass_fields__)


def main() -> None:
    """Run the paper-only forward test loop."""

    parser = argparse.ArgumentParser(description="Run a paper-only MT5-data forward test. No MT5 orders are sent.")
    parser.add_argument("--duration-days", type=float, default=7.0)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--export-report", action="store_true")
    parser.add_argument("--watchlist", default=FIXED_WATCHLIST, choices=watchlist_names())
    parser.add_argument("--max-cycles", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.watchlist != FIXED_WATCHLIST:
        raise SystemExit("forward_test_paper requires --watchlist multi_asset_demo")
    run_forward_test(
        duration_days=args.duration_days,
        interval_seconds=args.interval_seconds,
        asset_class=args.asset_class,
        style=TradingStyle(args.style),
        export_report=args.export_report,
        max_cycles=args.max_cycles,
    )


def run_forward_test(
    *,
    duration_days: float,
    interval_seconds: int,
    asset_class: str,
    style: TradingStyle,
    export_report: bool,
    max_cycles: int | None = None,
) -> dict[str, object]:
    """Run the forward-test loop and return the final summary."""

    if duration_days <= 0:
        raise SystemExit("--duration-days must be greater than zero")
    if interval_seconds <= 0:
        raise SystemExit("--interval-seconds must be greater than zero")

    settings, database, provider = load_demo_runtime(
        "forward_test_paper.py",
        provider_name=FIXED_PROVIDER,
        broker_mode=FIXED_BROKER,
    )
    base_symbols = normalize_symbols(None, FIXED_WATCHLIST, asset_class)
    base_symbols = filter_unhealthy_symbols_if_requested(base_symbols, True, FIXED_PROVIDER)
    print_next_session_windows(base_symbols)
    print(
        "forward_test_paper=started "
        f"provider={FIXED_PROVIDER} broker={FIXED_BROKER} watchlist={FIXED_WATCHLIST} "
        f"asset_class={asset_class} style={style.value} interval_seconds={interval_seconds} "
        f"duration_days={duration_days}"
    )

    rows: list[ForwardTestRow] = []
    total_cycles = 0
    end_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    service = DemoBotService(settings, provider, database)
    try:
        while datetime.now(timezone.utc) < end_at:
            if max_cycles is not None and total_cycles >= max_cycles:
                break
            total_cycles += 1
            cycle_started = datetime.now(timezone.utc)
            symbols = filter_tradable_session_symbols_if_requested(base_symbols, True, broker_mode=FIXED_BROKER)
            if not symbols:
                print(f"forward_cycle={total_cycles} skipped=true reason=no_tradable_symbols_now")
            else:
                result = service.run_cycle(style, symbols, watchlist=FIXED_WATCHLIST)
                rows.extend(rows_from_cycle(result, database, settings))
                print(
                    f"forward_cycle={total_cycles} cycle_id={result.cycle_id} "
                    f"signals={result.opportunities} rows_recorded={len(rows)} orders_created={result.orders_created}"
                )
            if max_cycles is not None and total_cycles >= max_cycles:
                break
            sleep_seconds = min(interval_seconds, max(0, int((end_at - datetime.now(timezone.utc)).total_seconds())))
            if sleep_seconds <= 0:
                break
            print(f"sleep_seconds={sleep_seconds}")
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        print("forward_test_paper=stopped reason=keyboard_interrupt")

    summary = build_forward_summary(rows, total_cycles=total_cycles)
    print_forward_summary(summary)
    if export_report:
        export_forward_rows(rows, FORWARD_TEST_CSV)
        export_forward_summary(summary, FORWARD_TEST_SUMMARY_JSON)
        print(f"csv_export={FORWARD_TEST_CSV}")
        print(f"summary_json_export={FORWARD_TEST_SUMMARY_JSON}")
    return summary


def rows_from_cycle(result: DemoBotCycleResult, database, settings) -> list[ForwardTestRow]:
    """Build forward-test rows from the trade journal for a completed cycle."""

    journal_rows = [row for row in load_trade_journal() if row.get("cycle_id") == result.cycle_id]
    paper_orders = {order.order_id: order for order in database.load_paper_orders()}
    output: list[ForwardTestRow] = []
    for row in journal_rows:
        order_id = row.get("order_id", "")
        gate_status = "blocked"
        gate_reasons = "signal did not create a paper order"
        if order_id and order_id in paper_orders:
            gate = evaluate_order_execution_gate(
                settings,
                database,
                paper_orders[order_id],
                broker_mode=FIXED_BROKER,
            )
            gate_status = gate.status
            gate_reasons = "; ".join(gate.reasons) if gate.reasons else "all checks passed"
        output.append(
            ForwardTestRow(
                timestamp=row.get("timestamp", ""),
                cycle_id=result.cycle_id,
                asset_class=row.get("asset_class", ""),
                logical_symbol=row.get("logical_symbol", ""),
                mt5_symbol=row.get("mt5_symbol", ""),
                provider=row.get("provider", FIXED_PROVIDER),
                broker=FIXED_BROKER,
                style=result.style.value,
                session_name=row.get("session_name", ""),
                is_tradable_session=_bool(row.get("is_tradable_session")),
                setup=row.get("setup", ""),
                status=row.get("status", ""),
                score=_float_or_none(row.get("score")),
                risk_reward=_float_or_none(row.get("risk_reward")),
                pattern_score=_float_or_none(row.get("pattern_score")),
                spread_atr=_float_or_none(row.get("spread_atr")),
                decision=row.get("decision", ""),
                near_miss=is_forward_near_miss(row),
                rejection_reasons=row.get("rejection_reasons", ""),
                execution_gate_status=gate_status,
                execution_gate_reasons=gate_reasons,
                created_order=_bool(row.get("created_order")),
                order_id=order_id,
            )
        )
    return output


def is_forward_near_miss(row: dict[str, str]) -> bool:
    """Return whether a row is worth forward-test review."""

    score = _float_or_none(row.get("score")) or 0.0
    pattern_score = _float_or_none(row.get("pattern_score")) or 0.0
    status = (row.get("status") or "").lower()
    setup = (row.get("setup") or "").lower()
    reasons = (row.get("rejection_reasons") or "").lower()
    return (
        score >= 55.0
        or pattern_score > 0.0
        or status in {"watchlist", "detected"}
        or (setup and setup != "none" and ("session" in reasons or "off-hours" in reasons or "scan_only" in reasons))
    )


def build_forward_summary(rows: list[ForwardTestRow], *, total_cycles: int) -> dict[str, object]:
    """Build the requested forward-test summary."""

    approved_like = [row for row in rows if row.status in {"approved", "premium"}]
    near_misses = [row for row in rows if row.near_miss]
    return {
        "total_cycles": total_cycles,
        "total_signals": len(rows),
        "approved_like_signals": len(approved_like),
        "near_miss_signals": len(near_misses),
        "best_symbols": _rank_symbols(rows, reverse=True)[:5],
        "worst_symbols": _rank_symbols(rows, reverse=False)[:5],
        "best_asset_class": _best_group(rows, "asset_class"),
        "best_session": _best_group(rows, "session_name"),
        "most_common_rejection_reasons": dict(Counter(reason for row in rows for reason in _split_reasons(row.rejection_reasons)).most_common(10)),
        "safety_status": safety_status_for_broker(FIXED_BROKER),
    }


def print_forward_summary(summary: dict[str, object]) -> None:
    """Print a compact terminal summary."""

    print("forward_test_summary=no_mt5_orders")
    for key, value in summary.items():
        if isinstance(value, (dict, list)):
            print(f"{key}={json.dumps(value, ensure_ascii=True, sort_keys=True)}")
        else:
            print(f"{key}={value}")


def export_forward_rows(rows: list[ForwardTestRow], path: Path = FORWARD_TEST_CSV) -> Path:
    """Export forward-test observations."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    return path


def export_forward_summary(summary: dict[str, object], path: Path = FORWARD_TEST_SUMMARY_JSON) -> Path:
    """Export forward-test summary JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _rank_symbols(rows: list[ForwardTestRow], *, reverse: bool) -> list[dict[str, object]]:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.score is not None:
            values[row.logical_symbol].append(row.score)
    ranked = sorted(
        ((symbol, sum(scores) / len(scores), len(scores)) for symbol, scores in values.items() if scores),
        key=lambda item: item[1],
        reverse=reverse,
    )
    return [{"symbol": symbol, "average_score": round(score, 2), "signals": count} for symbol, score, count in ranked]


def _best_group(rows: list[ForwardTestRow], field_name: str) -> str:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        key = str(getattr(row, field_name) or "")
        if key and row.score is not None:
            values[key].append(row.score)
    if not values:
        return "n/a"
    key, scores = max(values.items(), key=lambda item: sum(item[1]) / len(item[1]))
    return f"{key} avg_score={sum(scores) / len(scores):.2f} signals={len(scores)}"


def _split_reasons(value: str) -> list[str]:
    return [item.strip() for item in value.split(";") if item.strip()]


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


if __name__ == "__main__":
    main()
