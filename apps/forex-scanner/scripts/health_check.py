"""Local safety and provider health check without sending any order."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.safety import DemoSafetyError, ensure_demo_safe_mode, ensure_mt5_demo_safe_mode
from app.config.settings import load_settings
from app.core.types import Timeframe
from app.data.providers import DataProviderError, MetaTrader5Provider, SyntheticForexDataProvider
from app.execution.broker import BrokerExecutionError
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.utils.logging import configure_logging

REQUIRED_ENV = {
    "EXECUTION_MODE": "paper",
    "ALLOW_LIVE_TRADING": "false",
    "AUTO_BOT_ENABLED": "false",
}


def main() -> None:
    """Run non-trading health checks and exit non-zero on unsafe configuration."""

    load_dotenv()
    configure_logging()
    settings = load_settings()
    failures: list[str] = []

    print("health_check=started orders=false live_trading=false")
    _check_environment(failures)
    _check_safety_lock(settings, failures)
    _check_synthetic_provider(settings, failures)
    _check_mt5_if_available(settings, failures)

    if failures:
        print("health_check=failed")
        for failure in failures:
            print(f"FAIL {failure}")
        raise SystemExit(1)

    print("health_check=ok")


def _check_environment(failures: list[str]) -> None:
    for name, expected in REQUIRED_ENV.items():
        actual = os.getenv(name)
        if actual is None:
            failures.append(f"missing {name}={expected}")
            continue
        if actual.strip().lower() != expected:
            failures.append(f"{name} must be {expected}, got {actual}")
            continue
        print(f"OK {name}={expected}")

    broker_mode = os.getenv("BROKER_MODE")
    if broker_mode is None:
        failures.append("missing BROKER_MODE=paper or BROKER_MODE=mt5_demo")
        return
    normalized = broker_mode.strip().lower()
    if normalized not in {"paper", "mt5_demo"}:
        failures.append(f"BROKER_MODE must be paper or mt5_demo, got {broker_mode}")
        return
    print(f"OK BROKER_MODE={normalized}")

    if normalized == "mt5_demo":
        if os.getenv("MT5_DEMO_ONLY", "").strip().lower() != "true":
            failures.append("MT5_DEMO_ONLY must be true for mt5_demo")
        else:
            print("OK MT5_DEMO_ONLY=true")
        if os.getenv("MT5_SERVER", "").strip() != "Deriv-Demo":
            failures.append("MT5_SERVER must be Deriv-Demo for mt5_demo")
        else:
            print("OK MT5_SERVER=Deriv-Demo")


def _check_safety_lock(settings, failures: list[str]) -> None:
    broker_mode = os.getenv("BROKER_MODE", "").strip().lower()
    try:
        if broker_mode == "mt5_demo":
            ensure_mt5_demo_safe_mode(settings, context="health_check.py")
        else:
            ensure_demo_safe_mode(settings, context="health_check.py")
    except DemoSafetyError as exc:
        failures.append(str(exc))
        return
    print("OK safety_lock=active")


def _check_synthetic_provider(settings, failures: list[str]) -> None:
    adjusted = settings.provider.model_copy(update={"name": "synthetic", "max_bars": max(220, min(settings.provider.max_bars, 260))})
    try:
        df = SyntheticForexDataProvider(adjusted).get_ohlcv("EUR/USD", Timeframe.M15)
    except Exception as exc:
        failures.append(f"synthetic provider failed: {exc}")
        return
    print(f"OK provider=synthetic symbol=EUR/USD timeframe=M15 bars={len(df)}")


def _check_mt5_if_available(settings, failures: list[str]) -> None:
    if importlib.util.find_spec("MetaTrader5") is None:
        print("SKIP provider=mt5 reason=MetaTrader5 package is not installed")
        return

    broker_mode = os.getenv("BROKER_MODE", "").strip().lower()
    if broker_mode == "mt5_demo":
        broker = MT5DemoBroker(settings)
        try:
            account = broker.connect()
            print(f"OK mt5_connection login={account.account_id} server={account.server} balance={account.balance} currency={account.currency}")
        except BrokerExecutionError as exc:
            failures.append(f"MT5 demo connection failed: {exc}")
        finally:
            broker.disconnect()

    adjusted = settings.provider.model_copy(update={"name": "mt5", "max_bars": max(220, min(settings.provider.max_bars, 260))})
    try:
        df = MetaTrader5Provider(adjusted).get_ohlcv("EUR/USD", Timeframe.M15)
    except DataProviderError as exc:
        failures.append(f"MT5 provider failed: {exc}")
        return
    print(f"OK provider=mt5 symbol=EUR/USD timeframe=M15 bars={len(df)}")


if __name__ == "__main__":
    main()
