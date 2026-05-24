from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.instruments import instrument_for_symbol
from app.config.watchlists import get_watchlist

EXPECTED_MAPPINGS: dict[str, str] = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/CHF": "USDCHF",
    "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD",
    "USD/CAD": "USDCAD",
    "NZD/USD": "NZDUSD",
    "XAU/USD": "XAUUSD",
    "XAG/USD": "XAGUSD",
    "WTI/OIL": "US Oil",
    "BRENT/OIL": "UK Brent Oil",
    "US500": "US SP 500",
    "US30": "Wall Street 30",
    "NAS100": "US Tech 100",
    "GER40": "Germany 40",
    "UK100": "UK 100",
    "FRA40": "France 40",
}

REPORT_FILES = {
    "signal_journal": Path("reports/signal_journal.jsonl"),
    "forward_test_paper": Path("reports/forward_test_paper.csv"),
    "symbol_health_summary": Path("reports/symbol_health_summary.json"),
    "watchlist_coverage_summary": Path("reports/watchlist_coverage_summary.json"),
}


@dataclass(frozen=True)
class MappingAuditOptions:
    watchlist: str = "multi_asset_demo"
    check_reports: bool = False
    check_static: bool = True


def run_mapping_audit(options: MappingAuditOptions) -> dict[str, Any]:
    watchlist_symbols = get_watchlist(options.watchlist)
    expected = {k: v for k, v in EXPECTED_MAPPINGS.items() if k in watchlist_symbols}

    resolved: dict[str, str | None] = {}
    mismatched: list[dict[str, str]] = []
    missing: list[str] = []
    unused: list[str] = []
    asset_consistency: list[dict[str, str]] = []

    if options.check_static:
        for logical_symbol, expected_mt5 in expected.items():
            candidates = list(instrument_for_symbol(logical_symbol).mt5_symbol_candidates or [])
            resolved_symbol = expected_mt5 if expected_mt5 in candidates else (candidates[0] if candidates else None)
            resolved[logical_symbol] = resolved_symbol
            if resolved_symbol is None:
                missing.append(logical_symbol)
            elif resolved_symbol != expected_mt5:
                mismatched.append({"logical_symbol": logical_symbol, "expected": expected_mt5, "resolved": resolved_symbol})

            asset_consistency.append(
                {
                    "logical_symbol": logical_symbol,
                    "asset_class": instrument_for_symbol(logical_symbol).asset_class.value,
                    "status": "OK" if instrument_for_symbol(logical_symbol).asset_class.value in {"forex", "commodities", "indices"} else "WARN",
                }
            )

        used_mt5 = {v for v in resolved.values() if v}
        unused = sorted({value for value in expected.values() if value not in used_mt5})

    symbols_seen = sorted(_symbols_from_reports() if options.check_reports else set())
    symbols_missing_from_reports = [symbol for symbol in expected if symbol not in symbols_seen]

    mapping_status = _mapping_status(missing, mismatched, symbols_missing_from_reports)
    recommendations = _recommendations(missing, mismatched, symbols_missing_from_reports)

    return {
        "watchlist": options.watchlist,
        "expected_mappings": expected,
        "resolved_mappings": resolved,
        "missing_mappings": missing,
        "mismatched_mappings": mismatched,
        "unused_mappings": unused,
        "symbols_seen_in_reports": symbols_seen,
        "symbols_missing_from_reports": symbols_missing_from_reports,
        "asset_class_consistency": asset_consistency,
        "mapping_status": mapping_status,
        "recommendations": recommendations,
        "safety_warning": "READ-ONLY AUDIT: no MT5 call required, no order, no mapping mutation.",
    }


def export_audit_json(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def export_audit_csv(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for logical, expected in report["expected_mappings"].items():
        resolved = report["resolved_mappings"].get(logical)
        status = "OK" if resolved == expected else "MISMATCH"
        rows.append({"logical_symbol": logical, "expected_mt5_symbol": expected, "resolved_mt5_symbol": resolved or "", "status": status})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["logical_symbol", "expected_mt5_symbol", "resolved_mt5_symbol", "status"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _symbols_from_reports() -> set[str]:
    seen: set[str] = set()
    if REPORT_FILES["signal_journal"].exists():
        for raw in REPORT_FILES["signal_journal"].read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            symbol = str(row.get("logical_symbol") or row.get("symbol") or "").strip().upper()
            if symbol:
                seen.add(symbol)
    if REPORT_FILES["forward_test_paper"].exists():
        with REPORT_FILES["forward_test_paper"].open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                symbol = str(row.get("symbol") or row.get("logical_symbol") or "").strip().upper()
                if symbol:
                    seen.add(symbol)
    for key in ["symbol_health_summary", "watchlist_coverage_summary"]:
        path = REPORT_FILES[key]
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        seen.update(_extract_symbols(payload))
    return seen


def _extract_symbols(payload: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"symbol", "logical_symbol"} and isinstance(value, str) and value.strip():
                found.add(value.strip().upper())
            found.update(_extract_symbols(value))
    elif isinstance(payload, list):
        for item in payload:
            found.update(_extract_symbols(item))
    return found


def _mapping_status(missing: list[str], mismatched: list[dict[str, str]], symbols_missing: list[str]) -> str:
    if missing:
        return "BLOCKED"
    if mismatched:
        return "NEEDS_REVIEW"
    if symbols_missing:
        return "WARN"
    return "CLEAN"


def _recommendations(missing: list[str], mismatched: list[dict[str, str]], symbols_missing: list[str]) -> list[str]:
    out = ["Keep audit read-only: never send MT5 orders from this workflow."]
    if missing:
        out.append(f"Investigate missing static candidates for: {', '.join(sorted(missing))}.")
    if mismatched:
        out.append("Review instrument mt5_symbol_candidates order for mismatched logical symbols.")
    if symbols_missing:
        out.append("Run scanner/reporting cycles to improve symbol coverage in reports.")
    if len(out) == 1:
        out.append("No action required; mapping looks coherent for audited scope.")
    return out
