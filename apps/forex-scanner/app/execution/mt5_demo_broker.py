"""FTMO/MetaTrader 5 demo-only broker helper.

This adapter is intentionally separate from broker_live flows. It is only for
explicit operator-triggered MT5 demo testing and never enables live trading.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.safety import DemoSafetyError, ensure_mt5_demo_safe_mode
from app.config.settings import AppSettings
from app.config.instruments import AssetClass, canonical_symbol, instrument_for_symbol
from app.core.types import DirectionBias
from app.data.mt5_symbol_resolver import MT5SymbolResolver
from app.execution.broker import BrokerExecutionError, append_broker_transition
from app.execution.models import BrokerAccountState, BrokerErrorCategory, BrokerOrderState, ExecutionOrder, OrderRequest, OrderStatus, TradeEventType
from app.execution.mt5_filling import filling_attempts_payload, resolve_mt5_filling_mode_or_try_fallbacks
from app.risk.position_sizing import PositionSizeResult, calculate_position_size

MT5_DEMO_MODE = "mt5_demo"
MT5_LOGIN_ENV = "MT5_LOGIN"
MT5_PASSWORD_ENV = "MT5_PASSWORD"
MT5_SERVER_ENV = "MT5_SERVER"
MT5_PATH_ENV = "MT5_PATH"
DERIV_DEMO_SERVER = "Deriv-Demo"
RISK_PER_TRADE_PERCENT_ENV = "RISK_PER_TRADE_PERCENT"
MAX_VOLUME_PER_TRADE_ENV = "MAX_VOLUME_PER_TRADE"
POSITION_SIZING_MODE_ENV = "POSITION_SIZING_MODE"
DEFAULT_RISK_PER_TRADE_PERCENT = 0.25
DEFAULT_MAX_VOLUME_PER_TRADE = 0.05
ALLOW_MULTI_ASSET_DEMO_TRADING_ENV = "ALLOW_MULTI_ASSET_DEMO_TRADING"

LOGGER = logging.getLogger(__name__)


class MT5SymbolMapper:
    """Map internal symbols such as EUR/USD to available MT5 symbols."""

    def __init__(self, mt5: object) -> None:
        self.mt5 = mt5

    def map_symbol(self, internal_symbol: str) -> str:
        """Return a selected MT5 symbol or raise for unknown symbols."""

        if not _canonical_symbol(internal_symbol):
            raise BrokerExecutionError(f"unknown MT5 symbol mapping for {internal_symbol}", BrokerErrorCategory.CONFIGURATION)
        resolution = MT5SymbolResolver(self.mt5, require_bars=False).resolve(internal_symbol, require_bars=False)
        if not resolution.ok or not resolution.mt5_symbol:
            if resolution.reason == "symbol_select_failed" and resolution.mt5_symbol:
                raise BrokerExecutionError(f"MT5 symbol {resolution.mt5_symbol} could not be selected", BrokerErrorCategory.CONFIGURATION)
            raise BrokerExecutionError(f"unknown MT5 symbol mapping for {internal_symbol}", BrokerErrorCategory.CONFIGURATION)
        return resolution.mt5_symbol

    def available_symbols(self) -> list[str]:
        symbols_get = getattr(self.mt5, "symbols_get", None)
        if not callable(symbols_get):
            return []
        rows = symbols_get() or []
        return [str(getattr(row, "name", row)) for row in rows if str(getattr(row, "name", row)).strip()]


class MT5DemoBroker:
    """Demo-only MT5 adapter for explicit FTMO Free Trial testing."""

    def __init__(self, settings: AppSettings, *, mt5_module: object | None = None) -> None:
        try:
            ensure_mt5_demo_safe_mode(settings, context="MT5 demo broker")
        except DemoSafetyError as exc:
            raise BrokerExecutionError(str(exc), BrokerErrorCategory.CONFIGURATION) from exc
        self.settings = settings
        self.mt5 = mt5_module
        self.connected = False
        self.mapper: MT5SymbolMapper | None = None
        self.account: object | None = None

    def connect(self) -> BrokerAccountState:
        """Connect to MT5 and refuse non-demo accounts."""

        mt5 = self.mt5 or _load_mt5_module()
        if mt5 is None:
            raise BrokerExecutionError("MetaTrader5 package is not installed", BrokerErrorCategory.CONFIGURATION)
        credentials = _mt5_credentials()
        timeout_ms = int(self.settings.broker.connect_timeout_seconds * 1000)
        initialize = getattr(mt5, "initialize")
        kwargs: dict[str, Any] = {
            "login": credentials["login"],
            "password": credentials["password"],
            "server": credentials["server"],
            "timeout": timeout_ms,
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
            raise BrokerExecutionError(f"MT5 demo initialize failed: {last_error}", BrokerErrorCategory.CONNECTIVITY)

        account = getattr(mt5, "account_info")()
        if account is None:
            self.disconnect()
            raise BrokerExecutionError("MT5 demo account_info unavailable", BrokerErrorCategory.ACCOUNT_UNAVAILABLE)
        account_server = str(getattr(account, "server", "")).strip()
        if account_server != DERIV_DEMO_SERVER:
            self.disconnect()
            raise BrokerExecutionError(f"MT5 demo account server must be {DERIV_DEMO_SERVER}, got {account_server}", BrokerErrorCategory.CONFIGURATION)
        if not _account_is_demo(mt5, account):
            self.disconnect()
            raise BrokerExecutionError("MT5 account is not a demo account; refusing mt5_demo mode", BrokerErrorCategory.CONFIGURATION)

        self.mt5 = mt5
        self.connected = True
        self.mapper = MT5SymbolMapper(mt5)
        self.account = account
        return _account_state(account)

    def disconnect(self) -> None:
        """Shutdown MT5 if connected."""

        if self.mt5 is not None and self.connected:
            shutdown = getattr(self.mt5, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self.connected = False

    def query_account_state(self) -> BrokerAccountState:
        """Return a sanitized demo account snapshot."""

        mt5 = self._connected_mt5()
        account = getattr(mt5, "account_info")()
        if account is None:
            raise BrokerExecutionError("MT5 demo account_info unavailable", BrokerErrorCategory.ACCOUNT_UNAVAILABLE)
        if not _account_is_demo(mt5, account):
            raise BrokerExecutionError("MT5 account is not a demo account; refusing mt5_demo mode", BrokerErrorCategory.CONFIGURATION)
        return _account_state(account)

    def place_order(self, request: OrderRequest) -> ExecutionOrder:
        """Place a very small pending order on the connected demo account."""

        mt5 = self._connected_mt5()
        account = self.query_account_state()
        if not account.can_trade:
            raise BrokerExecutionError("MT5 demo account is not tradable", BrokerErrorCategory.ACCOUNT_UNAVAILABLE)
        mapper = self.mapper or MT5SymbolMapper(mt5)
        instrument = instrument_for_symbol(request.symbol)
        if instrument.asset_class != AssetClass.FOREX and os.getenv(ALLOW_MULTI_ASSET_DEMO_TRADING_ENV, "false").strip().lower() != "true":
            raise BrokerExecutionError(
                f"scan_only reason=ALLOW_MULTI_ASSET_DEMO_TRADING is false for asset_class={instrument.asset_class.value}",
                BrokerErrorCategory.CONFIGURATION,
            )
        symbol = mapper.map_symbol(request.symbol)
        tick = getattr(mt5, "symbol_info_tick")(symbol)
        if tick is None:
            raise BrokerExecutionError(f"MT5 demo tick unavailable for {symbol}", BrokerErrorCategory.CONNECTIVITY)
        symbol_info = getattr(mt5, "symbol_info")(symbol)
        if symbol_info is None:
            raise BrokerExecutionError(f"MT5 demo symbol_info unavailable for {symbol}", BrokerErrorCategory.CONNECTIVITY)
        try:
            sizing = _demo_position_size(account, request, symbol_info)
        except ValueError as exc:
            raise BrokerExecutionError(f"MT5 demo position sizing failed: {exc}", BrokerErrorCategory.CONFIGURATION) from exc
        broker_request = request.model_copy(update={"quantity_units": sizing.final_volume})
        LOGGER.info(
            "MT5 demo position sizing",
            extra={
                "symbol": symbol,
                "asset_class": instrument.asset_class.value,
                "instrument_config_used": instrument.logical_symbol,
                "balance": account.balance,
                "risk_percent": sizing.risk_percent,
                "calculated_volume": sizing.calculated_volume,
                "final_volume": sizing.final_volume,
                "stop_distance": sizing.stop_distance,
            },
        )
        base_payload = _order_payload(mt5, symbol, broker_request, float(tick.ask), float(tick.bid), self.settings)
        now = datetime.now(timezone.utc)
        order = ExecutionOrder(
            order_id=str(uuid.uuid4()),
            request=broker_request,
            status=OrderStatus.PENDING,
            created_at=now,
            signal_timestamp=broker_request.signal_timestamp,
            initial_stop_loss=broker_request.stop_loss,
            broker_mode=MT5_DEMO_MODE,
            broker_name="mt5",
            broker_state=BrokerOrderState.INTENT_CREATED,
            broker_submission={key: value for key, value in base_payload.items() if key != "password"},
            execution_assumptions={
                "broker": "mt5",
                "mode": MT5_DEMO_MODE,
                "live_money": False,
                "demo_only": True,
                "position_sizing_mode": os.getenv(POSITION_SIZING_MODE_ENV, "auto").strip().lower() or "auto",
                "asset_class": instrument.asset_class.value,
                "instrument_config_used": instrument.logical_symbol,
                "balance": account.balance or 0.0,
                "risk_percent": sizing.risk_percent,
                "calculated_volume": sizing.calculated_volume,
                "final_volume": sizing.final_volume,
                "stop_distance": sizing.stop_distance,
            },
        )
        order = append_broker_transition(order, BrokerOrderState.INTENT_CREATED, TradeEventType.BROKER_INTENT_CREATED, now)
        order = append_broker_transition(order, BrokerOrderState.PRETRADE_VALIDATED, TradeEventType.BROKER_PRETRADE_VALIDATED, now)
        order = append_broker_transition(order, BrokerOrderState.SUBMIT_REQUESTED, TradeEventType.BROKER_SUBMIT_REQUESTED, now, payload={"symbol": symbol, "mode": MT5_DEMO_MODE})
        resolution = resolve_mt5_filling_mode_or_try_fallbacks(
            mt5,
            symbol=symbol,
            symbol_info=symbol_info,
            build_payload=lambda filling_mode: {**base_payload, "type_filling": filling_mode},
            logger=LOGGER,
        )
        payload = resolution.payload or base_payload
        attempts_payload = filling_attempts_payload(resolution.attempts)
        attempts_summary = json.dumps(attempts_payload, sort_keys=True)
        result = resolution.result
        if result is None:
            order = append_broker_transition(
                order,
                BrokerOrderState.MANUAL_INTERVENTION_REQUIRED,
                TradeEventType.MANUAL_INTERVENTION_REQUIRED,
                now,
                reason="MT5 demo order_send returned no acknowledgement",
                payload={"symbol": symbol, "filling_attempts": attempts_summary},
            )
            raise BrokerExecutionError("MT5 demo order_send returned no acknowledgement", BrokerErrorCategory.TIMEOUT)
        retcode = int(getattr(result, "retcode", -1))
        broker_order_id = str(getattr(result, "order", ""))
        acknowledgement = {
            "retcode": retcode,
            "comment": str(getattr(result, "comment", "")),
            "broker_order_id": broker_order_id,
            "filling_mode": resolution.filling_mode_label,
            "filling_mode_value": resolution.filling_mode,
            "filling_attempts": attempts_summary,
        }
        success_codes = {int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)), int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008))}
        if retcode not in success_codes:
            order = order.model_copy(update={"broker_acknowledgement": acknowledgement, "rejection_reason": acknowledgement["comment"]})
            order = append_broker_transition(order, BrokerOrderState.REJECTED, TradeEventType.BROKER_REJECTED, now, reason=acknowledgement["comment"], payload=acknowledgement)
            raise BrokerExecutionError(f"MT5 demo rejected order: {acknowledgement['comment']}", BrokerErrorCategory.ORDER_REJECTED)
        order = order.model_copy(update={"broker_order_id": broker_order_id, "broker_acknowledgement": acknowledgement})
        order = order.model_copy(update={"broker_submission": {key: value for key, value in payload.items() if key != "password"}})
        order = append_broker_transition(
            order,
            BrokerOrderState.SUBMITTED,
            TradeEventType.BROKER_SUBMITTED,
            now,
            payload={
                "broker_order_id": broker_order_id,
                "mode": MT5_DEMO_MODE,
                "filling_mode": resolution.filling_mode_label,
                "filling_mode_value": resolution.filling_mode,
            },
        )
        order = append_broker_transition(order, BrokerOrderState.ACKNOWLEDGED, TradeEventType.BROKER_ACKNOWLEDGED, now, payload=acknowledgement)
        return order

    def _connected_mt5(self) -> object:
        if self.mt5 is None or not self.connected:
            raise BrokerExecutionError("MT5 demo connection was not established", BrokerErrorCategory.CONNECTIVITY)
        return self.mt5


def _order_payload(mt5: object, symbol: str, request: OrderRequest, ask: float, bid: float, settings: AppSettings) -> dict[str, object]:
    return {
        "action": getattr(mt5, "TRADE_ACTION_PENDING"),
        "symbol": symbol,
        "volume": request.quantity_units,
        "type": _pending_order_type(mt5, request, ask, bid),
        "price": request.entry_price,
        "sl": request.stop_loss,
        "tp": request.take_profit,
        "deviation": settings.broker.order_deviation_points,
        "magic": settings.broker.magic_number,
        "comment": f"{settings.broker.comment_prefix}:mt5_demo"[:31],
        "type_time": getattr(mt5, "ORDER_TIME_GTC"),
    }


def _pending_order_type(mt5: object, request: OrderRequest, ask: float, bid: float) -> int:
    if request.direction == DirectionBias.LONG:
        return int(getattr(mt5, "ORDER_TYPE_BUY_STOP") if request.entry_price >= ask else getattr(mt5, "ORDER_TYPE_BUY_LIMIT"))
    if request.direction == DirectionBias.SHORT:
        return int(getattr(mt5, "ORDER_TYPE_SELL_STOP") if request.entry_price <= bid else getattr(mt5, "ORDER_TYPE_SELL_LIMIT"))
    raise BrokerExecutionError("MT5 demo only accepts long or short orders", BrokerErrorCategory.CONFIGURATION)


def _demo_position_size(account: BrokerAccountState, request: OrderRequest, symbol_info: object) -> PositionSizeResult:
    instrument = instrument_for_symbol(request.symbol)
    mode = os.getenv(POSITION_SIZING_MODE_ENV, "auto").strip().lower() or "auto"
    max_volume = instrument.max_volume if instrument.asset_class != AssetClass.FOREX else _env_float(MAX_VOLUME_PER_TRADE_ENV, DEFAULT_MAX_VOLUME_PER_TRADE)
    require_tick_value = instrument.asset_class != AssetClass.FOREX
    if mode != "auto":
        fixed_volume = min(float(request.quantity_units), max_volume)
        return calculate_position_size(
            balance=account.balance or 1.0,
            risk_percent=instrument.risk_percent if instrument.asset_class != AssetClass.FOREX else DEFAULT_RISK_PER_TRADE_PERCENT,
            entry_price=request.entry_price,
            stop_loss=request.stop_loss,
            symbol_info=symbol_info,
            max_volume=fixed_volume,
            require_tick_value=require_tick_value,
        )
    risk_percent = instrument.risk_percent if instrument.asset_class != AssetClass.FOREX else _env_float(RISK_PER_TRADE_PERCENT_ENV, DEFAULT_RISK_PER_TRADE_PERCENT)
    return calculate_position_size(
        balance=account.balance or 0.0,
        risk_percent=risk_percent,
        entry_price=request.entry_price,
        stop_loss=request.stop_loss,
        symbol_info=symbol_info,
        max_volume=max_volume,
        require_tick_value=require_tick_value,
    )


def _mt5_credentials() -> dict[str, str | int]:
    login = os.getenv(MT5_LOGIN_ENV)
    password = os.getenv(MT5_PASSWORD_ENV)
    server = os.getenv(MT5_SERVER_ENV)
    missing = [name for name, value in {MT5_LOGIN_ENV: login, MT5_PASSWORD_ENV: password, MT5_SERVER_ENV: server}.items() if not value]
    if missing:
        raise BrokerExecutionError(f"missing MT5 demo credential env vars: {', '.join(missing)}", BrokerErrorCategory.CONFIGURATION)
    if str(server).strip() != DERIV_DEMO_SERVER:
        raise BrokerExecutionError(f"{MT5_SERVER_ENV} must be {DERIV_DEMO_SERVER}", BrokerErrorCategory.CONFIGURATION)
    try:
        login_value = int(str(login))
    except ValueError as exc:
        raise BrokerExecutionError("MT5_LOGIN must be an integer account id", BrokerErrorCategory.CONFIGURATION) from exc
    payload: dict[str, str | int] = {
        "login": login_value,
        "password": str(password),
        "server": str(server),
    }
    path = os.getenv(MT5_PATH_ENV)
    if path:
        payload["path"] = path
    return payload


def _account_state(account: object) -> BrokerAccountState:
    return BrokerAccountState(
        broker="mt5",
        mode=MT5_DEMO_MODE,
        connected=True,
        can_trade=bool(getattr(account, "trade_allowed", True)),
        balance=_optional_float(getattr(account, "balance", None)),
        equity=_optional_float(getattr(account, "equity", None)),
        free_margin=_optional_float(getattr(account, "margin_free", None)),
        currency=str(getattr(account, "currency", "")) or None,
        account_id=str(getattr(account, "login", "")) or None,
        server=str(getattr(account, "server", "")) or None,
        is_demo=True,
        retrieved_at=datetime.now(timezone.utc),
        health_status="healthy" if bool(getattr(account, "trade_allowed", True)) else "connected_not_tradable",
        raw_summary={
            "login": str(getattr(account, "login", "")),
            "server": str(getattr(account, "server", "")),
            "trade_mode": str(getattr(account, "trade_mode", "")),
        },
    )


def _account_is_demo(mt5: object, account: object) -> bool:
    trade_mode = getattr(account, "trade_mode", None)
    demo_constant = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
    if demo_constant is not None and trade_mode is not None:
        return int(trade_mode) == int(demo_constant)
    server = str(getattr(account, "server", "")).lower()
    return "demo" in server or "trial" in server


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise BrokerExecutionError(f"{name} must be a number", BrokerErrorCategory.CONFIGURATION) from exc
    if value <= 0:
        raise BrokerExecutionError(f"{name} must be greater than zero", BrokerErrorCategory.CONFIGURATION)
    return value


def _canonical_symbol(value: str) -> str:
    return canonical_symbol(value)


def _load_mt5_module() -> object | None:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError:
        return None
