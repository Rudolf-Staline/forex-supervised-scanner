"""Signal quality report (analysis only)."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from app.config.instruments import COMMODITIES_DEFAULT, FOREX_DEFAULT, INDICES_DEFAULT, filter_symbols_by_asset_class
from app.config.watchlists import get_watchlist, watchlist_names

REPORTS_DIR = Path("reports")
SIGNAL_JOURNAL = REPORTS_DIR / "signal_journal.jsonl"
FORWARD_TEST_CSV = REPORTS_DIR / "forward_test_paper.csv"
FORWARD_SUMMARY = REPORTS_DIR / "forward_test_summary.json"
MULTI_ASSET_SUMMARY = REPORTS_DIR / "multi_asset_signal_report_summary.json"
EXPORT_JSON = REPORTS_DIR / "signal_quality_summary.json"
EXPORT_CSV = REPORTS_DIR / "signal_quality_report.csv"
WARNING = "Do not change thresholds without forward testing."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Signal quality report. Analysis only, no config change.")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--symbol")
    parser.add_argument("--session")
    parser.add_argument("--watchlist", default="multi_asset_demo", choices=watchlist_names())
    parser.add_argument("--min-score", type=float)
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--top-n", type=int, default=10)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def as_float(v: Any, default: float = 0.0) -> float:
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def near_miss(record: dict[str, Any], *, min_score: float, min_rr: float, max_spread_atr: float) -> tuple[bool, list[str]]:
    score = as_float(record.get("score"))
    rr = as_float(record.get("risk_reward"))
    spread = as_float(record.get("spread_atr"), default=-1.0)
    status = str(record.get("status") or "").lower()
    reasons: list[str] = []
    if 0 < (min_score - score) <= 5:
        reasons.append("score_close_to_threshold")
    if 0 < (min_rr - rr) <= 0.2:
        reasons.append("risk_reward_close_to_threshold")
    if max_spread_atr > 0 and 0 < (spread - max_spread_atr) <= 0.05:
        reasons.append("spread_atr_slightly_above_threshold")
    if status in {"watchlist", "detected"} and score >= 70:
        reasons.append("high_score_watchlist_or_detected")
    return (len(reasons) > 0, reasons)


def aggregate(records: list[dict[str, Any]], *, top_n: int, min_score: float, min_rr: float, max_spread_atr: float) -> dict[str, Any]:
    by_asset_scores: dict[str, list[float]] = defaultdict(list)
    by_asset_rr: dict[str, list[float]] = defaultdict(list)
    by_symbol_spread: dict[str, list[float]] = defaultdict(list)
    status_counter: Counter[str] = Counter()
    reject_reasons: Counter[str] = Counter()
    near_miss_reasons: Counter[str] = Counter()
    score_by_symbol: dict[str, list[float]] = defaultdict(list)
    rr_by_symbol: dict[str, list[float]] = defaultdict(list)
    score_by_session: dict[str, list[float]] = defaultdict(list)

    near_miss_count = 0
    for r in records:
        asset = str(r.get("asset_class") or "unknown")
        symbol = str(r.get("symbol") or "unknown")
        session = str(r.get("session") or "unknown")
        status = str(r.get("status") or "unknown").lower()
        score = as_float(r.get("score"))
        rr = as_float(r.get("risk_reward"))
        spread = as_float(r.get("spread_atr"), default=-1.0)

        status_counter[status] += 1
        by_asset_scores[asset].append(score)
        by_asset_rr[asset].append(rr)
        score_by_symbol[symbol].append(score)
        rr_by_symbol[symbol].append(rr)
        score_by_session[session].append(score)
        if spread >= 0:
            by_symbol_spread[symbol].append(spread)

        if status in {"rejected", "reject"}:
            reject_reasons[str(r.get("reason") or "unknown")] += 1

        is_nm, reasons = near_miss(r, min_score=min_score, min_rr=min_rr, max_spread_atr=max_spread_atr)
        if is_nm:
            near_miss_count += 1
            for reason in reasons:
                near_miss_reasons[reason] += 1

    avg_asset_scores = {k: round(mean(v), 4) for k, v in by_asset_scores.items() if v}
    avg_asset_rr = {k: round(mean(v), 4) for k, v in by_asset_rr.items() if v}
    avg_spread_symbol = {k: round(mean(v), 5) for k, v in by_symbol_spread.items() if v}

    best_by_score = sorted(((k, mean(v)) for k, v in score_by_symbol.items() if v), key=lambda x: x[1], reverse=True)[:top_n]
    best_by_rr = sorted(((k, mean(v)) for k, v in rr_by_symbol.items() if v), key=lambda x: x[1], reverse=True)[:top_n]
    worst_by_spread = sorted(avg_spread_symbol.items(), key=lambda x: x[1], reverse=True)[:top_n]

    sessions_ranked = sorted(((k, mean(v)) for k, v in score_by_session.items() if v), key=lambda x: x[1], reverse=True)

    return {
        "total_records": len(records),
        "executable_candidates": sum(1 for r in records if str(r.get("status") or "").lower() in {"executable", "candidate"}),
        "approved_signals": sum(1 for r in records if str(r.get("status") or "").lower() == "approved"),
        "premium_signals": sum(1 for r in records if str(r.get("status") or "").lower() == "premium"),
        "rejected_signals": sum(1 for r in records if str(r.get("status") or "").lower() in {"rejected", "reject"}),
        "watchlist_signals": sum(1 for r in records if str(r.get("status") or "").lower() == "watchlist"),
        "detected_signals": sum(1 for r in records if str(r.get("status") or "").lower() == "detected"),
        "near_miss_signals": near_miss_count,
        "average_score_by_asset_class": avg_asset_scores,
        "average_risk_reward_by_asset_class": avg_asset_rr,
        "average_spread_atr_by_symbol": avg_spread_symbol,
        "top_rejection_reasons": reject_reasons.most_common(top_n),
        "top_near_miss_reasons": near_miss_reasons.most_common(top_n),
        "best_symbols_by_score": [(s, round(v, 4)) for s, v in best_by_score],
        "best_symbols_by_risk_reward": [(s, round(v, 4)) for s, v in best_by_rr],
        "worst_symbols_by_spread_atr": worst_by_spread,
        "best_sessions": [(s, round(v, 4)) for s, v in sessions_ranked[:top_n]],
        "weakest_sessions": [(s, round(v, 4)) for s, v in sessions_ranked[-top_n:]],
        "recommended_focus": build_recommended_focus(best_by_score, worst_by_spread),
        "safety_warning": WARNING,
    }


def build_recommended_focus(best_by_score: list[tuple[str, float]], worst_by_spread: list[tuple[str, float]]) -> list[str]:
    focus: list[str] = []
    if best_by_score:
        focus.append(f"Prioritize {best_by_score[0][0]} quality setups.")
    if worst_by_spread:
        focus.append(f"Be cautious on {worst_by_spread[0][0]} due to spread/ATR pressure.")
    focus.append(WARNING)
    return focus


def filter_records(records: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    symbols = set(get_watchlist(args.watchlist))
    if args.asset_class != "all":
        symbols = set(filter_symbols_by_asset_class(list(symbols), args.asset_class))
    out: list[dict[str, Any]] = []
    for r in records:
        symbol = str(r.get("symbol") or "")
        if symbol and symbol not in symbols:
            continue
        if args.symbol and symbol != args.symbol:
            continue
        if args.session and str(r.get("session") or "") != args.session:
            continue
        if args.asset_class != "all" and str(r.get("asset_class") or "") != args.asset_class:
            continue
        if args.min_score is not None and as_float(r.get("score")) < args.min_score:
            continue
        out.append(r)
    return out


def export_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({k for row in records for k in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    args = parse_args()
    threshold_map = {"forex": FOREX_DEFAULT, "commodities": COMMODITIES_DEFAULT, "indices": INDICES_DEFAULT}
    base = threshold_map.get(args.asset_class, FOREX_DEFAULT) if args.asset_class != "all" else FOREX_DEFAULT
    min_score = float(args.min_score if args.min_score is not None else base["min_score"])
    min_rr = float(base["min_risk_reward"])
    max_spread_atr = float(base["max_spread_atr"])

    journal = load_jsonl(SIGNAL_JOURNAL)
    forward_rows = load_csv(FORWARD_TEST_CSV)
    forward_summary = load_json(FORWARD_SUMMARY)
    multi_asset_summary = load_json(MULTI_ASSET_SUMMARY)

    records = filter_records(journal, args)
    summary = aggregate(records, top_n=args.top_n, min_score=min_score, min_rr=min_rr, max_spread_atr=max_spread_atr)
    summary["input_files"] = {
        "signal_journal": SIGNAL_JOURNAL.exists(),
        "forward_test_paper": FORWARD_TEST_CSV.exists(),
        "forward_test_summary": FORWARD_SUMMARY.exists(),
        "multi_asset_signal_report_summary": MULTI_ASSET_SUMMARY.exists(),
    }
    summary["forward_test_rows"] = len(forward_rows)
    summary["forward_test_summary_keys"] = sorted(forward_summary.keys())
    summary["multi_asset_summary_keys"] = sorted(multi_asset_summary.keys())
    summary["config_modified"] = False
    summary["orders_sent"] = False

    print("signal_quality_report=analysis_only")
    print(f"total_records={summary['total_records']}")
    print(f"approved_signals={summary['approved_signals']}")
    print(f"near_miss_signals={summary['near_miss_signals']}")
    print(WARNING)

    if args.export_json:
        EXPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"json_export={EXPORT_JSON}")

    if args.export_csv:
        export_csv(records, EXPORT_CSV)
        print(f"csv_export={EXPORT_CSV}")


if __name__ == "__main__":
    main()
