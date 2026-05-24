"""Near-miss signal review queue (informational-only)."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WARNING_MESSAGE = "Near-miss review is informational and does not authorize execution."


def load_records(reports_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend(_load_jsonl(reports_dir / "signal_journal.jsonl"))
    records.extend(_load_csv(reports_dir / "forward_test_paper.csv"))
    records.extend(_load_json_list(reports_dir / "signal_quality_summary.json"))
    records.extend(_load_json_list(reports_dir / "multi_asset_signal_report_summary.json"))
    return [r for r in records if isinstance(r, dict)]


def build_near_miss_review_queue(
    records: list[dict[str, Any]],
    *,
    asset_class: str = "all",
    symbol: str | None = None,
    session: str | None = None,
    min_score: float = 65,
    top_n: int = 25,
) -> dict[str, Any]:
    filtered = [r for r in records if _match_filters(r, asset_class=asset_class, symbol=symbol, session=session)]

    candidates: list[dict[str, Any]] = []
    for record in filtered:
        near_miss, reasons = _is_near_miss(record, min_score=min_score)
        if not near_miss:
            continue
        enriched = dict(record)
        enriched["near_miss_reasons"] = reasons
        enriched["review_priority_score"] = _review_priority_score(record, reasons)
        candidates.append(enriched)

    candidates.sort(key=lambda item: item.get("review_priority_score", 0.0), reverse=True)
    selected = candidates[: max(top_n, 0)]

    near_miss_by_asset_class = _counter_to_dict(str(r.get("asset_class") or "unknown") for r in selected)
    near_miss_by_symbol = _counter_to_dict(str(r.get("symbol") or "unknown") for r in selected)
    near_miss_by_session = _counter_to_dict(str(r.get("session") or "unknown") for r in selected)

    reason_counter: Counter[str] = Counter()
    for record in selected:
        for reason in record.get("near_miss_reasons", []):
            reason_counter[str(reason)] += 1

    report = {
        "total_near_miss": len(selected),
        "near_miss_by_asset_class": near_miss_by_asset_class,
        "near_miss_by_symbol": near_miss_by_symbol,
        "near_miss_by_session": near_miss_by_session,
        "near_miss_by_reason": dict(reason_counter),
        "review_priority_score": _average(_to_float(r.get("review_priority_score")) for r in selected),
        "candidate_records": selected,
        "manual_review_notes": [
            "Review each candidate manually before any strategic interpretation.",
            "Near-miss queue does not alter strategy rules, thresholds, or execution flow.",
        ],
        "safety_warning": WARNING_MESSAGE,
    }
    return report


def export_summary(report: dict[str, Any], reports_dir: Path, *, export_json: bool, export_csv: bool) -> tuple[Path | None, Path | None]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path: Path | None = None
    csv_path: Path | None = None

    if export_json:
        json_path = reports_dir / "near_miss_review_queue.json"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if export_csv:
        csv_path = reports_dir / "near_miss_review_queue.csv"
        rows = report.get("candidate_records", [])
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = [
                "symbol",
                "asset_class",
                "session",
                "score",
                "risk_reward",
                "spread_atr",
                "status",
                "review_priority_score",
                "near_miss_reasons",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "symbol": row.get("symbol", ""),
                        "asset_class": row.get("asset_class", ""),
                        "session": row.get("session", ""),
                        "score": _score(row),
                        "risk_reward": _risk_reward(row) or "",
                        "spread_atr": _spread_atr(row) or "",
                        "status": row.get("status", ""),
                        "review_priority_score": row.get("review_priority_score", 0),
                        "near_miss_reasons": "|".join(row.get("near_miss_reasons", [])),
                    }
                )

    return json_path, csv_path


def _load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _match_filters(record: dict[str, Any], *, asset_class: str, symbol: str | None, session: str | None) -> bool:
    if asset_class != "all" and str(record.get("asset_class", "")).lower() != asset_class.lower():
        return False
    if symbol and str(record.get("symbol", "")).upper() != symbol.upper():
        return False
    if session and str(record.get("session", "")).lower() != session.lower():
        return False
    return True


def _score(record: dict[str, Any]) -> float:
    return _to_float(record.get("score") or record.get("final_score")) or 0.0


def _risk_reward(record: dict[str, Any]) -> float | None:
    return _to_float(record.get("risk_reward") or record.get("rr") or record.get("risk_reward_ratio"))


def _spread_atr(record: dict[str, Any]) -> float | None:
    return _to_float(record.get("spread_atr") or record.get("spread_to_atr"))


def _status(record: dict[str, Any]) -> str:
    return str(record.get("status") or "").lower()


def _is_near_miss(record: dict[str, Any], *, min_score: float) -> tuple[bool, list[str]]:
    score = _score(record)
    risk_reward = _risk_reward(record)
    spread_atr = _spread_atr(record)
    status = _status(record)
    reason_text = " ".join(str(record.get(k, "")) for k in ("reason", "reasons", "message")).lower()

    reasons: list[str] = []
    if min_score <= score < min_score + 10:
        reasons.append("score_below_threshold_but_close")
    if risk_reward is not None and 1.2 <= risk_reward < 1.5:
        reasons.append("risk_reward_close_but_below_minimum")
    if spread_atr is not None and 0.45 < spread_atr <= 0.65:
        reasons.append("spread_atr_slightly_too_high")
    if status in {"watchlist", "detected"} and score >= 70:
        reasons.append("watchlist_or_detected_high_score")

    major_blocker = any(token in reason_text for token in ("reject", "blocked", "spread", "slippage", "risk"))
    positive = sum(
        [
            score >= min_score,
            risk_reward is not None and risk_reward >= 1.5,
            spread_atr is not None and spread_atr <= 0.45,
        ]
    )
    if major_blocker and positive >= 2:
        reasons.append("single_major_blocker")

    return (len(reasons) > 0), reasons


def _review_priority_score(record: dict[str, Any], reasons: list[str]) -> float:
    score = _score(record)
    rr = _risk_reward(record) or 0.0
    sa = _spread_atr(record)
    session = str(record.get("session") or "").lower()

    priority = min(score, 100.0) * 0.6
    priority += min(rr, 3.0) * 15
    if sa is not None:
        priority += max(0.0, (0.7 - sa) * 20)
    if session in {"london", "new_york", "overlap"}:
        priority += 8
    priority -= max(0, len(reasons) - 1) * 6

    return round(max(0.0, min(100.0, priority)), 4)


def _average(values) -> float:
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 6) if clean else 0.0


def _counter_to_dict(values) -> dict[str, int]:
    return dict(Counter(v for v in values if v))


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
