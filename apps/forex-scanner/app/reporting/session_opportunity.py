from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SAFETY_WARNING = "Session analysis is informational and does not authorize execution."


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            payload = line.strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
    return rows


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_session(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return "off_hours"
    aliases = {
        "london_newyork_overlap": "overlap",
        "newyork_london_overlap": "overlap",
        "ny_london_overlap": "overlap",
        "tokyo_london_overlap": "overlap",
        "outside": "off_hours",
        "off": "off_hours",
        "offhours": "off_hours",
    }
    return aliases.get(value, value)


def normalize_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"approved", "premium"}:
        return "approved"
    if value in {"reject", "rejected"}:
        return "rejected"
    return "detected"


def detect_asset_class(record: dict[str, Any]) -> str:
    asset_class = str(record.get("asset_class") or "").strip().lower()
    if asset_class:
        return asset_class
    symbol = str(record.get("symbol") or record.get("logical_symbol") or "").upper()
    if any(x in symbol for x in ["XAU", "XAG", "WTI", "BRENT"]):
        return "commodities"
    if any(x in symbol for x in ["US30", "NAS", "GER", "SPX", "JP225", "UK100"]):
        return "indices"
    return "forex"


def collect_records(paths: dict[str, Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend(load_jsonl(paths["signal_journal"]))
    records.extend(load_csv(paths["forward_test"]))

    for key in ("backtest_summary", "multi_asset_summary"):
        payload = load_json(paths[key])
        if isinstance(payload.get("records"), list):
            records.extend(r for r in payload["records"] if isinstance(r, dict))
        elif isinstance(payload.get("signals"), list):
            records.extend(r for r in payload["signals"] if isinstance(r, dict))
    return records


def filter_records(records: list[dict[str, Any]], asset_class: str = "all", symbol: str | None = None) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for record in records:
        record_asset_class = detect_asset_class(record)
        record_symbol = str(record.get("symbol") or record.get("logical_symbol") or "")
        if asset_class != "all" and record_asset_class != asset_class:
            continue
        if symbol and record_symbol != symbol:
            continue
        enriched = dict(record)
        enriched["asset_class"] = record_asset_class
        filtered.append(enriched)
    return filtered


def _mean_map(data: dict[str, list[float]]) -> dict[str, float]:
    return {k: round(sum(v) / len(v), 6) for k, v in data.items() if v}


def _status_from_quality(approved_ratio: float, off_hours_ratio: float) -> str:
    if approved_ratio >= 0.55 and off_hours_ratio <= 0.1:
        return "HEALTHY"
    if approved_ratio >= 0.35 and off_hours_ratio <= 0.2:
        return "WARN"
    if approved_ratio >= 0.2:
        return "DEGRADED"
    return "BLOCKED"


def build_session_opportunity_report(records: list[dict[str, Any]], top_n: int = 10) -> dict[str, Any]:
    signals_by_session: Counter[str] = Counter()
    approved_by_session: Counter[str] = Counter()
    rejected_by_session: Counter[str] = Counter()
    score_by_session: dict[str, list[float]] = defaultdict(list)
    rr_by_session: dict[str, list[float]] = defaultdict(list)
    spread_atr_by_session: dict[str, list[float]] = defaultdict(list)
    by_asset_class: dict[str, Counter[str]] = defaultdict(Counter)

    for record in records:
        session = normalize_session(record.get("session"))
        status = normalize_status(record.get("status"))
        asset_class = detect_asset_class(record)

        signals_by_session[session] += 1
        by_asset_class[asset_class][session] += 1

        if status == "approved":
            approved_by_session[session] += 1
        elif status == "rejected":
            rejected_by_session[session] += 1

        score_by_session[session].append(as_float(record.get("score"), default=0.0))
        rr_by_session[session].append(as_float(record.get("risk_reward"), default=0.0))
        spread = as_float(record.get("spread_atr"), default=-1.0)
        if spread >= 0:
            spread_atr_by_session[session].append(spread)

    sessions_detected = sorted(signals_by_session)
    total_records = len(records)
    off_hours_count = signals_by_session.get("off_hours", 0)
    approved_total = sum(approved_by_session.values())
    approved_ratio = (approved_total / total_records) if total_records else 0.0
    off_hours_ratio = (off_hours_count / total_records) if total_records else 0.0

    best_sessions_by_asset_class: dict[str, str] = {}
    weakest_sessions_by_asset_class: dict[str, str] = {}
    for asset_class, counts in by_asset_class.items():
        ranked = counts.most_common()
        if ranked:
            best_sessions_by_asset_class[asset_class] = ranked[0][0]
            weakest_sessions_by_asset_class[asset_class] = ranked[-1][0]

    recommended_windows = [s for s, _ in approved_by_session.most_common(top_n) if s != "off_hours"]
    if not recommended_windows:
        recommended_windows = [s for s, _ in signals_by_session.most_common(top_n) if s != "off_hours"]

    return {
        "total_records": total_records,
        "sessions_detected": sessions_detected,
        "signals_by_session": dict(signals_by_session),
        "approved_by_session": dict(approved_by_session),
        "rejected_by_session": dict(rejected_by_session),
        "average_score_by_session": _mean_map(score_by_session),
        "average_risk_reward_by_session": _mean_map(rr_by_session),
        "average_spread_atr_by_session": _mean_map(spread_atr_by_session),
        "best_sessions_by_asset_class": best_sessions_by_asset_class,
        "weakest_sessions_by_asset_class": weakest_sessions_by_asset_class,
        "off_hours_count": off_hours_count,
        "session_quality_status": _status_from_quality(approved_ratio, off_hours_ratio),
        "recommended_observation_windows": recommended_windows,
        "safety_warning": SAFETY_WARNING,
    }


def export_csv(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for session, total in report.get("signals_by_session", {}).items():
        rows.append(
            {
                "session": session,
                "signals": total,
                "approved": report.get("approved_by_session", {}).get(session, 0),
                "rejected": report.get("rejected_by_session", {}).get(session, 0),
                "avg_score": report.get("average_score_by_session", {}).get(session, 0.0),
                "avg_risk_reward": report.get("average_risk_reward_by_session", {}).get(session, 0.0),
                "avg_spread_atr": report.get("average_spread_atr_by_session", {}).get(session, 0.0),
            }
        )

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["session", "signals", "approved", "rejected", "avg_score", "avg_risk_reward", "avg_spread_atr"],
        )
        writer.writeheader()
        writer.writerows(rows)
