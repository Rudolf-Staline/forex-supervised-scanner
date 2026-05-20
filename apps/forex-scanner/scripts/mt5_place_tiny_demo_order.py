"""Place one tiny MT5 demo order on Deriv-Demo after explicit confirmation.

This script is a manual connectivity/execution probe for a demo account only.
It never runs automatically and refuses to continue unless the local demo
safety lock and Deriv-Demo environment variables are explicit.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.env import load_dotenv
from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.settings import AppSettings, load_settings
from app.execution.mt5_demo_broker import MT5_LOGIN_ENV, MT5_PASSWORD_ENV, MT5_PATH_ENV, MT5_SERVER_ENV
from app.utils.logging import configure_logging

DERIV_DEMO_SERVER = "Deriv-Demo"
CONFIRMATION_TEXT = "DEMO_ORDER"
SCRIPT_CONTEXT = "mt5_place_tiny_demo_order.py"
PREFERRED_FOREX_SYMBOLS = ("EURUSD", "GBPUSD", "USDCHF", "USDJPY", "AUDUSD", "USDCAD")


def main() -> None:
    """Connect to Deriv-Demo and place one minimum-volume demo order."""

    load_dotenv()
    configure_logging()
    settings = load_settings()
    _ensure_deriv_demo_safe_mode(settings)

    mt5 = _load_mt5_module()
    credentials = _mt5_credentials()
    connected = False
    try:
        connected = _initialize_mt5(mt5, settings, credentials)
        account = _require_demo_account(mt5)
        print("mt5_connection=ok mode=mt5_demo demo_only=true broker=Deriv-Demo")
        print(f"account_login={getattr(account, 'login', '')}")
        print(f"server={getattr(account, 'server', '')}")
        print(f"balance={getattr(account, 'balance', '')}")
        print(f"equity={getattr(account, 'equity', '')}")
        print(f"currency={getattr(account, 'currency', '')}")

        symbols = _available_symbol_names(mt5)
        print("available_symbols_sample=" + ", ".join(symbols[:12]))
        symbol = _choose_demo_symbol(mt5, symbols)
        symbol_info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if symbol_info is None or tick is None:
            raise SystemExit(f"MT5 symbol data unavailable for {symbol}")

        volume = _volume_min(symbol_info)
        payload = _build_order_payload(mt5, settings, symbol, symbol_info, tick, volume)
        print(f"selected_symbol={symbol}")
        print(f"volume_min={volume}")
        print(f"order_type=BUY market demo")
        print(f"price={payload.get('price')}")
        print(f"stop_loss={payload.get('sl')}")
        print(f"take_profit={payload.get('tp')}")
        print("warning=Deriv-Demo only; no real-money order is allowed")
        print(f"TYPE {CONFIRMATION_TEXT} TO CONFIRM")
        if input("confirmation: ").strip() != CONFIRMATION_TEXT:
            print("confirmation=cancelled; no order sent")
            return

        result = mt5.order_send(payload)
        _print_order_response(mt5, result)
    finally:
        if connected:
            shutdown = getattr(mt5, "shutdown", None)
            if callable(shutdown):
                shutdown()
            print("mt5_connection=closed")


def _ensure_deriv_demo_safe_mode(settings: AppSettings) -> None:
    try:
        ensure_demo_safe_mode(settings, context=SCRIPT_CONTEXT, allowed_broker_modes=("mt5_demo",))
    except DemoSafetyError as exc:
        raise SystemExit(str(exc)) from exc
    demo_only = os.getenv("MT5_DEMO_ONLY", "").strip().lower()
    if demo_only != "true":
        raise SystemExit(f"demo safety lock blocked {SCRIPT_CONTEXT}: MT5_DEMO_ONLY must be true")
    server = os.getenv(MT5_SERVER_ENV, "").strip()
    if server != DERIV_DEMO_SERVER:
        raise SystemExit(f"demo safety lock blocked {SCRIPT_CONTEXT}: MT5_SERVER must be {DERIV_DEMO_SERVER}")


def _load_mt5_module() -> object:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise SystemExit("MetaTrader5 package is not installed. Install with: python -m pip install -e \".[broker]\"") from exc


def _mt5_credentials() -> dict[str, str | int]:
    login = os.getenv(MT5_LOGIN_ENV)
    password = os.getenv(MT5_PASSWORD_ENV)
    server = os.getenv(MT5_SERVER_ENV)
    missing = [name for name, value in {MT5_LOGIN_ENV: login, MT5_PASSWORD_ENV: password, MT5_SERVER_ENV: server}.items() if not value]
    if missing:
        raise SystemExit(f"missing MT5 demo credential env vars: {', '.join(missing)}")
    try:
        login_value = int(str(login))
    except ValueError as exc:
        raise SystemExit("MT5_LOGIN must be an integer account id") from exc
    credentials: dict[str, str | int] = {
        "login": login_value,
        "password": str(password),
        "server": str(server),
    }
    path = os.getenv(MT5_PATH_ENV)
    if path:
        credentials["path"] = path
    return credentials


def _initialize_mt5(mt5: object, settings: AppSettings, credentials: dict[str, str | int]) -> bool:
    initialize = getattr(mt5, "initialize")
    kwargs: dict[str, Any] = {
        "login": credentials["login"],
        "password": credentials["password"],
        "server": credentials["server"],
        "timeout": int(settings.broker.connect_timeout_seconds * 1000),
    }
    if credentials.get("path"):
        kwargs["path"] = credentials["path"]
    try:
        ok = bool(initialize(**kwargs))
    except TypeError:
        kwargs.pop("timeout", None)
        ok = bool(initialize(**kwargs))
    if not ok:
        last_error = getattr(mt5, "last_error", lambda: "unknown")()
        raise SystemExit(f"MT5 Deriv-Demo initialize failed: {last_error}")
    return True


def _require_demo_account(mt5: object) -> object:
    account = mt5.account_info()
    if account is None:
        raise SystemExit("MT5 account_info unavailable")
    account_server = str(getattr(account, "server", "")).strip()
    if account_server != DERIV_DEMO_SERVER:
        raise SystemExit(f"connected account server must be {DERIV_DEMO_SERVER}, got {account_server}")
    trade_mode = getattr(account, "trade_mode", None)
    demo_constant = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
    if demo_constant is not None and trade_mode is not None and int(trade_mode) != int(demo_constant):
        raise SystemExit("connected MT5 account is not demo; refusing order")
    if demo_constant is None and "demo" not in account_server.lower():
        raise SystemExit("connected MT5 account does not look like demo; refusing order")
    return account


def _available_symbol_names(mt5: object) -> list[str]:
    symbols = mt5.symbols_get() or []
    names = [str(getattr(symbol, "name", symbol)).strip() for symbol in symbols]
    return sorted(name for name in names if name)


def _choose_demo_symbol(mt5: object, symbols: list[str]) -> str:
    for preferred in PREFERRED_FOREX_SYMBOLS:
        matches = [symbol for symbol in symbols if preferred in _canonical_symbol(symbol)]
        for symbol in sorted(matches, key=lambda value: (not _canonical_symbol(value).endswith(preferred), len(value), value)):
            if _symbol_is_usable(mt5, symbol):
                return symbol
    raise SystemExit("no usable demo forex symbol found; expected EURUSD or another major FX pair")


def _symbol_is_usable(mt5: object, symbol: str) -> bool:
    symbol_select = getattr(mt5, "symbol_select", None)
    if callable(symbol_select) and not bool(symbol_select(symbol, True)):
        return False
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None:
        return False
    disabled = getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", None)
    trade_mode = getattr(info, "trade_mode", None)
    if disabled is not None and trade_mode is not None and int(trade_mode) == int(disabled):
        return False
    return _volume_min(info) > 0


def _volume_min(symbol_info: object) -> float:
    raw = getattr(symbol_info, "volume_min", None)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        raise SystemExit("selected symbol does not expose a valid volume_min")
    return value


def _build_order_payload(mt5: object, settings: AppSettings, symbol: str, symbol_info: object, tick: object, volume: float) -> dict[str, object]:
    digits = int(getattr(symbol_info, "digits", 5) or 5)
    point = float(getattr(symbol_info, "point", 0.0) or 0.0)
    ask = float(getattr(tick, "ask", 0.0) or 0.0)
    if ask <= 0.0 or point <= 0.0:
        raise SystemExit(f"selected symbol {symbol} has invalid ask/point data")
    distance_points = max(
        int(getattr(symbol_info, "trade_stops_level", 0) or 0) + 10,
        int(getattr(symbol_info, "spread", 0) or 0) * 3,
        100,
    )
    distance = distance_points * point
    return {
        "action": getattr(mt5, "TRADE_ACTION_DEAL"),
        "symbol": symbol,
        "volume": volume,
        "type": getattr(mt5, "ORDER_TYPE_BUY"),
        "price": round(ask, digits),
        "sl": round(ask - distance, digits),
        "tp": round(ask + 2 * distance, digits),
        "deviation": settings.broker.order_deviation_points,
        "magic": settings.broker.magic_number,
        "comment": f"{settings.broker.comment_prefix}:deriv-demo"[:31],
        "type_time": getattr(mt5, "ORDER_TIME_GTC"),
        "type_filling": _filling_mode(mt5, symbol_info),
    }


def _filling_mode(mt5: object, symbol_info: object) -> int:
    raw = getattr(symbol_info, "filling_mode", None)
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return int(getattr(mt5, "ORDER_FILLING_RETURN", getattr(mt5, "ORDER_FILLING_FOK", 0)))


def _print_order_response(mt5: object, result: object | None) -> None:
    if result is None:
        last_error = getattr(mt5, "last_error", lambda: "unknown")()
        print(f"order_response=none last_error={last_error}")
        return
    print("order_response=received")
    print(f"retcode={getattr(result, 'retcode', '')}")
    print(f"comment={getattr(result, 'comment', '')}")
    print(f"order={getattr(result, 'order', '')}")
    print(f"deal={getattr(result, 'deal', '')}")
    print(f"volume={getattr(result, 'volume', '')}")
    print(f"price={getattr(result, 'price', '')}")


def _canonical_symbol(value: str) -> str:
    return "".join(char for char in value.upper() if char.isalnum())


if __name__ == "__main__":
    main()
