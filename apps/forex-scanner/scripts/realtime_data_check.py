#!/usr/bin/env python
"""Check realtime market-data health for paper/demo operation."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.settings import load_settings
from app.config.watchlists import watchlist_names
from app.core.types import Timeframe
from app.data.providers import build_provider
from app.execution.realtime_data_health import RealtimeDataHealthConfig, RealtimeDataHealthService
from app.execution.realtime_paper_supervisor import symbols_from_args
from app.utils.logging import configure_logging

SAFETY_BANNER = "SAFETY: realtime data check is read-only; no live trading, no broker orders, no order_send."


def _install_safe_defaults() -> None:
    os.environ.setdefault("EXECUTION_MODE", "paper")
    os.environ.setdefault("BROKER_MODE", "paper")
    os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
    os.environ.setdefault("AUTO_BOT_ENABLED", "false")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime paper/demo data health check.")
    parser.add_argument("--provider", choices=["synthetic", "yahoo", "mt5", "auto"], default="auto")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--watchlist", choices=watchlist_names(), default=None)
    parser.add_argument("--timeframe", choices=[item.value for item in Timeframe], default=Timeframe.M1.value)
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Accepted for CLI parity; not used by one-shot data check.")
    parser.add_argument("--max-cycles", type=int, default=1, help="Accepted for CLI parity; not used by one-shot data check.")
    parser.add_argument("--max-runtime-minutes", type=float, default=None, help="Accepted for CLI parity; not used by one-shot data check.")
    parser.add_argument("--dry-run", action="store_true", help="Accepted for CLI parity; data check is always read-only.")
    parser.add_argument("--build-evidence-first", action="store_true", help="Accepted for CLI parity; not used by data check.")
    parser.add_argument("--plan-recovery-on-block", action="store_true", help="Accepted for CLI parity; not used by data check.")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--max-data-age-seconds", type=float, default=None, help="Override the stale-candle cutoff for realtime data.")
    parser.add_argument("--min-data-quality-score", type=float, default=75.0, help="Block below this data quality score.")
    parser.add_argument("--warn-data-quality-score", type=float, default=90.0, help="Warn below this data quality score when not blocked.")
    parser.add_argument("--max-spread-atr-ratio", type=float, default=0.25, help="Block when latest spread divided by ATR exceeds this ratio.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(SAFETY_BANNER)
    load_dotenv()
    _install_safe_defaults()
    configure_logging()
    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    provider = build_provider(settings)
    symbols = symbols_from_args(args.symbols, args.watchlist)
    config = RealtimeDataHealthConfig(
        provider=args.provider,
        symbols=symbols,
        timeframe=Timeframe(args.timeframe),
        reports_dir=Path(args.reports_dir),
        export_json=args.export_json,
        export_txt=args.export_txt,
        max_age_seconds=args.max_data_age_seconds,
        min_quality_score=args.min_data_quality_score,
        warn_quality_score=args.warn_data_quality_score,
        max_spread_atr_ratio=args.max_spread_atr_ratio,
    )
    report = RealtimeDataHealthService(provider).check(config)
    print(f"realtime_data_health={report.status.value} safe_for_realtime_paper={str(report.safe_for_realtime_paper).lower()}")
    print(f"provider={report.provider} symbols={','.join(report.symbols)} timeframe={report.timeframe.value}")
    print(f"synthetic_fallback_used={str(report.synthetic_fallback_used).lower()} mt5_used={str(report.mt5_used).lower()}")
    for reason in report.blocking_reasons:
        print(f"block={reason}")
    for path in report.output_paths:
        print(f"report={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
