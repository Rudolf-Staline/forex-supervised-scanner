"""Run exactly one paper-only demo bot cycle from the terminal."""

from __future__ import annotations

import argparse

from _demo_bot_cli import add_cycle_arguments, load_demo_runtime, normalize_symbols, print_cycle_result
from app.core.types import TradingStyle
from app.execution.demo_bot import DemoBotService


def main() -> None:
    """Run a single demo bot cycle and print every decision."""

    parser = argparse.ArgumentParser(description="Run one Forex Supervisor demo bot cycle in paper/demo mode.")
    add_cycle_arguments(parser)
    args = parser.parse_args()

    settings, database, provider = load_demo_runtime("run_one_cycle.py")
    style = TradingStyle(args.style)
    symbols = normalize_symbols(args.symbols)
    result = DemoBotService(settings, provider, database).run_cycle(style, symbols)
    print_cycle_result(result)


if __name__ == "__main__":
    main()
