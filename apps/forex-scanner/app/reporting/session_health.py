"""Read-only session health summary for scanner observation windows."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.reporting.session_opportunity import (
    SAFETY_WARNING as SESSION_OPPORTUNITY_WARNING,
    as_float,
    build_session_opportunity_report,
    collect_records,
    detect_asset_class,
    filter_records,
    normalize_session,
    normalize_status,
)

SAFETY_WARNING = "Session health is read-only and never authorizes broker or MT5 order execution."
BLOCKED_REASON = "No reliable in-session approved history is available; remain paper/read-only."


def _status(total: int, approved: int, rejected: int, off_hours: bool) -> str:
    if total <= 0:
        return "BLOCKED"
    approved_ratio = approved / total
    rejected_ratio = rejected / total
    if off_hours:
        return "BLOCKED" if approved_ratio < 0.5 else "WARN"
    if approved_ratio >= 0.55 and rejected_ratio <= 0.25:
        return "HEALTHY"
    if approved_ratio >= 0.35 and rejected_ratio <= 0.45:
        return "WARN"
    if approved_ratio > 0:
        return "DEGRADED"
    return "BLOCKED"


def _recommendation(status: str, session: str) -> str:
    if status == "HEALTHY":
        return f"Keep observing {session} in paper/read-only mode; do not enable execution."
    if status == "WARN":
        return f"Review spread, rejection, and score context before relying on {session} observations."
    if status == "DEGRADED":
        return f"Treat {session} as weak; collect more paper data before promoting it as an observation window."
    return BLOCKED_REASON


def build_session_health_summary(records: list[dict[str, Any]], top_n: int = 5) -> dict[str, Any]:
    """Summarize per-session quality without touching MT5, broker state, or environment."""

    totals: Counter[str] = Counter()
    approved: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    scores: dict[str, list[float]] = {}
    spread_atr: dict[str, list[float]] = {}
    asset_classes: dict[str, Counter[str]] = {}

    for record in records:
        session = normalize_session(record.get("session"))
        status = normalize_status(record.get("status"))
        totals[session] += 1
        if status == "approved":
            approved[session] += 1
        elif status == "rejected":
            rejected[session] += 1

        scores.setdefault(session, []).append(as_float(record.get("score"), default=0.0))
        spread_value = as_float(record.get("spread_atr"), default=-1.0)
        if spread_value >= 0:
            spread_atr.setdefault(session, []).append(spread_value)
        asset_classes.setdefault(session, Counter())[detect_asset_class(record)] += 1

    sessions: list[dict[str, Any]] = []
    for session in sorted(totals):
        total = totals[session]
        approved_count = approved[session]
        rejected_count = rejected[session]
        health_status = _status(total, approved_count, rejected_count, session == "off_hours")
        score_values = scores.get(session, [])
        spread_values = spread_atr.get(session, [])
        sessions.append(
            {
                "session": session,
                "status": health_status,
                "signals": total,
                "approved": approved_count,
                "rejected": rejected_count,
                "approved_ratio": round(approved_count / total, 6) if total else 0.0,
                "rejected_ratio": round(rejected_count / total, 6) if total else 0.0,
                "average_score": round(sum(score_values) / len(score_values), 6) if score_values else 0.0,
                "average_spread_atr": round(sum(spread_values) / len(spread_values), 6) if spread_values else 0.0,
                "dominant_asset_class": asset_classes[session].most_common(1)[0][0] if asset_classes.get(session) else "unknown",
                "recommendation": _recommendation(health_status, session),
            }
        )

    ranked_sessions = sorted(
        sessions,
        key=lambda item: (
            {"HEALTHY": 3, "WARN": 2, "DEGRADED": 1, "BLOCKED": 0}[str(item["status"])],
            float(item["approved_ratio"]),
            int(item["signals"]),
        ),
        reverse=True,
    )
    opportunity = build_session_opportunity_report(records, top_n=top_n)
    blocked_sessions = [item["session"] for item in sessions if item["status"] == "BLOCKED"]

    return {
        "total_records": len(records),
        "overall_status": opportunity["session_quality_status"] if records else "BLOCKED",
        "sessions": sessions,
        "recommended_observation_windows": [item["session"] for item in ranked_sessions if item["status"] in {"HEALTHY", "WARN"}][:top_n],
        "blocked_sessions": blocked_sessions,
        "off_hours_count": opportunity["off_hours_count"],
        "safety_warning": SAFETY_WARNING,
        "source_warning": SESSION_OPPORTUNITY_WARNING,
        "subprocess_used": False,
        "mt5_called": False,
        "env_mutation_performed": False,
    }


def collect_session_health_records(paths: dict[str, Path], asset_class: str = "all", symbol: str | None = None) -> list[dict[str, Any]]:
    records = collect_records(paths)
    return filter_records(records, asset_class=asset_class, symbol=symbol)


def export_session_health_json(summary: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return path


def export_session_health_csv(summary: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "session",
        "status",
        "signals",
        "approved",
        "rejected",
        "approved_ratio",
        "rejected_ratio",
        "average_score",
        "average_spread_atr",
        "dominant_asset_class",
        "recommendation",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in summary.get("sessions", []):
            writer.writerow({key: item.get(key, "") for key in fieldnames})
    return path
