"""Read-only risk exposure reporting utilities."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from app.config.instruments import instrument_for_symbol


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


def normalize_asset_class(value: str, symbol: str) -> str:
    asset_class = (value or "").lower().strip()
    if asset_class in {"forex", "commodities", "indices"}:
        return asset_class
    return instrument_for_symbol(symbol).asset_class.value


def collect_candidates(reports_dir: Path) -> list[dict[str, Any]]:
    signal_rows = load_jsonl(reports_dir / "signal_journal.jsonl")
    forward_rows = load_csv(reports_dir / "forward_test_paper.csv")
    fill_rows = load_csv(reports_dir / "paper_fill_report.csv")

    all_rows = signal_rows + forward_rows + fill_rows
    candidates: list[dict[str, Any]] = []
    for row in all_rows:
        symbol = str(row.get("symbol") or row.get("Symbol") or "unknown").upper()
        asset_class = normalize_asset_class(str(row.get("asset_class") or ""), symbol)
        status = str(row.get("status") or row.get("decision") or "unknown").lower()
        reason = str(row.get("reason") or row.get("rejection_reason") or "unknown")
        spread_atr = as_float(row.get("spread_atr"), default=-1)
        risk_reward = as_float(row.get("risk_reward"), default=-1)
        candidates.append(
            {
                "symbol": symbol,
                "asset_class": asset_class,
                "status": status,
                "reason": reason,
                "spread_atr": spread_atr,
                "risk_reward": risk_reward,
                "raw": row,
            }
        )
    return candidates


def is_executable(status: str) -> bool:
    return status in {"executable", "candidate", "approved", "executed", "filled"}


def analyze_risk_exposure(
    reports_dir: Path,
    *,
    asset_class: str = "all",
    symbol: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    candidates = collect_candidates(reports_dir)
    filtered: list[dict[str, Any]] = []
    for row in candidates:
        if asset_class != "all" and row["asset_class"] != asset_class:
            continue
        if symbol and row["symbol"] != symbol.upper():
            continue
        filtered.append(row)

    by_asset: Counter[str] = Counter()
    by_symbol: Counter[str] = Counter()
    rr_by_symbol: defaultdict[str, list[float]] = defaultdict(list)
    spread_by_symbol: defaultdict[str, list[float]] = defaultdict(list)
    rejected_reasons: Counter[str] = Counter()

    executable_count = 0
    rejected_count = 0
    incomplete_executable = 0

    for row in filtered:
        by_asset[row["asset_class"]] += 1
        by_symbol[row["symbol"]] += 1
        if row["risk_reward"] >= 0:
            rr_by_symbol[row["symbol"]].append(row["risk_reward"])
        if row["spread_atr"] >= 0:
            spread_by_symbol[row["symbol"]].append(row["spread_atr"])

        if is_executable(row["status"]):
            executable_count += 1
            if row["risk_reward"] < 0 or row["spread_atr"] < 0:
                incomplete_executable += 1
        else:
            rejected_count += 1
            rejected_reasons[row["reason"]] += 1

    high_risk_candidates: list[dict[str, Any]] = []
    concentrated_assets = {k for k, v in by_asset.items() if v >= 3}
    concentrated_symbols = {k for k, v in by_symbol.items() if v >= 3}

    for row in filtered:
        flags: list[str] = []
        if row["spread_atr"] > 0.12:
            flags.append("high_spread_atr")
        if 0 <= row["risk_reward"] < 1.2:
            flags.append("low_risk_reward")
        if row["asset_class"] in concentrated_assets:
            flags.append("asset_class_concentration")
        if row["symbol"] in concentrated_symbols:
            flags.append("symbol_concentration")
        if is_executable(row["status"]) and (row["risk_reward"] < 0 or row["spread_atr"] < 0):
            flags.append("incomplete_executable_data")
        if flags:
            high_risk_candidates.append({"symbol": row["symbol"], "asset_class": row["asset_class"], "flags": sorted(set(flags))})

    avg_rr = {k: round(mean(v), 4) for k, v in rr_by_symbol.items() if v}
    worst_spread = sorted(((k, round(mean(v), 5)) for k, v in spread_by_symbol.items() if v), key=lambda x: x[1], reverse=True)[:top_n]

    readiness = load_json(reports_dir / "readiness_report.json")
    forward_summary = load_json(reports_dir / "forward_test_summary.json")
    fill_summary = load_json(reports_dir / "paper_fill_summary.json")

    max_theoretical = max(by_symbol.values(), default=0)
    safety_status = "ok"
    if high_risk_candidates or incomplete_executable > 0:
        safety_status = "warning"
    if forward_summary.get("safety_status") == "blocked" or readiness.get("status") == "blocked":
        safety_status = "blocked"

    recommendation = "Exposure acceptable. Continue read-only monitoring."
    if safety_status == "warning":
        recommendation = "Reduce concentration and improve data completeness before any execution step."
    if safety_status == "blocked":
        recommendation = "Do not execute. Resolve blocked readiness/safety status first."

    return {
        "total_candidates": len(filtered),
        "executable_candidates": executable_count,
        "rejected_candidates": rejected_count,
        "exposure_by_asset_class": dict(by_asset),
        "exposure_by_symbol": dict(by_symbol),
        "average_risk_reward_by_symbol": avg_rr,
        "worst_spread_atr_symbols": worst_spread,
        "most_frequent_rejection_reasons": rejected_reasons.most_common(top_n),
        "max_theoretical_concurrent_signals": max_theoretical,
        "concentration_risk_symbols": sorted(concentrated_symbols),
        "concentration_risk_asset_classes": sorted(concentrated_assets),
        "high_risk_candidates": high_risk_candidates[: max(top_n, 1)],
        "safety_status": safety_status,
        "recommendation": recommendation,
        "metadata": {
            "read_only": True,
            "sources": {
                "forward_test_summary": forward_summary,
                "paper_fill_summary": fill_summary,
                "readiness_report": readiness,
            },
        },
    }


def export_report_csv(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("total_candidates", summary.get("total_candidates")),
        ("executable_candidates", summary.get("executable_candidates")),
        ("rejected_candidates", summary.get("rejected_candidates")),
        ("max_theoretical_concurrent_signals", summary.get("max_theoretical_concurrent_signals")),
        ("safety_status", summary.get("safety_status")),
        ("recommendation", summary.get("recommendation")),
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        writer.writerows(rows)
