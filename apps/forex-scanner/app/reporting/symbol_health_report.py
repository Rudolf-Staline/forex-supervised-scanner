from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

KNOWN_ASSET_CLASSES = ("forex", "commodities", "indices")


@dataclass
class SymbolHealthOptions:
    reports_dir: Path
    watchlist: str = "multi_asset_demo"
    asset_class: str = "all"
    symbol: str | None = None
    top_n: int = 10


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _f(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _s(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _asset_of(row: dict[str, Any]) -> str:
    asset = _s(row.get("asset_class"), "unknown").lower()
    return asset if asset else "unknown"


def _logical_symbol(row: dict[str, Any]) -> str:
    for k in ("logical_symbol", "symbol"):
        v = _s(row.get(k))
        if v:
            return v
    return "UNKNOWN"


def build_symbol_health_report(options: SymbolHealthOptions) -> dict[str, Any]:
    reports_dir = options.reports_dir
    inputs = {
        "signal_journal": reports_dir / "signal_journal.jsonl",
        "forward_test_paper": reports_dir / "forward_test_paper.csv",
        "multi_asset_summary": reports_dir / "multi_asset_signal_report_summary.json",
        "readiness_report": reports_dir / "readiness_report.json",
    }

    symbol_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_files: list[str] = []

    for name, path in inputs.items():
        if not path.exists() or path.stat().st_size == 0:
            missing_files.append(path.name)
            continue
        if path.suffix == ".jsonl":
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        symbol_rows[_logical_symbol(row)].append(row)
        elif path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    if not isinstance(row, dict):
                        continue
                    symbol_rows[_logical_symbol(row)].append(row)
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                maybe = payload.get("symbols")
                if isinstance(maybe, dict):
                    for sym, data in maybe.items():
                        if isinstance(data, dict):
                            item = {"logical_symbol": sym, **data}
                            symbol_rows[_s(sym, "UNKNOWN")].append(item)

    filtered: dict[str, list[dict[str, Any]]] = {}
    for sym, rows in symbol_rows.items():
        chosen = rows
        if options.asset_class != "all":
            chosen = [r for r in chosen if _asset_of(r) == options.asset_class]
        if options.symbol:
            chosen = [r for r in chosen if _logical_symbol(r).upper() == options.symbol.upper()]
        if chosen:
            filtered[sym] = chosen

    symbols_detected = sorted(filtered) if filtered else sorted(symbol_rows)

    by_asset: dict[str, list[str]] = {k: [] for k in KNOWN_ASSET_CLASSES}
    by_session: dict[str, list[str]] = defaultdict(list)
    no_data: list[str] = []
    high_spread: list[str] = []
    frequent_reject: list[str] = []
    best_scores: list[tuple[str, float]] = []
    low_rr: list[str] = []
    completeness: dict[str, float] = {}

    for sym in symbols_detected:
        rows = filtered.get(sym, symbol_rows.get(sym, []))
        if not rows:
            no_data.append(sym)
            continue
        has_numeric = any(_f(r.get("score")) is not None or _f(r.get("spread_atr")) is not None or _f(r.get("risk_reward")) is not None for r in rows)
        if not has_numeric:
            no_data.append(sym)

        asset_votes = Counter(_asset_of(r) for r in rows)
        asset = asset_votes.most_common(1)[0][0] if asset_votes else "unknown"
        if asset in by_asset:
            by_asset[asset].append(sym)

        sessions = {_s(r.get("session"), "unknown") for r in rows}
        for sess in sessions:
            by_session[sess].append(sym)

        spreads = [_f(r.get("spread_atr")) for r in rows]
        spreads = [x for x in spreads if x is not None]
        if spreads and mean(spreads) > 0.30:
            high_spread.append(sym)

        decisions = [_s(r.get("decision")).lower() for r in rows if _s(r.get("decision"))]
        if decisions:
            rej_ratio = sum(1 for d in decisions if "reject" in d) / len(decisions)
            if rej_ratio >= 0.5:
                frequent_reject.append(sym)

        scores = [_f(r.get("score")) for r in rows]
        scores = [x for x in scores if x is not None]
        if scores:
            best_scores.append((sym, mean(scores)))

        rrs = [_f(r.get("risk_reward")) for r in rows]
        rrs = [x for x in rrs if x is not None]
        if rrs and mean(rrs) < 1.2:
            low_rr.append(sym)

        keys = {"score", "risk_reward", "spread_atr", "decision", "session", "asset_class"}
        total = len(rows) * len(keys)
        filled = sum(1 for r in rows for k in keys if r.get(k) not in (None, ""))
        completeness[sym] = round((filled / total) if total else 0.0, 4)

    ranked = [s for s, _ in sorted(best_scores, key=lambda x: x[1], reverse=True)][: max(1, options.top_n)]

    unhealthy = len(no_data) + len(high_spread) + len(frequent_reject)
    if not symbols_detected:
        status = "BLOCKED"
    elif no_data and len(no_data) >= max(1, len(symbols_detected) // 2):
        status = "DEGRADED"
    elif unhealthy > 0:
        status = "WARN"
    else:
        status = "HEALTHY"

    return {
        "generated_at": _utcnow().isoformat(),
        "watchlist": options.watchlist,
        "asset_class_filter": options.asset_class,
        "symbol_filter": options.symbol,
        "input_files_missing_or_empty": sorted(set(missing_files)),
        "symbols_detected": symbols_detected,
        "symbols_by_asset_class": {k: sorted(v) for k, v in by_asset.items()},
        "symbols_with_no_data": sorted(no_data),
        "symbols_with_high_spread_atr": sorted(high_spread),
        "symbols_with_frequent_rejections": sorted(frequent_reject),
        "symbols_with_best_scores": ranked,
        "symbols_with_low_risk_reward": sorted(low_rr),
        "symbols_seen_by_session": {k: sorted(set(v)) for k, v in by_session.items()},
        "symbol_data_completeness": completeness,
        "symbol_health_status": status,
        "recommended_symbol_focus": ranked,
        "safety_warning": "Read-only diagnostic report. No MT5 call, no order routing, no live trading.",
    }


def write_report_csv(report: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "symbol",
                "completeness",
                "high_spread_atr",
                "frequent_rejections",
                "low_risk_reward",
                "in_best_scores",
            ],
        )
        writer.writeheader()
        best = set(report.get("symbols_with_best_scores", []))
        best_syms = {row[0] if isinstance(row, list | tuple) and row else row for row in best}
        for sym in report.get("symbols_detected", []):
            writer.writerow(
                {
                    "symbol": sym,
                    "completeness": report.get("symbol_data_completeness", {}).get(sym, 0),
                    "high_spread_atr": sym in report.get("symbols_with_high_spread_atr", []),
                    "frequent_rejections": sym in report.get("symbols_with_frequent_rejections", []),
                    "low_risk_reward": sym in report.get("symbols_with_low_risk_reward", []),
                    "in_best_scores": sym in best_syms,
                }
            )
