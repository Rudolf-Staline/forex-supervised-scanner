from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

REQUIRED_SIGNAL_FIELDS = {
    "timestamp_utc",
    "cycle_id",
    "logical_symbol",
    "asset_class",
    "status",
    "score",
    "risk_reward",
    "spread_atr",
    "decision",
}

INPUT_FILES = {
    "signal_journal": "signal_journal.jsonl",
    "forward_test_paper": "forward_test_paper.csv",
    "backtest_multi_asset": "backtest_multi_asset.csv",
    "multi_asset_summary": "multi_asset_signal_report_summary.json",
    "threshold_optimizer_summary": "threshold_optimizer_summary.json",
}


@dataclass
class DataHealthOptions:
    reports_dir: Path
    max_age_hours: int = 48
    min_records: int = 10


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_stale(path: Path, max_age_hours: int) -> bool:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    age_h = (_utcnow() - modified).total_seconds() / 3600
    return age_h > max_age_hours


def build_data_health_report(options: DataHealthOptions) -> dict[str, Any]:
    reports_dir = options.reports_dir
    report: dict[str, Any] = {
        "generated_at": _utcnow().isoformat(),
        "files_checked": [],
        "files_missing": [],
        "files_empty": [],
        "files_stale": [],
        "invalid_json_lines": [],
        "invalid_csv_rows": [],
        "duplicate_cycle_ids": [],
        "missing_required_fields": {},
        "symbol_coverage": {},
        "asset_class_coverage": {},
        "session_coverage": {},
        "score_distribution": {},
        "spread_atr_distribution": {},
        "risk_reward_distribution": {},
        "data_quality_status": "HEALTHY",
        "recommendations": [],
    }

    signal_records: list[dict[str, Any]] = []
    cycle_ids: Counter[str] = Counter()

    for logical_name, filename in INPUT_FILES.items():
        path = reports_dir / filename
        report["files_checked"].append(filename)
        if not path.exists():
            report["files_missing"].append(filename)
            continue
        if path.stat().st_size == 0:
            report["files_empty"].append(filename)
            continue
        if _is_stale(path, options.max_age_hours):
            report["files_stale"].append(filename)

        if filename.endswith(".jsonl"):
            with path.open("r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        report["invalid_json_lines"].append({"file": filename, "line": lineno})
                        continue
                    if not isinstance(row, dict):
                        report["invalid_json_lines"].append({"file": filename, "line": lineno})
                        continue
                    signal_records.append(row)
                    cid = str(row.get("cycle_id") or "")
                    if cid:
                        cycle_ids[cid] += 1
        elif filename.endswith(".csv"):
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                for lineno, row in enumerate(reader, start=2):
                    if row is None:
                        report["invalid_csv_rows"].append({"file": filename, "line": lineno, "reason": "empty_row"})
                        continue
                    if None in row:
                        report["invalid_csv_rows"].append({"file": filename, "line": lineno, "reason": "column_mismatch"})
        else:
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                report["invalid_json_lines"].append({"file": filename, "line": 1})

    for cid, cnt in cycle_ids.items():
        if cnt > 1:
            report["duplicate_cycle_ids"].append(cid)

    missing_fields: dict[str, int] = {field: 0 for field in REQUIRED_SIGNAL_FIELDS}
    scores: list[float] = []
    spreads: list[float] = []
    rrs: list[float] = []
    symbol_counter: Counter[str] = Counter()
    asset_counter: Counter[str] = Counter()
    session_counter: Counter[str] = Counter()

    for row in signal_records:
        for field in REQUIRED_SIGNAL_FIELDS:
            if row.get(field) in (None, ""):
                missing_fields[field] += 1
        symbol_counter[str(row.get("logical_symbol") or "unknown")] += 1
        asset_counter[str(row.get("asset_class") or "unknown")] += 1
        session_counter[str(row.get("session") or "unknown")] += 1
        s = _parse_float(row.get("score"))
        sp = _parse_float(row.get("spread_atr"))
        rr = _parse_float(row.get("risk_reward"))
        if s is not None:
            scores.append(s)
        if sp is not None:
            spreads.append(sp)
        if rr is not None:
            rrs.append(rr)

    report["missing_required_fields"] = {k: v for k, v in missing_fields.items() if v > 0}
    report["symbol_coverage"] = dict(symbol_counter)
    report["asset_class_coverage"] = dict(asset_counter)
    report["session_coverage"] = dict(session_counter)

    report["score_distribution"] = _distribution(scores)
    report["spread_atr_distribution"] = _distribution(spreads)
    report["risk_reward_distribution"] = _distribution(rrs)

    recommendations: list[str] = []
    if report["files_missing"]:
        recommendations.append("Populate missing report files before relying on analytics.")
    if report["files_stale"]:
        recommendations.append("Refresh stale report files to reduce data recency risk.")
    if report["missing_required_fields"]:
        recommendations.append("Fix signal journal schema completeness for required fields.")
    if report["invalid_json_lines"] or report["invalid_csv_rows"]:
        recommendations.append("Repair malformed report records before downstream usage.")
    if len(signal_records) < options.min_records:
        recommendations.append("Collect more signal records to improve statistical confidence.")

    report["recommendations"] = recommendations
    report["data_quality_status"] = _status_from_report(report, len(signal_records), options.min_records)
    return report


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {
        "count": len(values),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "avg": round(mean(values), 6),
    }


def _status_from_report(report: dict[str, Any], signal_count: int, min_records: int) -> str:
    if signal_count == 0:
        return "BLOCKED"
    if report["files_missing"] and signal_count < min_records:
        return "DEGRADED"
    if report["invalid_json_lines"] or report["invalid_csv_rows"] or report["missing_required_fields"]:
        return "DEGRADED"
    if report["files_stale"] or report["files_empty"] or signal_count < min_records or report["duplicate_cycle_ids"]:
        return "WARN"
    return "HEALTHY"


def render_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Data Health Report",
        f"generated_at: {report['generated_at']}",
        f"data_quality_status: {report['data_quality_status']}",
        f"files_missing: {len(report['files_missing'])}",
        f"files_stale: {len(report['files_stale'])}",
        f"invalid_json_lines: {len(report['invalid_json_lines'])}",
        f"invalid_csv_rows: {len(report['invalid_csv_rows'])}",
        f"duplicate_cycle_ids: {len(report['duplicate_cycle_ids'])}",
        "recommendations:",
    ]
    for rec in report["recommendations"]:
        lines.append(f"- {rec}")
    return "\n".join(lines) + "\n"
