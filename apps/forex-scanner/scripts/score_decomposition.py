"""Print and export score decomposition decision traces for paper/demo scans.

Paper/demo only. Runs one bounded synthetic/MT5 scan, then explains how each
final score was assembled, which thresholds applied, and which gates failed.
No live trading, no ``order_send``, no ``.env`` mutation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from _demo_bot_cli import load_demo_runtime, normalize_symbols
from app.core.pipeline import ScannerService
from app.core.types import TradingStyle
from app.reporting.decision_trace import (
    build_decision_trace,
    build_score_decomposition,
    export_decision_traces,
    export_min_score_policy_report,
    export_score_decomposition,
    render_decision_traces_text,
    render_score_decomposition_text,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain scanner score decomposition in paper/demo mode.")
    parser.add_argument("--provider", default="synthetic", choices=["synthetic", "auto", "mt5"])
    parser.add_argument("--symbols", nargs="+", default=["EUR/USD"])
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--export-json", action="store_true", help="Export score_decomposition.json, decision_trace.json and min_score_policy_report.json")
    parser.add_argument("--export-txt", action="store_true", help="Export score_decomposition.txt, decision_trace.txt and min_score_policy_report.txt")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings, database, provider = load_demo_runtime("score_decomposition.py", provider_name=args.provider, broker_mode="paper")
    style = TradingStyle(args.style)
    symbols = normalize_symbols(args.symbols)
    report = ScannerService(settings, provider, database).scan(style, symbols)
    traces = [build_decision_trace(opportunity, settings, provider=provider.name) for opportunity in report.opportunities]
    decompositions = [build_score_decomposition(opportunity, settings, provider=provider.name) for opportunity in report.opportunities]
    print(render_decision_traces_text(traces))
    print(render_score_decomposition_text(decompositions))
    if args.export_json or args.export_txt:
        reports_dir = Path(args.reports_dir)
        export_score_decomposition(decompositions, reports_dir)
        export_decision_traces(traces, reports_dir)
        export_min_score_policy_report(traces, reports_dir)
        print(f"exported score decomposition to {reports_dir / 'score_decomposition.json'} and {reports_dir / 'score_decomposition.txt'}")
        print(f"exported decision traces to {reports_dir / 'decision_trace.json'} and {reports_dir / 'decision_trace.txt'}")
        print(f"exported min-score policy to {reports_dir / 'min_score_policy_report.json'} and {reports_dir / 'min_score_policy_report.txt'}")


if __name__ == "__main__":
    main()
