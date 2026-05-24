from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config.instruments import filter_symbols_by_asset_class, instrument_for_symbol

READONLY_FALLBACK_WATCHLIST = {
    "multi_asset_demo": [
        "EUR/USD", "GBP/USD", "USD/CHF", "USD/JPY", "AUD/USD", "USD/CAD", "NZD/USD",
        "XAU/USD", "XAG/USD", "WTI/OIL", "BRENT/OIL", "US500", "US30", "NAS100", "GER40", "UK100", "FRA40",
    ]
}


@dataclass
class WatchlistCoverageOptions:
    reports_dir: Path
    watchlist: str = "multi_asset_demo"
    asset_class: str = "all"


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _load_expected_symbols(watchlist: str, asset_class: str) -> list[str]:
    symbols: list[str] = []
    try:
        from app.config.watchlists import get_watchlist

        symbols = get_watchlist(watchlist)
    except Exception:
        symbols = list(READONLY_FALLBACK_WATCHLIST.get(watchlist, []))
    if asset_class != "all":
        symbols = filter_symbols_by_asset_class(symbols, asset_class)
    return list(dict.fromkeys(symbols))


def _extract_symbol(row: dict[str, Any]) -> str:
    for key in ("logical_symbol", "symbol"):
        value = _s(row.get(key))
        if value:
            return value
    return ""


def build_watchlist_coverage_report(options: WatchlistCoverageOptions) -> dict[str, Any]:
    reports_dir = options.reports_dir
    expected = _load_expected_symbols(options.watchlist, options.asset_class)
    expected_set = set(expected)

    inputs = [
        reports_dir / "signal_journal.jsonl",
        reports_dir / "forward_test_paper.csv",
        reports_dir / "multi_asset_signal_report_summary.json",
        reports_dir / "symbol_health_summary.json",
    ]

    missing_inputs: list[str] = []
    observed: set[str] = set()
    sessions: defaultdict[str, set[str]] = defaultdict(set)
    rejection_counts: Counter[str] = Counter()
    symbol_last_seen: dict[str, str] = {}

    for path in inputs:
        if not path.exists() or path.stat().st_size == 0:
            missing_inputs.append(path.name)
            continue
        if path.suffix == ".jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                sym = _extract_symbol(row)
                if not sym:
                    continue
                if options.asset_class != "all" and instrument_for_symbol(sym).asset_class.value != options.asset_class:
                    continue
                observed.add(sym)
                sess = _s(row.get("session")).lower() or "unknown"
                sessions[sess].add(sym)
                decision = _s(row.get("decision")).lower()
                if "reject" in decision:
                    rejection_counts[sym] += 1
                ts = _s(row.get("timestamp")) or _s(row.get("generated_at"))
                if ts:
                    symbol_last_seen[sym] = max(ts, symbol_last_seen.get(sym, ""))
        elif path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    sym = _extract_symbol(row)
                    if not sym:
                        continue
                    if options.asset_class != "all" and instrument_for_symbol(sym).asset_class.value != options.asset_class:
                        continue
                    observed.add(sym)
                    sess = _s(row.get("session")).lower() or "unknown"
                    sessions[sess].add(sym)
                    if "reject" in _s(row.get("decision")).lower() or "rejected" in _s(row.get("status")).lower():
                        rejection_counts[sym] += 1
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                for key in ("symbols", "symbols_detected", "recommended_symbol_focus"):
                    block = payload.get(key)
                    if isinstance(block, dict):
                        iterator = block.keys()
                    elif isinstance(block, list):
                        iterator = [x[0] if isinstance(x, list | tuple) else x for x in block]
                    else:
                        continue
                    for sym in iterator:
                        s = _s(sym)
                        if not s:
                            continue
                        if options.asset_class != "all" and instrument_for_symbol(s).asset_class.value != options.asset_class:
                            continue
                        observed.add(s)

    observed_list = sorted(observed)
    observed_set = set(observed_list)
    missing_symbols = sorted(expected_set - observed_set)
    extra_symbols = sorted(observed_set - expected_set)
    coverage_pct = round((len(observed_set & expected_set) / len(expected_set) * 100.0), 2) if expected_set else 0.0

    by_asset: dict[str, dict[str, Any]] = {}
    for asset in ["forex", "commodities", "indices"]:
        exp = [s for s in expected if instrument_for_symbol(s).asset_class.value == asset]
        obs = [s for s in observed_list if instrument_for_symbol(s).asset_class.value == asset]
        by_asset[asset] = {
            "expected": sorted(exp),
            "observed": sorted(set(obs) & set(exp)),
            "coverage_percentage": round((len(set(obs) & set(exp)) / len(exp) * 100.0), 2) if exp else 0.0,
        }

    without_recent = sorted([s for s in expected if s not in symbol_last_seen])
    repeated_rejections = sorted([s for s, c in rejection_counts.items() if c >= 2])
    manual_checks = []
    if missing_symbols:
        manual_checks.append("Review scanner inputs for missing watchlist symbols.")
    if extra_symbols:
        manual_checks.append("Investigate extra symbols appearing outside configured watchlist.")
    if repeated_rejections:
        manual_checks.append("Inspect spread/session filters for repeatedly rejected symbols.")
    if without_recent:
        manual_checks.append("Check data feed freshness for symbols without recent data.")

    if not observed_list:
        status = "NO_DATA"
    elif coverage_pct >= 99.9:
        status = "FULL"
    elif coverage_pct >= 60:
        status = "PARTIAL"
    else:
        status = "LOW"

    return {
        "generated_at": _utcnow(),
        "watchlist": options.watchlist,
        "asset_class_filter": options.asset_class,
        "input_files_missing_or_empty": sorted(set(missing_inputs)),
        "expected_symbols": expected,
        "observed_symbols": observed_list,
        "missing_symbols": missing_symbols,
        "extra_symbols": extra_symbols,
        "coverage_percentage": coverage_pct,
        "coverage_by_asset_class": by_asset,
        "observed_by_session": {k: sorted(v) for k, v in sessions.items()},
        "symbols_without_recent_data": without_recent,
        "symbols_with_repeated_rejections": repeated_rejections,
        "recommended_manual_checks": manual_checks,
        "coverage_status": status,
        "safety_warning": "Read-only watchlist coverage report. No MT5 call, no order routing, no live trading.",
    }


def write_watchlist_coverage_csv(report: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["symbol", "expected", "observed", "missing", "extra", "asset_class", "repeated_rejections"],
        )
        writer.writeheader()
        expected = set(report.get("expected_symbols", []))
        observed = set(report.get("observed_symbols", []))
        missing = set(report.get("missing_symbols", []))
        extra = set(report.get("extra_symbols", []))
        repeated = set(report.get("symbols_with_repeated_rejections", []))
        for symbol in sorted(expected | observed):
            writer.writerow(
                {
                    "symbol": symbol,
                    "expected": symbol in expected,
                    "observed": symbol in observed,
                    "missing": symbol in missing,
                    "extra": symbol in extra,
                    "asset_class": instrument_for_symbol(symbol).asset_class.value,
                    "repeated_rejections": symbol in repeated,
                }
            )
