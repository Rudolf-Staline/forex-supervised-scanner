#!/usr/bin/env python
"""Update local realtime paper positions from fresh market candles."""

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
from app.execution.realtime_paper_positions import RealtimePaperPositionConfig, RealtimePaperPositionManagerService
from app.execution.realtime_paper_supervisor import symbols_from_args
from app.storage.database import Database
from app.utils.logging import configure_logging

SAFETY_BANNER = "SAFETY: realtime paper positions are local paper/demo only; no live trading, no order_send."


def _install_safe_defaults() -> None:
    os.environ.setdefault("EXECUTION_MODE", "paper")
    os.environ.setdefault("BROKER_MODE", "paper")
    os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
    os.environ.setdefault("AUTO_BOT_ENABLED", "false")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage local realtime paper position lifecycle.")
    parser.add_argument("--provider", choices=["synthetic", "yahoo", "mt5", "auto"], default="auto")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--watchlist", choices=watchlist_names(), default=None)
    parser.add_argument("--timeframe", choices=[item.value for item in Timeframe], default=Timeframe.M1.value)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--max-data-age-seconds", type=float, default=None)
    parser.add_argument("--max-spread-atr-ratio", type=float, default=0.25)
    parser.add_argument("--block-on-wide-spread", action="store_true")
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
    config = RealtimePaperPositionConfig(
        provider=args.provider,
        symbols=symbols_from_args(args.symbols, args.watchlist),
        timeframe=Timeframe(args.timeframe),
        dry_run=args.dry_run,
        export_json=args.export_json,
        export_txt=args.export_txt,
        reports_dir=Path(args.reports_dir),
        max_age_seconds=args.max_data_age_seconds,
        max_spread_atr_ratio=args.max_spread_atr_ratio,
        block_on_wide_spread=args.block_on_wide_spread,
    )
    report = RealtimePaperPositionManagerService(settings, provider, database).evaluate_position_lifecycle(config)
    print(
        "realtime_paper_positions=completed "
        f"positions_seen={report.positions_seen} pending_orders_seen={report.pending_orders_seen} "
        f"positions_updated={report.positions_updated} positions_closed={report.positions_closed} "
        f"partials={report.partial_exits_created} breakeven_moves={report.breakeven_moves}"
    )
    print("safety=paper_demo_only live_execution_allowed=false broker_order_submission_allowed=false order_send_called=false")
    for warning in report.warnings:
        print(f"warning={warning}")
    for reason in report.blocking_reasons:
        print(f"block={reason}")
    for key, path in report.output_paths.items():
        print(f"report_{key}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
