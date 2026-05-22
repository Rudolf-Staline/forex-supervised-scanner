"""Discover available MT5 symbols for multi-asset demo configuration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.data.providers import DataProviderError, initialize_mt5_terminal, mt5_last_error
from app.data.mt5_symbols_health import load_mt5_module

KEYWORDS = [
    "gold",
    "xau",
    "silver",
    "xag",
    "oil",
    "brent",
    "wti",
    "us500",
    "spx",
    "nas",
    "nasdaq",
    "us30",
    "dow",
    "ger",
    "dax",
    "uk100",
    "ftse",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover MT5 commodity/index symbols. No orders are sent.")
    parser.add_argument("--keywords", nargs="+", default=KEYWORDS)
    args = parser.parse_args()

    load_dotenv()
    try:
        mt5 = load_mt5_module()
        initialize_mt5_terminal(mt5)
    except DataProviderError as exc:
        print(f"mt5_discover_symbols=error reason={exc}")
        raise SystemExit(1) from exc

    try:
        rows = mt5.symbols_get() or []
        matches = [_symbol_payload(row) for row in rows if _matches(row, args.keywords)]
        print(f"mt5_discover_symbols=ok matches={len(matches)}")
        for item in matches:
            print(
                "symbol "
                f"name={item['symbol']} description={item['description']} path={item['path']} "
                f"trade_mode={item['trade_mode']} visible={item['visible']} volume_min={item['volume_min']} "
                f"volume_step={item['volume_step']} spread={item['spread']} point={item['point']} "
                f"digits={item['digits']} tick_value={item['tick_value']} tick_size={item['tick_size']} "
                f"contract_size={item['contract_size']}"
            )
        if not matches:
            print(f"last_error={mt5_last_error(mt5)}")
    finally:
        shutdown = getattr(mt5, "shutdown", None)
        if callable(shutdown):
            shutdown()


def _matches(row: object, keywords: list[str]) -> bool:
    text = " ".join(str(getattr(row, field, "")) for field in ["name", "description", "path", "basis"]).lower()
    return any(keyword.lower() in text for keyword in keywords)


def _symbol_payload(row: object) -> dict[str, object]:
    return {
        "symbol": str(getattr(row, "name", "")),
        "description": str(getattr(row, "description", "")),
        "path": str(getattr(row, "path", "")),
        "trade_mode": getattr(row, "trade_mode", ""),
        "visible": getattr(row, "visible", ""),
        "volume_min": getattr(row, "volume_min", ""),
        "volume_step": getattr(row, "volume_step", ""),
        "spread": getattr(row, "spread", ""),
        "point": getattr(row, "point", ""),
        "digits": getattr(row, "digits", ""),
        "tick_value": getattr(row, "trade_tick_value", getattr(row, "tick_value", "")),
        "tick_size": getattr(row, "trade_tick_size", getattr(row, "tick_size", "")),
        "contract_size": getattr(row, "trade_contract_size", getattr(row, "contract_size", "")),
    }


if __name__ == "__main__":
    main()
