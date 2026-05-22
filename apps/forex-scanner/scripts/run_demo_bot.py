"""Run the paper-only demo bot loop from the terminal."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from _demo_bot_cli import (
    add_cycle_arguments,
    calculate_session_wait_seconds,
    created_order_ids,
    evaluate_order_execution_gate,
    filter_tradable_session_symbols_if_requested,
    filter_unhealthy_symbols_if_requested,
    load_demo_runtime,
    next_session_windows_for_symbols,
    normalize_symbols,
    print_next_session_windows,
    print_broker_result,
    print_cycle_result,
    print_execution_gate_explanations,
    recommended_next_run_time,
)
from app.core.types import TradingStyle
from app.execution.demo_bot import DemoBotService
from app.execution.demo_bot_config import DemoBotConfig
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.notifications.notifier import notify_cycle_result


def main() -> None:
    """Run demo bot cycles until interrupted by the operator."""

    parser = argparse.ArgumentParser(description="Run Forex Supervisor demo bot cycles in paper/demo mode.")
    add_cycle_arguments(parser)
    add_session_wait_arguments(parser)
    args = parser.parse_args()

    settings, database, provider = load_demo_runtime(
        "run_demo_bot.py",
        provider_name=args.provider,
        broker_mode=args.broker,
        debug_market_data=args.debug_market_data,
    )
    config = DemoBotConfig.from_settings(settings)
    style = TradingStyle(args.style)
    base_symbols = normalize_symbols(args.symbols, args.watchlist, args.asset_class)
    base_symbols = filter_unhealthy_symbols_if_requested(base_symbols, args.skip_unhealthy_symbols, args.provider)
    if args.show_next_windows:
        print_next_session_windows(base_symbols)
    service = DemoBotService(settings, provider, database)

    print(
        "demo_bot=started "
        f"mode=paper broker={args.broker} provider={provider.name} interval_seconds={config.interval_seconds} "
        f"style={style.value} symbols={','.join(base_symbols)}"
    )
    print("Press Ctrl+C to stop.")
    try:
        while True:
            print(f"cycle_start={datetime.now().isoformat(timespec='seconds')}")
            symbols = filter_tradable_session_symbols_if_requested(base_symbols, args.only_tradable_session, broker_mode=args.broker)
            if not symbols:
                if args.show_next_windows:
                    print_next_session_windows(base_symbols)
                print("cycle=skipped reason=no_tradable_symbols_now orders_created=0")
                sleep_seconds = config.interval_seconds
                if args.wait_for_session:
                    recommended_time = recommended_next_run_time(next_session_windows_for_symbols(base_symbols))
                    sleep_seconds, wait_capped = calculate_session_wait_seconds(
                        recommended_time,
                        now_utc=datetime.now(timezone.utc),
                        max_wait_seconds=args.max_session_wait_seconds,
                    )
                    print(f"recommended_next_run_time={recommended_time}")
                    print(f"session_wait_seconds={sleep_seconds}")
                    print(f"wait_capped={str(wait_capped).lower()}")
                    if args.dry_wait:
                        print("dry_wait=true action=skip_sleep")
                        break
                print(f"sleep_seconds={sleep_seconds}")
                time.sleep(sleep_seconds)
                continue
            result = service.run_cycle(style, symbols, watchlist=args.watchlist)
            print_cycle_result(result)
            notify_cycle_result(result, broker_mode=args.broker)
            if args.explain_execution_gate and args.broker != "mt5_demo":
                print_execution_gate_explanations(result, database, settings, args.broker)
            if args.broker == "mt5_demo":
                _submit_created_orders_to_mt5_demo(settings, database, result)
            print(f"sleep_seconds={config.interval_seconds}")
            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        print("demo_bot=stopped reason=keyboard_interrupt")


def add_session_wait_arguments(parser: argparse.ArgumentParser) -> None:
    """Add continuous-bot session waiting controls."""

    parser.add_argument(
        "--wait-for-session",
        action="store_true",
        help="When all symbols are off-hours, sleep until the next configured tradable session.",
    )
    parser.add_argument(
        "--max-session-wait-seconds",
        type=int,
        default=86400,
        help="Maximum sleep duration when --wait-for-session is active. Default: 86400.",
    )
    parser.add_argument(
        "--dry-wait",
        action="store_true",
        help="Print the session wait that would be used, then exit without sleeping.",
    )


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
            mapper = broker.mapper
            if mapper is None or broker.mt5 is None:
                raise SystemExit("MT5 demo broker mapper is unavailable")
            mt5_symbol = mapper.map_symbol(paper_order.request.symbol)
            symbol_info = broker.mt5.symbol_info(mt5_symbol)
            gate = evaluate_order_execution_gate(
                settings,
                database,
                paper_order,
                broker_mode="mt5_demo",
                account=account,
                mt5=broker.mt5,
                mt5_symbol=mt5_symbol,
                symbol_info=symbol_info,
            )
            print(f"demo_execution_gate={gate.status} paper_order_id={order_id} reason={'; '.join(gate.reasons) if gate.reasons else 'all checks passed'}")
            if not gate.allowed:
                raise SystemExit(f"MT5 demo submission blocked by execution gate for paper_order_id={order_id}")
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
