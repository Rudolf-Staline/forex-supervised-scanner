#!/usr/bin/env python3
"""Run the adaptive threshold engine and generate a report."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adaptive_thresholds.engine import AdaptiveThresholdEngine
from app.config.settings import load_settings
from app.core.types import TradingStyle

REPORTS_DIR = PROJECT_ROOT / "apps" / "forex-scanner" / "reports"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate adaptive thresholds report.")
    parser.add_argument("--style", type=str, required=True, help="Trading style (scalping, day_trading, swing_trading)")
    parser.add_argument("--export-json", action="store_true", help="Export summary to JSON")
    parser.add_argument("--export-csv", action="store_true", help="Export report to CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        style = TradingStyle(args.style)
    except ValueError:
        print(f"Invalid style: {args.style}. Must be one of: {[s.value for s in TradingStyle]}")
        sys.exit(1)

    settings = load_settings()
    engine = AdaptiveThresholdEngine(settings)

    # Analyze all symbols defined in settings
    report = engine.generate_report(settings.symbols, style)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.export_json:
        json_path = REPORTS_DIR / "adaptive_thresholds_summary.json"
        with open(json_path, "w", encoding="utf-8") as f:
            # Simple conversion for dataclass
            import dataclasses

            # Helper to recursively convert dataclasses to dicts
            def _to_dict(obj):
                if dataclasses.is_dataclass(obj):
                    return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
                if isinstance(obj, dict):
                    return {k: _to_dict(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [_to_dict(v) for v in obj]
                return obj

            json.dump(_to_dict(report), f, indent=2)
            print(f"Exported JSON summary to {json_path}")

    if args.export_csv:
        csv_path = REPORTS_DIR / "adaptive_thresholds_report.csv"
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "symbol", "asset_class", "style", "base_min_score",
                "recommended_min_score", "effective_min_score",
                "sample_size", "confidence_level", "adjustment_reason"
            ])
            for sym, res in report.thresholds_by_symbol.items():
                writer.writerow([
                    res.symbol, res.asset_class, res.style, res.base_min_score,
                    res.recommended_min_score, res.effective_min_score,
                    res.sample_size, res.confidence_level, res.reason_summary
                ])
        print(f"Exported CSV report to {csv_path}")

    print(f"Globally generated {len(report.symbols)} thresholds for {style.value}.")

if __name__ == "__main__":
    main()