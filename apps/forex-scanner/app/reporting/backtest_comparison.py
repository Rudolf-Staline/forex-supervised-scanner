"""Compare existing backtest/forward-test reports (informational-only)."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

WARNING_MESSAGE = "Backtest comparison is not proof of future profitability."
MIN_SAMPLE_SIZE = 20


METRIC_FIELDS: dict[str, str] = {
    "total_trades": "delta_total_trades",
    "win_rate": "delta_win_rate",
    "expectancy_R": "delta_expectancy_R",
    "profit_factor": "delta_profit_factor",
    "max_drawdown_R": "delta_max_drawdown_R",
    "average_score": "delta_average_score",
    "average_risk_reward": "delta_average_risk_reward",
    "average_spread_atr": "delta_average_spread_atr",
}


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("records"), list):
            return [item for item in data["records"] if isinstance(item, dict)]
        return [data]
    return []


def summarize_records(records: list[dict[str, Any]], *, dataset_name: str) -> dict[str, Any]:
    symbol_scores: dict[str, list[float]] = defaultdict(list)
    session_scores: dict[str, list[float]] = defaultdict(list)
    metrics: dict[str, list[float]] = {k: [] for k in METRIC_FIELDS}

    for row in records:
        symbol = str(row.get("symbol") or "unknown")
        session = str(row.get("session") or "unknown")
        score = _first_float(row, ["average_score", "score", "final_score"])
        if score is not None:
            symbol_scores[symbol].append(score)
            session_scores[session].append(score)

        _push_metric(metrics, "total_trades", _first_float(row, ["total_trades", "trades", "trade_count"]))
        _push_metric(metrics, "win_rate", _first_float(row, ["win_rate"]))
        _push_metric(metrics, "expectancy_R", _first_float(row, ["expectancy_R", "expectancy_r"]))
        _push_metric(metrics, "profit_factor", _first_float(row, ["profit_factor"]))
        _push_metric(metrics, "max_drawdown_R", _first_float(row, ["max_drawdown_R", "max_drawdown_r"]))
        _push_metric(metrics, "average_score", score)
        _push_metric(metrics, "average_risk_reward", _first_float(row, ["average_risk_reward", "risk_reward"]))
        _push_metric(metrics, "average_spread_atr", _first_float(row, ["average_spread_atr", "spread_atr"]))

    out = {
        "name": dataset_name,
        "sample_size": len(records),
        "metrics": {k: _average(v) for k, v in metrics.items()},
        "symbol_scores": {k: _average(v) for k, v in symbol_scores.items()},
        "session_scores": {k: _average(v) for k, v in session_scores.items()},
    }
    return out


def compare_summaries(baseline: dict[str, Any], candidate: dict[str, Any], *, top_n: int = 10) -> dict[str, Any]:
    compared_metrics = sorted(METRIC_FIELDS.keys())
    report: dict[str, Any] = {
        "baseline_name": baseline["name"],
        "candidate_name": candidate["name"],
        "compared_metrics": compared_metrics,
        "improved_symbols": _rank_changes(baseline["symbol_scores"], candidate["symbol_scores"], improved=True, top_n=top_n),
        "degraded_symbols": _rank_changes(baseline["symbol_scores"], candidate["symbol_scores"], improved=False, top_n=top_n),
        "improved_sessions": _rank_changes(baseline["session_scores"], candidate["session_scores"], improved=True, top_n=top_n),
        "degraded_sessions": _rank_changes(baseline["session_scores"], candidate["session_scores"], improved=False, top_n=top_n),
    }

    for metric_key, delta_key in METRIC_FIELDS.items():
        report[delta_key] = round(candidate["metrics"].get(metric_key, 0.0) - baseline["metrics"].get(metric_key, 0.0), 8)

    sample_size_warning = baseline["sample_size"] < MIN_SAMPLE_SIZE or candidate["sample_size"] < MIN_SAMPLE_SIZE
    report["sample_size_warning"] = sample_size_warning
    report["comparison_status"] = _comparison_status(report, sample_size_warning)
    report["recommendation"] = _recommendation(report)
    report["safety_warning"] = WARNING_MESSAGE
    return report


def export_summary(report: dict[str, Any], reports_dir: Path, *, export_json: bool, export_csv: bool) -> tuple[Path | None, Path | None]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = None
    csv_path = None

    if export_json:
        json_path = reports_dir / "backtest_comparison_summary.json"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if export_csv:
        csv_path = reports_dir / "backtest_comparison_report.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["metric", "value"])
            writer.writeheader()
            for key, value in report.items():
                writer.writerow({"metric": key, "value": json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value})

    return json_path, csv_path


def _comparison_status(report: dict[str, Any], sample_size_warning: bool) -> str:
    if sample_size_warning:
        return "INSUFFICIENT_DATA"
    improved = sum(1 for key in METRIC_FIELDS.values() if report.get(key, 0.0) > 0)
    degraded = sum(1 for key in METRIC_FIELDS.values() if report.get(key, 0.0) < 0)
    if improved >= 5 and degraded <= 1:
        return "IMPROVED"
    if degraded >= 5 and improved <= 1:
        return "DEGRADED"
    return "MIXED"


def _recommendation(report: dict[str, Any]) -> str:
    status = report["comparison_status"]
    if status == "INSUFFICIENT_DATA":
        return "Collect more samples before drawing conclusions from this comparison."
    if status == "IMPROVED":
        return "Candidate looks better on aggregate metrics; keep validating in paper mode only."
    if status == "DEGRADED":
        return "Candidate underperforms baseline; do not promote without further diagnostics."
    return "Results are mixed; inspect symbol/session breakdown before any operational decision."


def _rank_changes(before: dict[str, float], after: dict[str, float], *, improved: bool, top_n: int) -> list[dict[str, float | str]]:
    rows: list[tuple[str, float]] = []
    for key in sorted(set(before) | set(after)):
        delta = round(after.get(key, 0.0) - before.get(key, 0.0), 8)
        if improved and delta > 0:
            rows.append((key, delta))
        if not improved and delta < 0:
            rows.append((key, delta))
    rows.sort(key=lambda item: item[1], reverse=improved)
    return [{"name": name, "delta_score": delta} for name, delta in rows[:top_n]]


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 8) if values else 0.0


def _push_metric(store: dict[str, list[float]], key: str, value: float | None) -> None:
    if value is not None:
        store[key].append(value)


def _first_float(row: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = _to_float(row.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
