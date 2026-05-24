"""MT5 availability helpers for local-vs-cloud test execution."""

from __future__ import annotations

import importlib
from typing import Any

MT5_CLOUD_UNAVAILABLE_MESSAGE = "MT5 terminal is not available in cloud environment."


def _load_mt5_module() -> Any | None:
    try:
        return importlib.import_module("MetaTrader5")
    except ModuleNotFoundError:
        return None


def is_mt5_available() -> bool:
    """Return True only when MetaTrader5 package and terminal are both reachable."""
    mt5 = _load_mt5_module()
    if mt5 is None:
        return False

    initialized = False
    try:
        initialized = bool(mt5.initialize())
        return initialized
    except Exception:
        return False
    finally:
        if initialized:
            try:
                mt5.shutdown()
            except Exception:
                pass
