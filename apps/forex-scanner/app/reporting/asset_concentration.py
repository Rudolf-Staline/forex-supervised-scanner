from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config.instruments import instrument_for_symbol


@dataclass
class AssetConcentrationOptions:
    reports_dir: Path
    asset_class: str = "all"
    top_n: int = 10


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _iter_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    if path.suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _extract_symbol(row: dict[str, Any]) -> str:
    for key in ("logical_symbol", "symbol"):
        value = _s(row.get(key))
        if value:
            return value
    return ""


def _is_executable(row: dict[str, Any]) -> bool:
    decision = _s(row.get("decision")).lower()
    status = _s(row.get("status")).lower()
    executable = _s(row.get("executable")).lower()
    return any(v in {"approved", "executed", "filled", "true", "1", "yes"} for v in [decision, status, executable])


def build_asset_concentration_report(options: AssetConcentrationOptions) -> dict[str, Any]:
    reports_dir = options.reports_dir
    input_files = [
        reports_dir / "signal_journal.jsonl",
        reports_dir / "forward_test_paper.csv",
        reports_dir / "risk_exposure_summary.json",
        reports_dir / "multi_asset_signal_report_summary.json",
        reports_dir / "symbol_health_summary.json",
    ]

    by_symbol: Counter[str] = Counter()
    executable_by_symbol: Counter[str] = Counter()
    rejected_by_symbol: Counter[str] = Counter()
    approved_by_symbol: Counter[str] = Counter()
    by_session: Counter[str] = Counter()
    by_asset_class: Counter[str] = Counter()
    missing_inputs: list[str] = []

    for path in input_files:
        if not path.exists() or path.stat().st_size == 0:
            missing_inputs.append(path.name)
            continue
        rows = _iter_rows(path)
        if not rows:
            continue
        for row in rows:
            sym = _extract_symbol(row)
            if not sym:
                continue
            asset = instrument_for_symbol(sym).asset_class.value
            if options.asset_class != "all" and asset != options.asset_class:
                continue
            by_symbol[sym] += 1
            by_asset_class[asset] += 1
            session = _s(row.get("session")).lower() or "unknown"
            by_session[session] += 1
            decision = _s(row.get("decision")).lower()
            if "reject" in decision or "rejected" in _s(row.get("status")).lower():
                rejected_by_symbol[sym] += 1
            if "approve" in decision or "approved" in _s(row.get("status")).lower():
                approved_by_symbol[sym] += 1
            if _is_executable(row):
                executable_by_symbol[sym] += 1

    total_records = sum(by_symbol.values())
    if total_records == 0:
        risk = "INSUFFICIENT_DATA"
        overrepresented: list[str] = []
        underrepresented: list[str] = []
    else:
        mean_share = 1.0 / max(len(by_symbol), 1)
        overrepresented = sorted([s for s, c in by_symbol.items() if (c / total_records) > (mean_share * 1.3)])
        underrepresented = sorted([s for s, c in by_symbol.items() if (c / total_records) < (mean_share * 0.4)])
        top_share = by_symbol.most_common(1)[0][1] / total_records
        if top_share >= 0.60:
            risk = "HIGH"
        elif top_share >= 0.35:
            risk = "MODERATE"
        else:
            risk = "LOW"

    recommendations: list[str] = []
    if risk == "HIGH":
        recommendations.append("Review scanner filters to increase diversification across symbols and sessions.")
    elif risk == "MODERATE":
        recommendations.append("Monitor concentration trends and rebalance signal sourcing if concentration rises.")
    elif risk == "LOW":
        recommendations.append("Maintain current diversification checks and continue monitoring concentration drift.")
    else:
        recommendations.append("Collect more records before acting on concentration analytics.")

    if overrepresented:
        recommendations.append("Investigate why overrepresented symbols dominate the signal flow.")

    return {
        "generated_at": _utcnow(),
        "asset_class_filter": options.asset_class,
        "input_files_missing_or_empty": sorted(set(missing_inputs)),
        "total_records": total_records,
        "concentration_by_asset_class": dict(by_asset_class),
        "concentration_by_symbol": dict(by_symbol),
        "concentration_by_session": dict(by_session),
        "top_symbols_by_signal_count": [{"symbol": s, "count": c} for s, c in by_symbol.most_common(options.top_n)],
        "top_symbols_by_executable_count": [{"symbol": s, "count": c} for s, c in executable_by_symbol.most_common(options.top_n)],
        "rejected_concentration_by_symbol": dict(rejected_by_symbol),
        "approved_concentration_by_symbol": dict(approved_by_symbol),
        "overrepresented_symbols": overrepresented,
        "underrepresented_symbols": underrepresented,
        "concentration_risk_status": risk,
        "recommendations": recommendations,
        "safety_warning": "Concentration analysis is informational and does not authorize execution.",
    }


def write_asset_concentration_csv(report: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    counts = report.get("concentration_by_symbol", {})
    executable = report.get("top_symbols_by_executable_count", [])
    executable_map = {x["symbol"]: x["count"] for x in executable if isinstance(x, dict) and "symbol" in x}
    rejected = report.get("rejected_concentration_by_symbol", {})
    approved = report.get("approved_concentration_by_symbol", {})
    over = set(report.get("overrepresented_symbols", []))
    under = set(report.get("underrepresented_symbols", []))
    with destination.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["symbol", "asset_class", "signal_count", "executable_count", "rejected_count", "approved_count", "overrepresented", "underrepresented"],
        )
        writer.writeheader()
        for sym in sorted(counts):
            writer.writerow(
                {
                    "symbol": sym,
                    "asset_class": instrument_for_symbol(sym).asset_class.value,
                    "signal_count": counts.get(sym, 0),
                    "executable_count": executable_map.get(sym, 0),
                    "rejected_count": rejected.get(sym, 0),
                    "approved_count": approved.get(sym, 0),
                    "overrepresented": sym in over,
                    "underrepresented": sym in under,
                }
            )
