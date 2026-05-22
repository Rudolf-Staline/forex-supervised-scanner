"""Run exactly one paper-only demo bot cycle from the terminal."""

from __future__ import annotations

import argparse

from _demo_bot_cli import (
    add_cycle_arguments,
    created_order_ids,
    filter_tradable_session_symbols_if_requested,
    filter_unhealthy_symbols_if_requested,
    evaluate_order_execution_gate,
    load_demo_runtime,
    normalize_symbols,
    print_next_session_windows,
    print_broker_result,
    print_cycle_result,
    print_execution_gate_explanations,
)
from app.core.types import TradingStyle
from app.execution.demo_bot import DemoBotService
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.notifications.notifier import notify_cycle_result


def main() -> None:
    """Run a single demo bot cycle and print every decision."""

    parser = argparse.ArgumentParser(description="Run one Forex Supervisor demo bot cycle in paper/demo mode.")
    add_cycle_arguments(parser)
    args = parser.parse_args()

    settings, database, provider = load_demo_runtime(
        "run_one_cycle.py",
        provider_name=args.provider,
        broker_mode=args.broker,
        debug_market_data=args.debug_market_data,
    )
    style = TradingStyle(args.style)
    symbols = normalize_symbols(args.symbols, args.watchlist, args.asset_class)
    symbols = filter_unhealthy_symbols_if_requested(symbols, args.skip_unhealthy_symbols, args.provider)
    if args.show_next_windows:
        print_next_session_windows(symbols)
    symbols = filter_tradable_session_symbols_if_requested(symbols, args.only_tradable_session, broker_mode=args.broker)
    if not symbols:
        print("cycle=skipped reason=no_tradable_symbols_now orders_created=0")
        return
    print(f"runtime provider={provider.name} broker={args.broker} mode=paper")
    result = DemoBotService(settings, provider, database).run_cycle(style, symbols, watchlist=args.watchlist)
    print_cycle_result(result)
    notify_cycle_result(result, broker_mode=args.broker)
    if args.explain_execution_gate and args.broker != "mt5_demo":
        print_execution_gate_explanations(result, database, settings, args.broker)
    if args.broker == "mt5_demo":
        _submit_created_orders_to_mt5_demo(settings, database, result)


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
