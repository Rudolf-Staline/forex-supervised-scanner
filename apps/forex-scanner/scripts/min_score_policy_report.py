"""Explain the minimum-score policy for one or more symbols (paper/demo only).

This diagnostic distinguishes the instrument min score, adaptive base /
recommended / effective scores, the scanner effective threshold, the demo bot
min score, and the effective bot threshold. It also surfaces whether adaptive
thresholds are enabled, the adaptive mode, and any scanner/bot mismatch
warnings.

It never mutates ``.env``, never authorizes live trading, never calls
``order_send``, and runs as a bounded one-shot command (no scan required).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from _demo_bot_cli import load_demo_runtime, normalize_symbols
from app.core.types import TradingStyle
from app.reporting.decision_trace import (
    build_min_score_policy_report,
    export_min_score_policies,
    render_min_score_policy_text,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain the paper/demo minimum-score policy without changing thresholds.")
    parser.add_argument("--symbols", nargs="+", default=["EUR/USD"])
    parser.add_argument("--style", default=TradingStyle.DAY_TRADING.value, choices=[style.value for style in TradingStyle])
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--export-json", action="store_true", help="Export reports/min_score_policy_report.json")
    parser.add_argument("--export-txt", action="store_true", help="Export reports/min_score_policy_report.txt")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    # Load settings and enforce paper/demo safety. No scan and no provider data are required.
    settings, _database, _provider = load_demo_runtime("min_score_policy_report.py", provider_name="synthetic", broker_mode="paper")
    style = TradingStyle(args.style)
    symbols = normalize_symbols(args.symbols)
    policies = [build_min_score_policy_report(symbol, style, settings) for symbol in symbols]
    print(render_min_score_policy_text(policies))
    if args.export_json or args.export_txt:
        reports_dir = Path(args.reports_dir)
        export_min_score_policies(policies, reports_dir)
        print(f"exported min-score policy to {reports_dir / 'min_score_policy_report.json'} and {reports_dir / 'min_score_policy_report.txt'}")


if __name__ == "__main__":
    main()
