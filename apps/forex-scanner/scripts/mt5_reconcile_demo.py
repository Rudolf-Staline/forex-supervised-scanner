"""Read-only MT5 demo reconciliation for Forex Supervisor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.brokers.mt5_reconciliation import MT5ReconciliationReport, reconcile_mt5_demo
from app.config.env import load_dotenv
from app.config.safety import DemoSafetyError, ensure_mt5_demo_safe_mode
from app.config.settings import load_settings
from app.execution.broker import BrokerExecutionError
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.storage.database import Database


def main() -> None:
    """Connect to MT5 demo in read-only mode and print reconciliation status."""

    parser = argparse.ArgumentParser(description="Read-only MT5 demo reconciliation. No positions are opened or closed.")
    parser.add_argument("--max-open-positions", type=int, default=2)
    parser.add_argument("--history-days", type=int, default=7)
    args = parser.parse_args()

    load_dotenv()
    settings = load_settings()
    try:
        ensure_mt5_demo_safe_mode(settings, context="mt5_reconcile_demo.py")
    except DemoSafetyError as exc:
        raise SystemExit(str(exc)) from exc

    database = Database(settings.database_absolute_path)
    broker = MT5DemoBroker(settings)
    try:
        account = broker.connect()
        if broker.mt5 is None:
            raise SystemExit("mt5_connected=false reason=MT5 module unavailable after connect")
        report = reconcile_mt5_demo(
            broker.mt5,
            account=broker.account,
            local_orders=[*database.load_paper_orders(), *database.load_broker_orders()],
            max_open_positions=args.max_open_positions,
            history_days=args.history_days,
        )
    except BrokerExecutionError as exc:
        print("MT5 terminal is not available in cloud environment")
        print_reconciliation_report(
            MT5ReconciliationReport(
                mt5_connected=False,
                account_server="-",
                demo_only=False,
                open_positions=0,
                pending_orders=0,
                bot_positions=0,
                foreign_positions=0,
                duplicate_risk=False,
                reconciliation_status="BLOCKED",
                reasons=[f"mt5_unavailable reason={exc}"],
            )
        )
        return
    finally:
        broker.disconnect()

    print_reconciliation_report(report)


def print_reconciliation_report(report) -> None:
    """Print the required reconciliation fields."""

    print(f"mt5_connected={str(report.mt5_connected).lower()}")
    print(f"account_server={report.account_server or '-'}")
    print(f"demo_only={str(report.demo_only).lower()}")
    print(f"magic_number={report.magic_number}")
    print(f"open_positions={report.open_positions}")
    print(f"pending_orders={report.pending_orders}")
    print(f"bot_positions={report.bot_positions}")
    print(f"foreign_positions={report.foreign_positions}")
    if report.foreign_positions:
        print(f"warning_foreign_position=count:{report.foreign_positions}")
    print(f"duplicate_risk={str(report.duplicate_risk).lower()}")
    print(f"reconciliation_status={report.reconciliation_status}")
    if report.reasons:
        print("reconciliation_reasons:")
        for reason in report.reasons:
            print(f"- {reason}")


if __name__ == "__main__":
    main()
