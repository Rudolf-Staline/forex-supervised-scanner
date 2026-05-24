from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.reporting.strategy_explainability import build_report, load_csv, load_json, load_jsonl, normalize_status

REPORTS_DIR = Path("reports")
SIGNAL_JOURNAL = REPORTS_DIR / "signal_journal.jsonl"
MULTI_ASSET_SUMMARY = REPORTS_DIR / "multi_asset_signal_report_summary.json"
FORWARD_TEST_CSV = REPORTS_DIR / "forward_test_paper.csv"
EXPORT_JSON = REPORTS_DIR / "strategy_explainability_summary.json"
EXPORT_TXT = REPORTS_DIR / "strategy_explainability_report.txt"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Strategy explainability report (analysis only).")
    p.add_argument("--asset-class", default="all", choices=["forex", "commodities", "indices", "all"])
    p.add_argument("--symbol")
    p.add_argument("--status", default="all", choices=["approved", "premium", "watchlist", "detected", "rejected", "all"])
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--export-json", action="store_true")
    p.add_argument("--export-txt", action="store_true")
    return p.parse_args()


def collect_records() -> list[dict]:
    records = load_jsonl(SIGNAL_JOURNAL)
    summary = load_json(MULTI_ASSET_SUMMARY)
    if isinstance(summary.get("records"), list):
        records.extend(r for r in summary["records"] if isinstance(r, dict))
    records.extend(load_csv(FORWARD_TEST_CSV))
    return records


def filter_records(records: list[dict], args: argparse.Namespace) -> list[dict]:
    out = []
    for r in records:
        if args.asset_class != "all" and str(r.get("asset_class") or "").lower() != args.asset_class:
            continue
        if args.symbol and str(r.get("symbol") or r.get("logical_symbol") or "") != args.symbol:
            continue
        if args.status != "all" and normalize_status(r.get("status")) != args.status:
            continue
        out.append(r)
    return out


def main() -> None:
    args = parse_args()
    rows = filter_records(collect_records(), args)
    report = build_report(rows, top_n=args.top_n)

    if args.export_json:
        EXPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.export_txt:
        lines = ["Strategy Explainability Report", "", f"total_records: {report['total_records']}"]
        for k, v in report["decision_distribution"].items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append(report["safety_warning"])
        EXPORT_TXT.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
