"""Submit approved scanner opportunities to the local paper executor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.core.pipeline import ScannerService
from app.core.types import OpportunityStatus, TradingStyle
from app.data.providers import build_provider
from app.paper.trading import PaperTradingService
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
    if settings.execution.mode != "paper":
        raise SystemExit(f"paper execution is disabled by execution.mode={settings.execution.mode}")
    database = Database(settings.database_absolute_path)
    provider = build_provider(settings)
    symbols = args.symbols or settings.symbols
    style = TradingStyle(args.style)

    report = ScannerService(settings, provider, database).scan(style, symbols)
    result = PaperTradingService(settings).submit_approved(report.opportunities)
    database.save_paper_orders(result.orders)
    database.save_paper_blocks(result.block_records)
    database.rebuild_trading_journal()
    executable = [opportunity for opportunity in report.opportunities if opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}]

    print(
        "paper_trade=ok "
        f"scanned={len(symbols)} opportunities={len(report.opportunities)} executable={len(executable)} "
        f"orders={len(result.orders)} blocked={len(result.blocked)}"
    )
    if not executable:
        print("paper_trade=no_executable_opportunities current scan produced no approved/premium rows")
    for order in result.orders:
        print(
            "order "
            f"id={order.order_id} symbol={order.request.symbol} status={order.status.value} "
            f"direction={order.request.direction.value} entry={order.request.entry_price:.5f} "
            f"sl={order.request.stop_loss:.5f} tp={order.request.take_profit:.5f}"
        )
    for key, reasons in result.blocked.items():
        print(f"blocked {key}: {'; '.join(reasons)}")


if __name__ == "__main__":
    main()
