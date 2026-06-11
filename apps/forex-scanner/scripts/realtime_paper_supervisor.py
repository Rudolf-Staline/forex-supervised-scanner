#!/usr/bin/env python
"""Run a bounded foreground realtime paper supervisor."""

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
from app.execution.realtime_paper_supervisor import RealtimePaperSupervisorConfig, RealtimePaperSupervisorService, symbols_from_args
from app.storage.database import Database
from app.utils.logging import configure_logging

SAFETY_BANNER = "SAFETY: realtime paper supervisor is bounded and paper/demo only; no live trading, no order_send."


def _install_safe_defaults() -> None:
    os.environ.setdefault("EXECUTION_MODE", "paper")
    os.environ.setdefault("BROKER_MODE", "paper")
    os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
    os.environ.setdefault("AUTO_BOT_ENABLED", "false")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded realtime paper/demo supervisor.")
    parser.add_argument("--provider", choices=["synthetic", "yahoo", "mt5", "auto"], default="auto")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--watchlist", choices=watchlist_names(), default=None)
    parser.add_argument("--timeframe", choices=[item.value for item in Timeframe], default=Timeframe.M1.value)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-cycles", type=int, default=5)
    parser.add_argument("--max-runtime-minutes", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--build-evidence-first", action="store_true")
    parser.add_argument("--plan-recovery-on-block", action="store_true")
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
    database = Database(settings.database_absolute_path)
    config = RealtimePaperSupervisorConfig(
        provider=args.provider,
        symbols=symbols_from_args(args.symbols, args.watchlist),
        watchlist=args.watchlist,
        timeframe=Timeframe(args.timeframe),
        interval_seconds=args.interval_seconds,
        max_cycles=args.max_cycles,
        max_runtime_minutes=args.max_runtime_minutes,
        dry_run=args.dry_run,
        build_evidence_first=args.build_evidence_first,
        plan_recovery_on_block=args.plan_recovery_on_block,
        export_json=args.export_json,
        export_txt=args.export_txt,
        reports_dir=Path(args.reports_dir),
        max_data_age_seconds=args.max_data_age_seconds,
        min_data_quality_score=args.min_data_quality_score,
        warn_data_quality_score=args.warn_data_quality_score,
        max_spread_atr_ratio=args.max_spread_atr_ratio,
    )
    report = RealtimePaperSupervisorService(settings, provider, database).run(config)
    print(
        "realtime_paper_supervisor="
        f"{report.stop_reason} cycles={report.cycles_attempted}/{config.max_cycles} "
        f"orders={report.paper_orders_created} data_health={report.data_health_status} "
        f"evidence={report.evidence_status} readiness={report.readiness_status}"
    )
    print("safety=paper_demo_only live_execution_allowed=false broker_order_submission_allowed=false order_send_called=false")
    for reason in report.blocking_reasons:
        print(f"block={reason}")
    for key, path in report.output_paths.items():
        print(f"report_{key}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
