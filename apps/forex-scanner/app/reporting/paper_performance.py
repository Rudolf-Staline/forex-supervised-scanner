"""Paper trading performance analyzer (informational-only)."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WARNING_MESSAGE = "Paper performance is not proof of profitability."


def load_records(reports_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    records.extend(_load_csv(reports_dir / "forward_test_paper.csv"))
    records.extend(_load_csv(reports_dir / "paper_fill_report.csv"))
    records.extend(_load_json_list(reports_dir / "forward_test_summary.json"))
    records.extend(_load_json_list(reports_dir / "paper_fill_summary.json"))
    records.extend(_load_jsonl(reports_dir / "signal_journal.jsonl"))
    return [record for record in records if isinstance(record, dict)]


def build_paper_performance_report(
    records: list[dict[str, Any]], *, asset_class: str = "all", symbol: str | None = None, session: str | None = None, top_n: int = 10
) -> dict[str, Any]:
    filtered = [r for r in records if _match_filters(r, asset_class=asset_class, symbol=symbol, session=session)]

    simulated_orders = sum(1 for r in filtered if _is_simulated_order(r))
    rejected = [r for r in filtered if _is_rejected(r)]

    report = {
        "total_paper_records": len(filtered),
        "simulated_orders": simulated_orders,
        "rejected_paper_orders": len(rejected),
        "average_score": _average(_to_float(r.get("score") or r.get("final_score")) for r in filtered),
        "average_risk_reward": _average(_to_float(r.get("risk_reward")) for r in filtered),
        "average_spread_atr": _average(_to_float(r.get("spread_atr")) for r in filtered),
        "average_slippage": _average(_to_float(r.get("slippage") or r.get("paper_slippage_points")) for r in filtered),
        "average_spread_cost": _average(_to_float(r.get("spread_cost") or r.get("paper_spread_cost")) for r in filtered),
        "average_commission_estimate": _average(_to_float(r.get("commission") or r.get("paper_commission_estimate")) for r in filtered),
        "best_symbols": _rank_group(filtered, "symbol", top_n=top_n, reverse=True),
        "weakest_symbols": _rank_group(filtered, "symbol", top_n=top_n, reverse=False),
        "best_sessions": _rank_group(filtered, "session", top_n=top_n, reverse=True),
        "weakest_sessions": _rank_group(filtered, "session", top_n=top_n, reverse=False),
        "rejection_reasons_top": _top_rejections(rejected, top_n=top_n),
    }
    report["execution_cost_impact"] = round(
        report["average_slippage"] + report["average_spread_cost"] + report["average_commission_estimate"], 8
    )
    report["paper_quality_status"] = _quality_status(report)
    report["recommendations"] = _recommendations(report)
    report["safety_warning"] = WARNING_MESSAGE
    return report


def export_summary(report: dict[str, Any], reports_dir: Path, *, export_csv: bool) -> tuple[Path, Path | None]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "paper_performance_summary.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    csv_path: Path | None = None
    if export_csv:
        csv_path = reports_dir / "paper_performance_report.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["metric", "value"])
            writer.writeheader()
            for key, value in report.items():
                writer.writerow({"metric": key, "value": json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value})
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


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values) -> float:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 8) if clean else 0.0


def _score(record: dict[str, Any]) -> float:
    return _to_float(record.get("score") or record.get("final_score")) or 0.0


def _rank_group(records: list[dict[str, Any]], key: str, *, top_n: int, reverse: bool) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for r in records:
        name = str(r.get(key) or "unknown")
        grouped[name].append(_score(r))
    ranked = sorted(((k, sum(v) / len(v), len(v)) for k, v in grouped.items() if v), key=lambda x: x[1], reverse=reverse)
    return [{key: name, "average_score": round(avg, 8), "count": cnt} for name, avg, cnt in ranked[:top_n]]


def _is_rejected(record: dict[str, Any]) -> bool:
    text = " ".join(str(record.get(field, "")) for field in ("status", "reason", "reasons", "message")).lower()
    return "reject" in text or "blocked" in text


def _is_simulated_order(record: dict[str, Any]) -> bool:
    text = json.dumps(record).lower()
    return any(token in text for token in ("paper", "simulat", "forward_test"))


def _top_rejections(records: list[dict[str, Any]], *, top_n: int) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for r in records:
        reasons = r.get("reasons")
        if isinstance(reasons, list):
            for item in reasons:
                counter[str(item)] += 1
        elif reasons:
            counter[str(reasons)] += 1
        elif r.get("reason"):
            counter[str(r["reason"])] += 1
    return [{"reason": reason, "count": count} for reason, count in counter.most_common(top_n)]


def _quality_status(report: dict[str, Any]) -> str:
    if report["simulated_orders"] == 0:
        return "BLOCKED"
    reject_ratio = report["rejected_paper_orders"] / max(report["simulated_orders"], 1)
    if reject_ratio > 0.5 or report["average_score"] < 40:
        return "DEGRADED"
    if reject_ratio > 0.25 or report["execution_cost_impact"] > 0.001:
        return "WARN"
    return "HEALTHY"


def _recommendations(report: dict[str, Any]) -> list[str]:
    recs = []
    if report["rejected_paper_orders"] > 0:
        recs.append("Review top rejection reasons and session liquidity before interpreting results.")
    if report["execution_cost_impact"] > 0.001:
        recs.append("Execution costs are elevated; prioritize tighter spread windows.")
    if report["average_score"] < 60:
        recs.append("Average score is weak; paper signals need quality improvements.")
    if not recs:
        recs.append("Paper metrics look stable; keep monitoring over larger samples.")
    return recs
