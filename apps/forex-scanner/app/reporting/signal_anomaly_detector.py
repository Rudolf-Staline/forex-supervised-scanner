"""Signal anomaly detector (analysis only)."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.instruments import COMMODITIES_DEFAULT, FOREX_DEFAULT, INDICES_DEFAULT

WARNING = "Anomaly detection is informational and does not authorize execution."
KNOWN_STATUSES = {"approved", "rejected", "reject", "watchlist", "detected", "candidate", "executable", "premium"}
KNOWN_DECISIONS = {"approved", "rejected", "watchlist", "hold", "candidate", "executable", "skip", "none", ""}
ASSET_CLASSES = {"forex", "commodities", "indices"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect suspicious/inconsistent signals from report files.")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    parser.add_argument("--symbol")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--top-n", type=int, default=20)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
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
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _valid_timestamp(value: Any) -> bool:
    if value in (None, ""):
        return False
    text = str(value).strip().replace("Z", "+00:00")
    try:
        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def _known_symbols() -> set[str]:
    symbols = set(FOREX_DEFAULT.get("symbols", []))
    symbols.update(COMMODITIES_DEFAULT.get("symbols", []))
    symbols.update(INDICES_DEFAULT.get("symbols", []))
    return symbols


def _add_anomaly(anomalies: list[dict[str, Any]], anomaly_type: str, severity: str, record: dict[str, Any], detail: str) -> None:
    anomalies.append({
        "anomaly_type": anomaly_type,
        "severity": severity,
        "symbol": str(record.get("symbol") or ""),
        "asset_class": str(record.get("asset_class") or ""),
        "cycle_id": str(record.get("cycle_id") or ""),
        "detail": detail,
    })


def detect_anomalies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    cycle_counter = Counter(str(r.get("cycle_id") or "") for r in records if r.get("cycle_id") not in (None, ""))
    known_symbols = _known_symbols()

    for record in records:
        score = as_float(record.get("score"))
        rr = as_float(record.get("risk_reward"))
        spread_atr = as_float(record.get("spread_atr"))
        status = str(record.get("status") or "").lower()
        decision = str(record.get("decision") or "").lower()
        asset_class = str(record.get("asset_class") or "").lower()
        symbol = str(record.get("symbol") or "")

        if score is None or score < 0 or score > 100:
            _add_anomaly(anomalies, "invalid_score", "high", record, "score missing or outside 0-100")
        if rr is None or rr <= 0:
            _add_anomaly(anomalies, "invalid_risk_reward", "high", record, "risk_reward missing, zero, or negative")
        if spread_atr is not None and spread_atr < 0:
            _add_anomaly(anomalies, "negative_spread_atr", "high", record, "spread_atr is negative")
        if spread_atr is not None and spread_atr > 2.5:
            _add_anomaly(anomalies, "extreme_spread_atr", "medium", record, "spread_atr abnormally high")
        if status not in KNOWN_STATUSES:
            _add_anomaly(anomalies, "unknown_status", "medium", record, "status not recognized")
        if decision not in KNOWN_DECISIONS:
            _add_anomaly(anomalies, "unknown_decision", "medium", record, "decision not recognized")
        if status == "approved" and decision in {"rejected", "skip", "hold"}:
            _add_anomaly(anomalies, "decision_status_mismatch", "high", record, "decision inconsistent with approved status")
        if status in {"rejected", "reject"} and decision in {"approved", "executable"}:
            _add_anomaly(anomalies, "decision_status_mismatch", "high", record, "decision inconsistent with rejected status")

        if str(record.get("executable_candidate") or "").lower() == "true":
            required = ["entry", "stop_loss", "take_profit"]
            if any(record.get(k) in (None, "") for k in required):
                _add_anomaly(anomalies, "incomplete_executable_candidate", "high", record, "missing entry/stop_loss/take_profit")

        created_order = str(record.get("created_order") or "").lower() == "true"
        broker = str(record.get("broker") or "").lower()
        mode = str(record.get("mode") or "").lower()
        if created_order and (broker == "paper" or "read" in mode):
            _add_anomaly(anomalies, "order_creation_in_paper_or_readonly", "high", record, "created_order true in paper/read-only context")

        if asset_class not in ASSET_CLASSES:
            _add_anomaly(anomalies, "unknown_asset_class", "medium", record, "asset_class is unknown")
        if not symbol or symbol not in known_symbols:
            _add_anomaly(anomalies, "unknown_or_empty_symbol", "medium", record, "symbol missing or unrecognized")
        if not _valid_timestamp(record.get("timestamp")):
            _add_anomaly(anomalies, "invalid_timestamp", "medium", record, "timestamp missing or invalid ISO format")

        cycle_id = str(record.get("cycle_id") or "")
        if cycle_id and cycle_counter[cycle_id] > 1:
            _add_anomaly(anomalies, "duplicate_cycle_id", "high", record, "cycle_id appears multiple times")

        rejection_reasons = record.get("rejection_reasons")
        has_reasons = bool(rejection_reasons)
        if status == "approved" and has_reasons:
            _add_anomaly(anomalies, "approved_with_rejection_reasons", "high", record, "approved signal has rejection_reasons")
        if status in {"rejected", "reject"} and not has_reasons:
            _add_anomaly(anomalies, "rejected_without_reasons", "high", record, "rejected signal has no rejection_reasons")

    return anomalies


def build_summary(records: list[dict[str, Any]], anomalies: list[dict[str, Any]], *, top_n: int) -> dict[str, Any]:
    by_type = Counter(a["anomaly_type"] for a in anomalies)
    by_symbol = Counter((a["symbol"] or "<empty>") for a in anomalies)
    by_asset = Counter((a["asset_class"] or "<empty>") for a in anomalies)

    severe = [a for a in anomalies if a["severity"] == "high"]
    medium = [a for a in anomalies if a["severity"] == "medium"]
    low = [a for a in anomalies if a["severity"] == "low"]

    if len(severe) == 0 and len(medium) == 0:
        integrity = "CLEAN"
    elif len(severe) == 0:
        integrity = "WARN"
    elif len(severe) < max(3, len(records) // 10):
        integrity = "DEGRADED"
    else:
        integrity = "BLOCKED"

    suspicious = sorted(anomalies, key=lambda x: (x["severity"] != "high", x["anomaly_type"]))[:top_n]
    recommendations = [
        "Review high severity anomalies before trusting generated signal metrics.",
        "Fix upstream logging consistency for status/decision/rejection fields.",
        "Keep trading execution disabled until data_integrity_status is CLEAN or WARN.",
    ]

    return {
        "total_records_checked": len(records),
        "anomalies_count": len(anomalies),
        "anomalies_by_type": dict(by_type),
        "anomalies_by_symbol": dict(by_symbol),
        "anomalies_by_asset_class": dict(by_asset),
        "high_severity_anomalies": len(severe),
        "medium_severity_anomalies": len(medium),
        "low_severity_anomalies": len(low),
        "suspicious_records": suspicious,
        "data_integrity_status": integrity,
        "recommendations": recommendations,
        "safety_warning": WARNING,
    }


def export_anomaly_csv(anomalies: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["anomaly_type", "severity", "symbol", "asset_class", "cycle_id", "detail"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(anomalies)


def collect_records(reports_dir: Path) -> list[dict[str, Any]]:
    journal = load_jsonl(reports_dir / "signal_journal.jsonl")
    forward_rows = load_csv(reports_dir / "forward_test_paper.csv")
    return journal + forward_rows


def main() -> None:
    args = parse_args()
    reports_dir = Path(args.reports_dir)
    records = collect_records(reports_dir)
    if args.asset_class != "all":
        records = [r for r in records if str(r.get("asset_class") or "").lower() == args.asset_class]
    if args.symbol:
        records = [r for r in records if str(r.get("symbol") or "") == args.symbol]

    anomalies = detect_anomalies(records)
    summary = build_summary(records, anomalies, top_n=args.top_n)

    print(WARNING)
    print(f"records_checked={summary['total_records_checked']}")
    print(f"anomalies_count={summary['anomalies_count']}")
    print(f"data_integrity_status={summary['data_integrity_status']}")

    if args.export_json:
        out_json = reports_dir / "signal_anomaly_summary.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"json_export={out_json}")
    if args.export_csv:
        out_csv = reports_dir / "signal_anomaly_report.csv"
        export_anomaly_csv(anomalies, out_csv)
        print(f"csv_export={out_csv}")


if __name__ == "__main__":
    main()
