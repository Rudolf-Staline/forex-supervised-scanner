"""Diagnose MT5 market-data candles without sending any order."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.core.types import Timeframe
from app.data.providers import initialize_mt5_terminal, mt5_last_error, mt5_rates_to_ohlcv, to_mt5_symbol
from app.data.validation import validate_ohlcv
from app.utils.logging import configure_logging

SYMBOLS = ["EURUSD", "GBPUSD", "USDCHF"]
TIMEFRAMES = [Timeframe.H1, Timeframe.M15, Timeframe.M5]
TIMEFRAME_ATTRS = {
    Timeframe.H1: "TIMEFRAME_H1",
    Timeframe.M15: "TIMEFRAME_M15",
    Timeframe.M5: "TIMEFRAME_M5",
}


def main() -> None:
    """Print raw and normalized MT5 candle diagnostics."""

    load_dotenv()
    configure_logging()
    mt5 = _load_mt5_module()
    connected = False
    try:
        initialize_mt5_terminal(mt5)
        connected = True
        account = mt5.account_info()
        print(f"account_login={getattr(account, 'login', '') if account else ''}")
        print(f"server={getattr(account, 'server', '') if account else ''}")
        for symbol in SYMBOLS:
            _diagnose_symbol(mt5, symbol)
    finally:
        if connected:
            shutdown = getattr(mt5, "shutdown", None)
            if callable(shutdown):
                shutdown()
            print("mt5_connection=closed")


def _diagnose_symbol(mt5: object, symbol: str) -> None:
    mapped_symbol = to_mt5_symbol(symbol)
    selected = bool(mt5.symbol_select(mapped_symbol, True))
    info = mt5.symbol_info(mapped_symbol)
    print(f"symbol={symbol} mt5_symbol={mapped_symbol}")
    print(f"symbol_visible={getattr(info, 'visible', '') if info else ''}")
    print(f"symbol_selected={selected}")
    print(f"last_error={mt5_last_error(mt5)}")
    for timeframe in TIMEFRAMES:
        tf_constant = getattr(mt5, TIMEFRAME_ATTRS[timeframe])
        rates = mt5.copy_rates_from_pos(mapped_symbol, tf_constant, 0, 200)
        raw = pd.DataFrame(rates if rates is not None else [])
        print(f"timeframe={timeframe.value}")
        print(f"raw_columns={list(raw.columns)}")
        print(f"rows_before_validation={len(raw)}")
        if not raw.empty:
            print(f"raw_head={raw.head(3).to_dict(orient='records')}")
            print(f"raw_tail={raw.tail(3).to_dict(orient='records')}")
            print(f"raw_dtypes={{{', '.join(f'{column}: {dtype}' for column, dtype in raw.dtypes.items())}}}")
            print(f"raw_nan_counts={raw.isna().sum().to_dict()}")
        print(f"last_error={mt5_last_error(mt5)}")
        try:
            normalized = mt5_rates_to_ohlcv(symbol, rates if rates is not None else [], mt5_symbol=mapped_symbol, timeframe=timeframe)
            print(f"normalized_columns={list(normalized.columns)}")
            print(f"normalized_rows_before_validation={len(normalized)}")
            cleaned = validate_ohlcv(normalized, min_rows=120)
            print(f"rows_after_validation={len(cleaned)}")
            print(f"last_normalized_candle={cleaned.tail(1).to_dict(orient='index')}")
        except Exception as exc:
            print(f"validation_error={exc}")


def _load_mt5_module() -> object:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise SystemExit("MetaTrader5 package is not installed. Install with: python -m pip install -e \".[broker]\"") from exc


if __name__ == "__main__":
    main()
