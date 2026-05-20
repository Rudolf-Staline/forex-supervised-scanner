"""Run the paper-only demo bot loop from the terminal."""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from _demo_bot_cli import add_cycle_arguments, load_demo_runtime, normalize_symbols, print_cycle_result
from app.core.types import TradingStyle
from app.execution.demo_bot import DemoBotService
from app.execution.demo_bot_config import DemoBotConfig


def main() -> None:
    """Run demo bot cycles until interrupted by the operator."""

    parser = argparse.ArgumentParser(description="Run Forex Supervisor demo bot cycles in paper/demo mode.")
    add_cycle_arguments(parser)
    args = parser.parse_args()

    settings, database, provider = load_demo_runtime("run_demo_bot.py")
    config = DemoBotConfig.from_settings(settings)
    style = TradingStyle(args.style)
    symbols = normalize_symbols(args.symbols)
    service = DemoBotService(settings, provider, database)

    print(
        "demo_bot=started "
        f"mode=paper interval_seconds={config.interval_seconds} style={style.value} symbols={','.join(symbols)}"
    )
    print("Press Ctrl+C to stop.")
    try:
        while True:
            print(f"cycle_start={datetime.now().isoformat(timespec='seconds')}")
            result = service.run_cycle(style, symbols)
            print_cycle_result(result)
            print(f"sleep_seconds={config.interval_seconds}")
            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        print("demo_bot=stopped reason=keyboard_interrupt")


if __name__ == "__main__":
    main()
