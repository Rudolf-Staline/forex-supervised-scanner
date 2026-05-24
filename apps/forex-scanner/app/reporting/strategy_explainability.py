from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SAFETY_WARNING = "This report explains decisions; it does not authorize execution."
STATUSES = {"approved", "premium", "watchlist", "detected", "rejected"}


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
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
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


def normalize_status(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in {"reject", "rejected"}:
        return "rejected"
    return s if s in STATUSES else "detected"


def explain_record(record: dict[str, Any]) -> str:
    status = normalize_status(record.get("status"))
    score = as_float(record.get("score"))
    rr = as_float(record.get("risk_reward"))
    spread = as_float(record.get("spread_atr"), default=-1.0)
    reasons = record.get("rejection_reasons") or record.get("reason") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    reasons = [str(r) for r in reasons if str(r).strip()]

    if status == "rejected":
        if spread > 0.2:
            return "Signal rejected mainly because spread_atr exceeded the allowed threshold."
        if reasons:
            return f"Signal rejected mainly because {reasons[0]}."
        return "Signal rejected because required quality filters did not pass."
    if status == "watchlist":
        if score >= 50 and rr < 1.5:
            return "Signal watchlist because score is close to approval threshold but risk_reward is insufficient."
        return "Signal watchlist because partial filters passed but confirmation is still required."
    if status in {"approved", "premium"}:
        return "Signal approved because score, risk_reward, session and spread filters passed."
    return "Signal detected because setup conditions were seen, but approval criteria were not fully met."


def build_report(records: list[dict[str, Any]], top_n: int = 10) -> dict[str, Any]:
    distribution: Counter[str] = Counter()
    positives: Counter[str] = Counter()
    negatives: Counter[str] = Counter()
    rejection_clusters: Counter[str] = Counter()
    score_summary: dict[str, list[float]] = defaultdict(list)
    rr_summary: dict[str, list[float]] = defaultdict(list)
    spread_summary: dict[str, list[float]] = defaultdict(list)
    session_summary: Counter[str] = Counter()
    pattern_summary: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)

    for r in records:
        status = normalize_status(r.get("status"))
        distribution[status] += 1
        score = as_float(r.get("score"))
        rr = as_float(r.get("risk_reward"))
        spread = as_float(r.get("spread_atr"), default=-1.0)
        session = str(r.get("session") or "unknown")
        score_summary[status].append(score)
        rr_summary[status].append(rr)
        if spread >= 0:
            spread_summary[status].append(spread)
        session_summary[session] += 1

        pats = r.get("detected_patterns") or []
        if isinstance(pats, str):
            pats = [pats]
        for p in pats:
            pattern_summary[str(p)] += 1

        exp = explain_record(r)
        if len(examples[status]) < 3:
            examples[status].append(exp)

        if status in {"approved", "premium"}:
            positives.update(["score_pass", "risk_reward_pass", "session_pass", "spread_pass"])
        if status in {"watchlist", "detected", "rejected"}:
            if score < 55:
                negatives["low_score"] += 1
            if rr < 1.5:
                negatives["low_risk_reward"] += 1
            if spread > 0.2:
                negatives["high_spread_atr"] += 1

        reasons = r.get("rejection_reasons") or r.get("reason") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        for reason in reasons:
            rejection_clusters[str(reason)] += 1

    def avg_map(src: dict[str, list[float]]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, vals in src.items():
            if vals:
                out[k] = round(sum(vals) / len(vals), 4)
        return out

    return {
        "total_records": len(records),
        "decision_distribution": dict(distribution),
        "main_positive_factors": positives.most_common(top_n),
        "main_negative_factors": negatives.most_common(top_n),
        "rejection_reason_clusters": rejection_clusters.most_common(top_n),
        "score_factor_summary": avg_map(score_summary),
        "risk_reward_factor_summary": avg_map(rr_summary),
        "spread_factor_summary": avg_map(spread_summary),
        "session_factor_summary": session_summary.most_common(top_n),
        "pattern_factor_summary": pattern_summary.most_common(top_n),
        "examples_by_decision": dict(examples),
        "explanation_templates": {
            "rejected": "Signal rejected mainly because spread_atr exceeded the allowed threshold.",
            "watchlist": "Signal watchlist because score is close to approval threshold but risk_reward is insufficient.",
            "approved": "Signal approved because score, risk_reward, session and spread filters passed.",
        },
        "recommended_review_points": [
            "Review recurrent rejection clusters before any manual intervention.",
            "Audit near-threshold watchlist signals for data quality and timing.",
            "Keep thresholds unchanged unless validated by separate forward testing.",
        ],
        "safety_warning": SAFETY_WARNING,
    }
