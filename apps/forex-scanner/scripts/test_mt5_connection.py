"""Check an MT5 demo connection without sending any order."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.safety import DemoSafetyError, ensure_mt5_demo_safe_mode
from app.config.settings import load_settings
from app.execution.broker import BrokerExecutionError
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.utils.logging import configure_logging


def main() -> None:
    """Connect to an MT5 demo account and print a sanitized account summary."""

    load_dotenv()
    configure_logging()
    settings = load_settings()
    try:
        ensure_mt5_demo_safe_mode(settings, context="test_mt5_connection.py")
    except DemoSafetyError as exc:
        raise SystemExit(str(exc)) from exc

    broker = MT5DemoBroker(settings)
    try:
        account = broker.connect()
        print("mt5_connection=ok mode=mt5_demo demo_only=true")
        print(f"account_login={account.account_id}")
        print(f"server={account.server}")
        print(f"balance={account.balance}")
        print(f"currency={account.currency}")
    except BrokerExecutionError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
