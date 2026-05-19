"""Submit approved scanner opportunities to the local paper executor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.settings import load_settings
from app.core.pipeline import ScannerService
from app.core.types import OpportunityStatus, TradingStyle
from app.data.providers import build_provider
from app.paper.trading import submit_signal_to_paper
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run a scan and create local paper orders for approved/premium rows."""

    parser = argparse.ArgumentParser(description="Create local paper orders from approved scanner opportunities.")
    parser.add_argument("--style", default="day_trading", choices=[style.value for style in TradingStyle])
    parser.add_argument("--symbols", nargs="+", default=None, help="Symbols to scan. Defaults to configured universe.")
    args = parser.parse_args()

    configure_logging()
    settings = load_settings()
    try:
        ensure_demo_safe_mode(settings, context="paper_trade.py")
    except DemoSafetyError as exc:
        raise SystemExit(str(exc))
    if settings.execution.mode != "paper":
        raise SystemExit(f"paper execution is disabled by execution.mode={settings.execution.mode}")
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)
    symbols = args.symbols or settings.symbols
    style = TradingStyle(args.style)

    report = ScannerService(settings, provider, database).scan(style, symbols)
    executable = [opportunity for opportunity in report.opportunities if opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}]
    submissions = [
        submit_signal_to_paper(opportunity, settings=settings, database=database, source="manual", notes="scripts/paper_trade.py")
        for opportunity in executable
    ]
    orders = [submission.order for submission in submissions if submission.order is not None]
    blocked = [submission for submission in submissions if submission.order is None]

    print(
        "paper_trade=ok "
        f"scanned={len(symbols)} opportunities={len(report.opportunities)} executable={len(executable)} "
        f"orders={len(orders)} blocked={len(blocked)}"
    )
    if not executable:
        print("paper_trade=no_executable_opportunities current scan produced no approved/premium rows")
    for order in orders:
        print(
            "order "
            f"id={order.order_id} symbol={order.request.symbol} status={order.status.value} "
            f"direction={order.request.direction.value} entry={order.request.entry_price:.5f} "
            f"sl={order.request.stop_loss:.5f} tp={order.request.take_profit:.5f}"
        )
    for submission in blocked:
        print(f"blocked source={submission.source}: {'; '.join(submission.reasons)}")


if __name__ == "__main__":
    main()
