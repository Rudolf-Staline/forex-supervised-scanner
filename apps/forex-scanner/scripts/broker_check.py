"""Safe broker connectivity and account preflight check.

This script never submits an order. It only verifies configured broker mode,
adapter loading, account visibility, and safety gates.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.execution.broker import build_execution_adapter
from app.execution.operations import build_broker_health_snapshot, classify_broker_incidents


def main() -> None:
    """Run a safe broker preflight check."""

    parser = argparse.ArgumentParser(description="Check broker connectivity and safety mode without submitting orders.")
    parser.add_argument("--mode", choices=["paper", "broker_sandbox", "broker_live"], default=None)
    parser.add_argument("--provider", choices=["mt5", "mock"], default=None)
    args = parser.parse_args()

    settings = load_settings().model_copy(deep=True)
    if args.mode:
        settings.execution.mode = args.mode
    if args.provider:
        settings.broker.provider = args.provider

    if settings.execution.mode == "broker_live" and not settings.execution_capabilities.broker_live_enabled:
        print("broker_check=blocked reason=broker_live requires execution_capabilities.broker_live_enabled=true")
        return
    if settings.execution.mode == "broker_live" and not settings.broker.live_enabled:
        print("broker_check=blocked reason=broker_live requires broker.live_enabled=true")
        return
    if settings.execution.mode == "broker_live" and os.getenv(settings.broker.live_confirmation_env) != settings.broker.live_confirmation_value:
        print(f"broker_check=blocked reason=missing live confirmation env var {settings.broker.live_confirmation_env}")
        return
    if settings.execution.mode == "broker_live" and settings.broker.provider == "mock":
        print("broker_check=blocked reason=mock provider is not allowed for broker_live")
        return

    adapter = build_execution_adapter(settings)
    account = adapter.query_account_state()
    snapshot = build_broker_health_snapshot(account, [], [], settings)
    incidents = classify_broker_incidents(account, [], [], settings)
    print(
        "broker_check=ok "
        f"mode={settings.execution.mode} provider={settings.broker.provider} "
        f"connected={account.connected} can_trade={account.can_trade} "
        f"balance={account.balance} equity={account.equity} free_margin={account.free_margin} "
        f"positions={account.open_positions} orders={account.pending_orders} demo={account.is_demo} "
        f"health={snapshot.health_status} incidents={len(incidents)}"
    )
    if snapshot.degraded_flags:
        print(f"broker_check=degraded flags={','.join(snapshot.degraded_flags)}")
    for incident in incidents:
        print(f"broker_check=incident severity={incident.severity.value} category={incident.category.value} reason={incident.reason}")
    if not account.connected or not account.can_trade:
        print(f"broker_check=safe_failure details={account.raw_summary}")


if __name__ == "__main__":
    main()
