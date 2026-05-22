"""Run the paper-only demo bot loop from the terminal."""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from _demo_bot_cli import (
    add_cycle_arguments,
    created_order_ids,
    filter_unhealthy_symbols_if_requested,
    load_demo_runtime,
    normalize_symbols,
    print_broker_result,
    print_cycle_result,
)
from app.core.types import TradingStyle
from app.execution.demo_bot import DemoBotService
from app.execution.demo_bot_config import DemoBotConfig
from app.execution.mt5_demo_broker import MT5DemoBroker


def main() -> None:
    """Run demo bot cycles until interrupted by the operator."""

    parser = argparse.ArgumentParser(description="Run Forex Supervisor demo bot cycles in paper/demo mode.")
    add_cycle_arguments(parser)
    args = parser.parse_args()

    settings, database, provider = load_demo_runtime(
        "run_demo_bot.py",
        provider_name=args.provider,
        broker_mode=args.broker,
        debug_market_data=args.debug_market_data,
    )
    config = DemoBotConfig.from_settings(settings)
    style = TradingStyle(args.style)
    symbols = normalize_symbols(args.symbols, args.watchlist)
    symbols = filter_unhealthy_symbols_if_requested(symbols, args.skip_unhealthy_symbols, args.provider)
    service = DemoBotService(settings, provider, database)

    print(
        "demo_bot=started "
        f"mode=paper broker={args.broker} provider={provider.name} interval_seconds={config.interval_seconds} "
        f"style={style.value} symbols={','.join(symbols)}"
    )
    print("Press Ctrl+C to stop.")
    try:
        while True:
            print(f"cycle_start={datetime.now().isoformat(timespec='seconds')}")
            result = service.run_cycle(style, symbols, watchlist=args.watchlist)
            print_cycle_result(result)
            if args.broker == "mt5_demo":
                _submit_created_orders_to_mt5_demo(settings, database, result)
            print(f"sleep_seconds={config.interval_seconds}")
            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        print("demo_bot=stopped reason=keyboard_interrupt")


def _submit_created_orders_to_mt5_demo(settings, database, result) -> None:
    order_ids = created_order_ids(result)
    if not order_ids:
        print("broker_submit=skipped mode=mt5_demo reason=no accepted paper orders")
        return
    broker = MT5DemoBroker(settings)
    try:
        account = broker.connect()
        print(f"mt5_demo_account login={account.account_id} server={account.server} balance={account.balance} currency={account.currency}")
        paper_orders = {order.order_id: order for order in database.load_paper_orders()}
        broker_orders = []
        broker_events = []
        for order_id in order_ids:
            paper_order = paper_orders.get(order_id)
            if paper_order is None:
                raise SystemExit(f"paper order {order_id} was not found for mt5_demo submission")
            broker_order = broker.place_order(paper_order.request)
            broker_orders.append(broker_order)
            broker_events.extend(broker_order.events)
            print_broker_result(order_id, broker_order.broker_order_id)
        database.save_broker_orders(broker_orders)
        database.save_trade_events(broker_events)
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
