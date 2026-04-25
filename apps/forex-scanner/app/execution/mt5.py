"""MetaTrader 5 broker adapter with sandbox-first safety gates.

The adapter is optional: if the local MetaTrader5 package, terminal, or
credentials are unavailable it reports a non-tradable account state instead of
falling through to unsafe behavior.
"""

from __future__ import annotations

import importlib
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from app.config.settings import AppSettings
from app.core.types import DirectionBias
from app.execution.broker import BrokerExecutionError, append_broker_transition
from app.execution.models import BrokerAccountState, BrokerErrorCategory, BrokerOrderSnapshot, BrokerOrderState, BrokerPositionSnapshot, CloseReason, ExecutionOrder, OrderRequest, OrderStatus, TradeEventType

T = TypeVar("T")


class MT5BrokerExecutor:
    """Broker executor backed by a local MetaTrader 5 terminal."""

    def __init__(self, settings: AppSettings, *, mode: str) -> None:
        self.settings = settings
        self.mode = mode
        self._orders: dict[str, ExecutionOrder] = {}
        self._mt5: object | None = None
        self._connected = False
        self._last_error: str | None = None
        self._last_error_category: BrokerErrorCategory | None = None
        self._consecutive_failures = 0
        self._last_connected_at: datetime | None = None

    def connect(self) -> bool:
        """Initialize the local MT5 terminal connection."""

        mt5 = _load_mt5_module()
        if mt5 is None:
            self._record_failure("MetaTrader5 package is not installed", BrokerErrorCategory.CONFIGURATION)
            raise BrokerExecutionError("MetaTrader5 package is not installed", BrokerErrorCategory.CONFIGURATION)
        path = os.getenv(self.settings.broker.mt5_path_env)
        login_value = os.getenv(self.settings.broker.mt5_login_env)
        password = os.getenv(self.settings.broker.mt5_password_env)
        server = os.getenv(self.settings.broker.mt5_server_env)
        initialize = getattr(mt5, "initialize")
        try:
            if path:
                ok = bool(initialize(path=path, timeout=int(self.settings.broker.connect_timeout_seconds * 1000)))
            elif login_value and password and server:
                ok = bool(initialize(login=int(login_value), password=password, server=server, timeout=int(self.settings.broker.connect_timeout_seconds * 1000)))
            else:
                ok = bool(initialize(timeout=int(self.settings.broker.connect_timeout_seconds * 1000)))
        except TypeError:
            if path:
                ok = bool(initialize(path=path))
            elif login_value and password and server:
                ok = bool(initialize(login=int(login_value), password=password, server=server))
            else:
                ok = bool(initialize())
        if not ok:
            last_error = getattr(mt5, "last_error", lambda: "unknown")()
            message = f"MT5 initialize failed: {last_error}"
            self._record_failure(message, BrokerErrorCategory.CONNECTIVITY)
            raise BrokerExecutionError(message, BrokerErrorCategory.CONNECTIVITY)
        self._mt5 = mt5
        self._connected = True
        self._last_connected_at = datetime.now(timezone.utc)
        self._record_success()
        return True

    def disconnect(self) -> None:
        """Shutdown the MT5 connection if it was initialized by this adapter."""

        if self._mt5 is not None and self._connected:
            shutdown = getattr(self._mt5, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self._connected = False

    def health_check(self) -> BrokerAccountState:
        """Return account state as the broker health check."""

        return self.query_account_state()

    def reconnect(self) -> bool:
        """Disconnect and connect again using the configured MT5 environment."""

        self.disconnect()
        return self.connect()

    def create_order_intent(self, request: OrderRequest) -> OrderRequest:
        """Return a validated MT5 order intent."""

        if request.quantity_units > self.settings.broker.max_volume_lots:
            raise BrokerExecutionError("requested broker volume exceeds configured max_volume_lots")
        return request

    def place_order(self, request: OrderRequest) -> ExecutionOrder:
        """Submit a pending order to MT5 after the caller has passed broker validation."""

        request = self.create_order_intent(request)
        mt5 = self._connected_mt5()
        symbol = _mt5_symbol(request.symbol)
        tick = getattr(mt5, "symbol_info_tick")(symbol)
        if tick is None:
            raise BrokerExecutionError(f"MT5 tick unavailable for {symbol}")
        order_type = _pending_order_type(mt5, request, float(tick.ask), float(tick.bid))
        payload = {
            "action": getattr(mt5, "TRADE_ACTION_PENDING"),
            "symbol": symbol,
            "volume": request.quantity_units,
            "type": order_type,
            "price": request.entry_price,
            "sl": request.stop_loss,
            "tp": request.take_profit,
            "deviation": self.settings.broker.order_deviation_points,
            "magic": self.settings.broker.magic_number,
            "comment": f"{self.settings.broker.comment_prefix}:{request.setup_subtype.value}"[:31],
            "type_time": getattr(mt5, "ORDER_TIME_GTC"),
            "type_filling": getattr(mt5, "ORDER_FILLING_RETURN"),
        }
        now = datetime.now(timezone.utc)
        order = ExecutionOrder(
            order_id=str(uuid.uuid4()),
            request=request,
            status=OrderStatus.PENDING,
            created_at=now,
            signal_timestamp=request.signal_timestamp,
            initial_stop_loss=request.stop_loss,
            broker_mode=self.mode,
            broker_name="mt5",
            broker_state=BrokerOrderState.INTENT_CREATED,
            broker_submission=_jsonable_payload(payload),
            execution_assumptions={"broker": "mt5", "mode": self.mode, "live_money": self.mode == "broker_live"},
        )
        order = append_broker_transition(order, BrokerOrderState.INTENT_CREATED, TradeEventType.BROKER_INTENT_CREATED, now)
        order = append_broker_transition(order, BrokerOrderState.PRETRADE_VALIDATED, TradeEventType.BROKER_PRETRADE_VALIDATED, now)
        order = append_broker_transition(order, BrokerOrderState.SUBMIT_REQUESTED, TradeEventType.BROKER_SUBMIT_REQUESTED, now, payload={"symbol": symbol})
        try:
            result = self._send_order(mt5, payload)
        except BrokerExecutionError as exc:
            order = append_broker_transition(order, BrokerOrderState.RETRY_EXHAUSTED, TradeEventType.BROKER_RETRY_EXHAUSTED, now, reason=str(exc), payload={"category": exc.category.value})
            order = append_broker_transition(order, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED, TradeEventType.MANUAL_INTERVENTION_REQUIRED, now, reason="broker submission acknowledgement failed; verify terminal before retrying")
            self._orders[order.order_id] = order
            raise
        if result is None:
            order = append_broker_transition(order, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED, TradeEventType.MANUAL_INTERVENTION_REQUIRED, now, reason="MT5 order_send returned no acknowledgement")
            self._orders[order.order_id] = order
            raise BrokerExecutionError("MT5 order_send returned no acknowledgement; manual broker review required", BrokerErrorCategory.TIMEOUT)
        retcode = int(getattr(result, "retcode", -1))
        broker_order_id = str(getattr(result, "order", ""))
        acknowledgement = {
            "retcode": retcode,
            "comment": str(getattr(result, "comment", "")),
            "broker_order_id": broker_order_id,
        }
        success_codes = {int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)), int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008))}
        if retcode not in success_codes:
            order = order.model_copy(update={"broker_acknowledgement": acknowledgement, "rejection_reason": acknowledgement["comment"]})
            order = append_broker_transition(order, BrokerOrderState.REJECTED, TradeEventType.BROKER_REJECTED, now, reason=acknowledgement["comment"], payload=acknowledgement)
            self._orders[order.order_id] = order
            raise BrokerExecutionError(f"MT5 rejected order: {acknowledgement['comment']}", BrokerErrorCategory.ORDER_REJECTED)
        order = order.model_copy(update={"broker_order_id": broker_order_id, "broker_acknowledgement": acknowledgement})
        order = append_broker_transition(order, BrokerOrderState.SUBMITTED, TradeEventType.BROKER_SUBMITTED, now, payload={"broker_order_id": broker_order_id})
        order = append_broker_transition(order, BrokerOrderState.ACKNOWLEDGED, TradeEventType.BROKER_ACKNOWLEDGED, now, payload=acknowledgement)
        self._orders[order.order_id] = order
        return order

    def modify_order(self, order_id: str, *, stop_loss: float | None = None, take_profit: float | None = None) -> ExecutionOrder:
        """Modify an MT5 pending/open order's stop or target."""

        order = self._existing_order(order_id)
        if not order.broker_order_id:
            raise BrokerExecutionError("cannot modify broker order without broker_order_id")
        mt5 = self._connected_mt5()
        request = order.request.model_copy(update={key: value for key, value in {"stop_loss": stop_loss, "take_profit": take_profit}.items() if value is not None})
        payload = {
            "action": getattr(mt5, "TRADE_ACTION_MODIFY"),
            "order": int(order.broker_order_id),
            "price": request.entry_price,
            "sl": request.stop_loss,
            "tp": request.take_profit,
        }
        updated = append_broker_transition(order, BrokerOrderState.MODIFY_REQUESTED, TradeEventType.BROKER_MODIFY_REQUESTED, datetime.now(timezone.utc), payload={"broker_order_id": order.broker_order_id})
        result = self._with_retry("modify_order", lambda: getattr(mt5, "order_send")(payload))
        now = datetime.now(timezone.utc)
        if result is None:
            updated = append_broker_transition(updated, BrokerOrderState.RETRY_EXHAUSTED, TradeEventType.BROKER_RETRY_EXHAUSTED, now, reason="MT5 modify returned no acknowledgement")
            self._orders[order_id] = updated
            raise BrokerExecutionError("MT5 modify returned no acknowledgement", BrokerErrorCategory.TIMEOUT)
        updated = updated.model_copy(update={"request": request})
        updated = append_broker_transition(updated, BrokerOrderState.MODIFIED, TradeEventType.BROKER_MODIFIED, now, payload={"broker_order_id": order.broker_order_id})
        self._orders[order_id] = updated
        return updated

    def partial_close_order(self, order_id: str, exit_price: float, fraction: float, reason: str = "manual_partial") -> ExecutionOrder:
        """Partially close a reconciled MT5 position."""

        order = self._existing_order(order_id)
        if not 0.0 < fraction <= 1.0:
            raise ValueError("partial close fraction must be between 0 and 1")
        if not order.broker_position_id:
            raise BrokerExecutionError("partial close requires a reconciled broker_position_id")
        volume = round(min(order.request.quantity_units * fraction, order.request.quantity_units), 2)
        return self._close_position_volume(order, volume, BrokerOrderState.PARTIALLY_FILLED, TradeEventType.BROKER_PARTIALLY_FILLED, reason)

    def close_order(self, order_id: str, exit_price: float, reason: str = "manual") -> ExecutionOrder:
        """Close a reconciled MT5 position."""

        order = self._existing_order(order_id)
        if not order.broker_position_id:
            raise BrokerExecutionError("close requires a reconciled broker_position_id")
        now = datetime.now(timezone.utc)
        updated = append_broker_transition(order, BrokerOrderState.CLOSE_REQUESTED, TradeEventType.BROKER_CLOSE_REQUESTED, now, reason=reason)
        updated = self._close_position_volume(updated, order.request.quantity_units, BrokerOrderState.CLOSED, TradeEventType.BROKER_CLOSED, reason)
        self._orders[order_id] = updated
        return updated

    def cancel_order(self, order_id: str) -> ExecutionOrder:
        """Cancel an MT5 pending order."""

        order = self._existing_order(order_id)
        if not order.broker_order_id:
            raise BrokerExecutionError("cannot cancel broker order without broker_order_id")
        mt5 = self._connected_mt5()
        payload = {"action": getattr(mt5, "TRADE_ACTION_REMOVE"), "order": int(order.broker_order_id)}
        requested = append_broker_transition(order, BrokerOrderState.CANCEL_REQUESTED, TradeEventType.BROKER_CANCEL_REQUESTED, datetime.now(timezone.utc), payload={"broker_order_id": order.broker_order_id})
        result = self._with_retry("cancel_order", lambda: getattr(mt5, "order_send")(payload))
        if result is None:
            requested = append_broker_transition(requested, BrokerOrderState.RETRY_EXHAUSTED, TradeEventType.BROKER_RETRY_EXHAUSTED, datetime.now(timezone.utc), reason="MT5 cancel returned no acknowledgement")
            self._orders[order_id] = requested
            raise BrokerExecutionError("MT5 cancel returned no acknowledgement", BrokerErrorCategory.TIMEOUT)
        now = datetime.now(timezone.utc)
        updated = requested.model_copy(update={"status": OrderStatus.CANCELLED_TRADE, "closed_at": now, "close_reason": CloseReason.CANCELED})
        updated = append_broker_transition(updated, BrokerOrderState.CANCELLED, TradeEventType.BROKER_CANCELLED, now, payload={"broker_order_id": order.broker_order_id})
        self._orders[order_id] = updated
        return updated

    def sync_positions(self) -> list[ExecutionOrder]:
        """Return locally tracked open/pending broker orders."""

        return [order for order in self._orders.values() if order.is_open]

    def query_order_status(self, order_id: str) -> ExecutionOrder:
        """Return locally tracked MT5 order state."""

        order = self._existing_order(order_id)
        if not order.broker_order_id or not self.settings.broker_retry.retry_order_status:
            return order
        try:
            snapshots = self.broker_order_snapshots()
        except BrokerExecutionError as exc:
            updated = append_broker_transition(
                order,
                BrokerOrderState.BROKER_UNREACHABLE,
                TradeEventType.BROKER_UNREACHABLE,
                datetime.now(timezone.utc),
                reason=str(exc),
                payload={"category": exc.category.value},
            )
            self._orders[order_id] = updated
            return updated
        match = next((snapshot for snapshot in snapshots if snapshot.broker_order_id == order.broker_order_id), None)
        if match is not None:
            updated = order.model_copy(update={"broker_state": match.state, "filled_quantity": match.filled_quantity or order.filled_quantity})
            self._orders[order_id] = updated
            return updated
        return order

    def query_account_state(self) -> BrokerAccountState:
        """Return an MT5 account snapshot or a safe non-tradable state."""

        try:
            mt5 = self._with_retry("connect", self._connected_mt5) if self.settings.broker_retry.retry_account_state else self._connected_mt5()
            account = self._with_retry("account_state", lambda: getattr(mt5, "account_info")()) if self.settings.broker_retry.retry_account_state else getattr(mt5, "account_info")()
            if account is None:
                self._record_failure("MT5 account_info unavailable", BrokerErrorCategory.ACCOUNT_UNAVAILABLE)
                return _unavailable_account(self.mode, "MT5 account_info unavailable", BrokerErrorCategory.ACCOUNT_UNAVAILABLE, self._consecutive_failures)
            positions = self._with_retry("positions_get", lambda: getattr(mt5, "positions_get")() or []) if self.settings.broker_retry.retry_position_sync else getattr(mt5, "positions_get")() or []
            orders = self._with_retry("orders_get", lambda: getattr(mt5, "orders_get")() or []) if self.settings.broker_retry.retry_order_status else getattr(mt5, "orders_get")() or []
            trade_allowed = bool(getattr(account, "trade_allowed", False))
            server = str(getattr(account, "server", ""))
            is_demo = "demo" in server.lower() if server else None
            return BrokerAccountState(
                broker="mt5",
                mode=self.mode,
                connected=True,
                can_trade=trade_allowed,
                balance=float(getattr(account, "balance", 0.0)),
                equity=float(getattr(account, "equity", 0.0)),
                free_margin=float(getattr(account, "margin_free", 0.0)),
                currency=str(getattr(account, "currency", "")) or None,
                open_positions=len(positions),
                pending_orders=len(orders),
                account_id=str(getattr(account, "login", "")) or None,
                server=server or None,
                is_demo=is_demo,
                retrieved_at=datetime.now(timezone.utc),
                health_status="healthy" if trade_allowed else "not_tradable",
                consecutive_failures=self._consecutive_failures,
            )
        except BrokerExecutionError as exc:
            return _unavailable_account(self.mode, str(exc), exc.category, self._consecutive_failures)

    def broker_order_snapshots(self) -> list[BrokerOrderSnapshot]:
        """Return MT5 pending-order snapshots for reconciliation."""

        mt5 = self._connected_mt5()
        rows = self._with_retry("orders_get", lambda: getattr(mt5, "orders_get")() or []) if self.settings.broker_retry.retry_reconciliation_refresh else getattr(mt5, "orders_get")() or []
        snapshots: list[BrokerOrderSnapshot] = []
        for row in rows:
            symbol = _display_symbol(str(getattr(row, "symbol", "")))
            snapshots.append(
                BrokerOrderSnapshot(
                    broker_order_id=str(getattr(row, "ticket", "")),
                    symbol=symbol,
                    direction=_direction_from_mt5_type(int(getattr(row, "type", -1))),
                    state=BrokerOrderState.ACKNOWLEDGED,
                    quantity=float(getattr(row, "volume_current", 0.0)),
                    filled_quantity=float(getattr(row, "volume_initial", 0.0)) - float(getattr(row, "volume_current", 0.0)),
                    entry_price=float(getattr(row, "price_open", 0.0)) or None,
                    stop_loss=float(getattr(row, "sl", 0.0)) or None,
                    take_profit=float(getattr(row, "tp", 0.0)) or None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        return snapshots

    def broker_position_snapshots(self) -> list[BrokerPositionSnapshot]:
        """Return MT5 position snapshots for reconciliation."""

        mt5 = self._connected_mt5()
        rows = self._with_retry("positions_get", lambda: getattr(mt5, "positions_get")() or []) if self.settings.broker_retry.retry_reconciliation_refresh else getattr(mt5, "positions_get")() or []
        snapshots: list[BrokerPositionSnapshot] = []
        for row in rows:
            snapshots.append(
                BrokerPositionSnapshot(
                    broker_position_id=str(getattr(row, "ticket", "")),
                    symbol=_display_symbol(str(getattr(row, "symbol", ""))),
                    direction=_direction_from_mt5_type(int(getattr(row, "type", -1))),
                    quantity=float(getattr(row, "volume", 0.0)),
                    entry_price=float(getattr(row, "price_open", 0.0)) or None,
                    stop_loss=float(getattr(row, "sl", 0.0)) or None,
                    take_profit=float(getattr(row, "tp", 0.0)) or None,
                    unrealized_pnl=float(getattr(row, "profit", 0.0)),
                    updated_at=datetime.now(timezone.utc),
                )
            )
        return snapshots

    def reconcile(self) -> list[ExecutionOrder]:
        """Return locally tracked orders; reporting performs anomaly comparison."""

        return list(self._orders.values())

    def _close_position_volume(
        self,
        order: ExecutionOrder,
        volume: float,
        state: BrokerOrderState,
        event_type: TradeEventType,
        reason: str,
    ) -> ExecutionOrder:
        mt5 = self._connected_mt5()
        symbol = _mt5_symbol(order.request.symbol)
        tick = getattr(mt5, "symbol_info_tick")(symbol)
        if tick is None:
            raise BrokerExecutionError(f"MT5 tick unavailable for {symbol}")
        if order.request.direction == DirectionBias.LONG:
            order_type = getattr(mt5, "ORDER_TYPE_SELL")
            price = float(tick.bid)
        else:
            order_type = getattr(mt5, "ORDER_TYPE_BUY")
            price = float(tick.ask)
        payload = {
            "action": getattr(mt5, "TRADE_ACTION_DEAL"),
            "position": int(order.broker_position_id or 0),
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": self.settings.broker.order_deviation_points,
            "magic": self.settings.broker.magic_number,
            "comment": f"{self.settings.broker.comment_prefix}:close"[:31],
        }
        result = self._with_retry("close_position", lambda: getattr(mt5, "order_send")(payload))
        if result is None:
            exhausted = append_broker_transition(order, BrokerOrderState.RETRY_EXHAUSTED, TradeEventType.BROKER_RETRY_EXHAUSTED, datetime.now(timezone.utc), reason="MT5 close returned no acknowledgement")
            self._orders[order.order_id] = exhausted
            raise BrokerExecutionError("MT5 close returned no acknowledgement", BrokerErrorCategory.TIMEOUT)
        retcode = int(getattr(result, "retcode", -1))
        success_codes = {int(getattr(mt5, "TRADE_RETCODE_DONE", 10009)), int(getattr(mt5, "TRADE_RETCODE_PLACED", 10008))}
        now = datetime.now(timezone.utc)
        acknowledgement = {
            "retcode": retcode,
            "comment": str(getattr(result, "comment", "")),
            "deal": str(getattr(result, "deal", "")),
            "closed_volume": volume,
        }
        if retcode not in success_codes:
            rejected = append_broker_transition(order, BrokerOrderState.REJECTED, TradeEventType.BROKER_REJECTED, now, reason=acknowledgement["comment"], payload=acknowledgement)
            self._orders[order.order_id] = rejected
            raise BrokerExecutionError(f"MT5 close rejected: {acknowledgement['comment']}", BrokerErrorCategory.ORDER_REJECTED)
        remaining_fraction = 0.0 if state == BrokerOrderState.CLOSED else round(max(0.0, order.remaining_fraction - volume / max(order.request.quantity_units, 1e-12)), 4)
        status = OrderStatus.FULLY_CLOSED if state == BrokerOrderState.CLOSED or remaining_fraction <= 0.0 else OrderStatus.PARTIALLY_CLOSED
        updated = order.model_copy(
            update={
                "status": status,
                "closed_at": now if status == OrderStatus.FULLY_CLOSED else order.closed_at,
                "exit_price": price,
                "close_reason": CloseReason.MANUAL if status == OrderStatus.FULLY_CLOSED else order.close_reason,
                "remaining_fraction": remaining_fraction,
                "filled_quantity": round(order.filled_quantity + volume, 4),
                "average_fill_price": price,
                "broker_acknowledgement": acknowledgement,
            }
        )
        return append_broker_transition(updated, state, event_type, now, reason=reason, payload=acknowledgement)

    def _connected_mt5(self) -> object:
        if self._mt5 is not None and self._connected:
            return self._mt5
        self.connect()
        if self._mt5 is None:
            raise BrokerExecutionError("MT5 connection was not established", BrokerErrorCategory.CONNECTIVITY)
        return self._mt5

    def _send_order(self, mt5: object, payload: dict[str, object]) -> object | None:
        if self.settings.broker_retry.retry_order_send_on_no_result:
            return self._with_retry("order_send", lambda: getattr(mt5, "order_send")(payload))
        result = getattr(mt5, "order_send")(payload)
        if result is None:
            self._record_failure("MT5 order_send returned no acknowledgement", BrokerErrorCategory.TIMEOUT)
        else:
            self._record_success()
        return result

    def _with_retry(self, operation: str, callback: Callable[[], T | None]) -> T | None:
        attempts = self.settings.broker_retry.max_attempts
        last_message = f"{operation} failed"
        for attempt in range(1, attempts + 1):
            try:
                result = callback()
                if result is not None:
                    self._record_success()
                    return result
                last_message = f"{operation} returned no result on attempt {attempt}"
                self._record_failure(last_message, BrokerErrorCategory.TIMEOUT)
            except BrokerExecutionError as exc:
                last_message = str(exc)
                self._record_failure(last_message, exc.category)
            except Exception as exc:
                last_message = str(exc)
                self._record_failure(last_message, BrokerErrorCategory.UNKNOWN)
            if attempt < attempts and self.settings.broker_retry.backoff_seconds > 0.0:
                time.sleep(self.settings.broker_retry.backoff_seconds)
        raise BrokerExecutionError(f"{operation} retry exhausted: {last_message}", BrokerErrorCategory.RETRY_EXHAUSTED)

    def _record_success(self) -> None:
        self._last_error = None
        self._last_error_category = None
        self._consecutive_failures = 0

    def _record_failure(self, message: str, category: BrokerErrorCategory) -> None:
        self._last_error = message
        self._last_error_category = category
        self._consecutive_failures += 1

    def _existing_order(self, order_id: str) -> ExecutionOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise KeyError(f"unknown MT5 broker order {order_id}") from exc


def _load_mt5_module() -> object | None:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError:
        return None


def _unavailable_account(mode: str, reason: str, category: BrokerErrorCategory, consecutive_failures: int) -> BrokerAccountState:
    return BrokerAccountState(
        broker="mt5",
        mode=mode,
        connected=False,
        can_trade=False,
        raw_summary={"reason": reason},
        retrieved_at=datetime.now(timezone.utc),
        health_status="unavailable",
        last_error=reason,
        error_category=category,
        consecutive_failures=consecutive_failures,
    )


def _mt5_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


def _display_symbol(symbol: str) -> str:
    if len(symbol) == 6:
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def _pending_order_type(mt5: object, request: OrderRequest, ask: float, bid: float) -> int:
    if request.direction == DirectionBias.LONG:
        return int(getattr(mt5, "ORDER_TYPE_BUY_STOP") if request.entry_price >= ask else getattr(mt5, "ORDER_TYPE_BUY_LIMIT"))
    return int(getattr(mt5, "ORDER_TYPE_SELL_STOP") if request.entry_price <= bid else getattr(mt5, "ORDER_TYPE_SELL_LIMIT"))


def _direction_from_mt5_type(order_type: int) -> DirectionBias | None:
    if order_type in {0, 2, 4}:
        return DirectionBias.LONG
    if order_type in {1, 3, 5}:
        return DirectionBias.SHORT
    return None


def _jsonable_payload(payload: dict[str, object]) -> dict[str, str | float | int | bool | None]:
    output: dict[str, str | float | int | bool | None] = {}
    for key, value in payload.items():
        if isinstance(value, (str, float, int, bool)) or value is None:
            output[key] = value
        else:
            output[key] = str(value)
    return output
