"""Broker adapter helpers, mock broker, and execution adapter selection."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.config.settings import AppSettings
from app.execution.base import ExecutionAdapter
from app.execution.models import (
    BrokerAccountState,
    BrokerErrorCategory,
    BrokerOrderState,
    BrokerPositionSnapshot,
    BrokerTransition,
    CloseReason,
    ExecutionOrder,
    OrderRequest,
    OrderStatus,
    TradeEvent,
    TradeEventType,
)
from app.execution.paper import PaperExecutor


class BrokerExecutionError(RuntimeError):
    """Raised when a broker adapter cannot safely execute a request."""

    def __init__(self, message: str, category: BrokerErrorCategory = BrokerErrorCategory.UNKNOWN) -> None:
        super().__init__(message)
        self.category = category


class MockBrokerExecutor:
    """Deterministic in-memory broker adapter for tests and safe sandbox dry-runs."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        mode: str = "broker_sandbox",
        connected: bool = True,
        can_trade: bool = True,
        balance: float = 100_000.0,
        equity: float = 100_000.0,
        free_margin: float = 100_000.0,
    ) -> None:
        self.settings = settings
        self.mode = mode
        self.connected = connected
        self.can_trade = can_trade
        self.balance = balance
        self.equity = equity
        self.free_margin = free_margin
        self._orders: dict[str, ExecutionOrder] = {}

    def create_order_intent(self, request: OrderRequest) -> OrderRequest:
        """Return a validated order intent."""

        return request

    def place_order(self, request: OrderRequest) -> ExecutionOrder:
        """Submit a mock broker order and acknowledge it immediately."""

        account = self.query_account_state()
        if not account.connected or not account.can_trade:
            raise BrokerExecutionError("mock broker account is not tradable")
        order_id = str(uuid.uuid4())
        broker_id = f"MOCK-{order_id[:8]}"
        now = datetime.now(timezone.utc)
        order = ExecutionOrder(
            order_id=order_id,
            request=self.create_order_intent(request),
            status=OrderStatus.PENDING,
            created_at=now,
            signal_timestamp=request.signal_timestamp,
            initial_stop_loss=request.stop_loss,
            broker_mode=self.mode,
            broker_name="mock",
            broker_order_id=broker_id,
            broker_state=BrokerOrderState.INTENT_CREATED,
            broker_submission=_submission_summary(request, "mock", self.mode),
            execution_assumptions={"broker": "mock", "mode": self.mode, "live_money": False},
        )
        order = append_broker_transition(order, BrokerOrderState.INTENT_CREATED, TradeEventType.BROKER_INTENT_CREATED, now)
        order = append_broker_transition(order, BrokerOrderState.PRETRADE_VALIDATED, TradeEventType.BROKER_PRETRADE_VALIDATED, now)
        order = append_broker_transition(order, BrokerOrderState.SUBMIT_REQUESTED, TradeEventType.BROKER_SUBMIT_REQUESTED, now, payload={"broker_order_id": broker_id})
        order = append_broker_transition(order, BrokerOrderState.SUBMITTED, TradeEventType.BROKER_SUBMITTED, now, payload={"broker_order_id": broker_id})
        order = append_broker_transition(order, BrokerOrderState.ACKNOWLEDGED, TradeEventType.BROKER_ACKNOWLEDGED, now, payload={"broker_order_id": broker_id})
        order = order.model_copy(update={"broker_acknowledgement": {"broker_order_id": broker_id, "accepted": True}})
        self._orders[order.order_id] = order
        return order

    def modify_order(self, order_id: str, *, stop_loss: float | None = None, take_profit: float | None = None) -> ExecutionOrder:
        """Modify a mock broker order."""

        order = self._existing_order(order_id)
        request = order.request.model_copy(update={key: value for key, value in {"stop_loss": stop_loss, "take_profit": take_profit}.items() if value is not None})
        updated = order.model_copy(update={"request": request, "status": OrderStatus.PENDING})
        updated = append_broker_transition(updated, BrokerOrderState.MODIFY_REQUESTED, TradeEventType.BROKER_MODIFY_REQUESTED, datetime.now(timezone.utc))
        updated = append_broker_transition(updated, BrokerOrderState.MODIFIED, TradeEventType.BROKER_MODIFIED, datetime.now(timezone.utc))
        self._orders[order_id] = updated
        return updated

    def partial_close_order(self, order_id: str, exit_price: float, fraction: float, reason: str = "manual_partial") -> ExecutionOrder:
        """Mark a mock broker order as partially filled/closed."""

        order = self._existing_order(order_id)
        if not 0.0 < fraction <= 1.0:
            raise ValueError("partial close fraction must be between 0 and 1")
        remaining = round(max(0.0, order.remaining_fraction - fraction), 4)
        updated = order.model_copy(
            update={
                "status": OrderStatus.PARTIALLY_CLOSED if remaining > 0.0 else OrderStatus.FULLY_CLOSED,
                "remaining_fraction": remaining,
                "filled_quantity": round(order.filled_quantity + fraction * order.request.quantity_units, 4),
                "average_fill_price": exit_price,
            }
        )
        updated = append_broker_transition(
            updated,
            BrokerOrderState.PARTIALLY_FILLED if remaining > 0.0 else BrokerOrderState.CLOSED,
            TradeEventType.BROKER_PARTIALLY_FILLED if remaining > 0.0 else TradeEventType.BROKER_CLOSED,
            datetime.now(timezone.utc),
            reason=reason,
            payload={"fraction": fraction, "exit_price": exit_price},
        )
        self._orders[order_id] = updated
        return updated

    def close_order(self, order_id: str, exit_price: float, reason: str = "manual") -> ExecutionOrder:
        """Close a mock broker order."""

        order = self._existing_order(order_id)
        now = datetime.now(timezone.utc)
        updated = append_broker_transition(order, BrokerOrderState.CLOSE_REQUESTED, TradeEventType.BROKER_CLOSE_REQUESTED, now, reason=reason)
        updated = updated.model_copy(
            update={
                "status": OrderStatus.FULLY_CLOSED,
                "closed_at": now,
                "exit_price": exit_price,
                "close_reason": CloseReason.MANUAL,
                "remaining_fraction": 0.0,
                "filled_quantity": order.request.quantity_units,
                "average_fill_price": exit_price,
            }
        )
        updated = append_broker_transition(updated, BrokerOrderState.CLOSED, TradeEventType.BROKER_CLOSED, now, reason=reason, payload={"exit_price": exit_price})
        self._orders[order_id] = updated
        return updated

    def cancel_order(self, order_id: str) -> ExecutionOrder:
        """Cancel a mock broker order."""

        order = self._existing_order(order_id)
        now = datetime.now(timezone.utc)
        updated = append_broker_transition(order, BrokerOrderState.CANCEL_REQUESTED, TradeEventType.BROKER_CANCEL_REQUESTED, now)
        updated = updated.model_copy(update={"status": OrderStatus.CANCELLED_TRADE, "closed_at": now, "close_reason": CloseReason.CANCELED})
        updated = append_broker_transition(updated, BrokerOrderState.CANCELLED, TradeEventType.BROKER_CANCELLED, now)
        self._orders[order_id] = updated
        return updated

    def sync_positions(self) -> list[ExecutionOrder]:
        """Return mock broker open/pending orders."""

        return [order for order in self._orders.values() if order.is_open]

    def query_order_status(self, order_id: str) -> ExecutionOrder:
        """Return tracked mock broker order state."""

        return self._existing_order(order_id)

    def query_account_state(self) -> BrokerAccountState:
        """Return deterministic mock account state."""

        open_orders = self.sync_positions()
        return BrokerAccountState(
            broker="mock",
            mode=self.mode,
            connected=self.connected,
            can_trade=self.can_trade,
            balance=self.balance,
            equity=self.equity,
            free_margin=self.free_margin,
            currency="USD",
            open_positions=sum(1 for order in open_orders if order.status in {OrderStatus.ACTIVE, OrderStatus.OPEN_TRADE, OrderStatus.PARTIALLY_CLOSED}),
            pending_orders=sum(1 for order in open_orders if order.status in {OrderStatus.PENDING, OrderStatus.PENDING_OPPORTUNITY}),
            is_demo=True,
            retrieved_at=datetime.now(timezone.utc),
            health_status="healthy" if self.connected and self.can_trade else "unhealthy",
            consecutive_failures=0 if self.connected and self.can_trade else 1,
        )

    def broker_order_snapshots(self) -> list["BrokerOrderSnapshot"]:
        """Return snapshots used by reconciliation tests."""

        from app.execution.models import BrokerOrderSnapshot

        return [
            BrokerOrderSnapshot(
                broker_order_id=order.broker_order_id or order.order_id,
                symbol=order.request.symbol,
                direction=order.request.direction,
                state=order.broker_state or BrokerOrderState.ACKNOWLEDGED,
                quantity=order.request.quantity_units,
                filled_quantity=order.filled_quantity,
                entry_price=order.request.entry_price,
                stop_loss=order.request.stop_loss,
                take_profit=order.request.take_profit,
                updated_at=order.created_at,
            )
            for order in self._orders.values()
        ]

    def broker_position_snapshots(self) -> list[BrokerPositionSnapshot]:
        """Return mock position snapshots for filled/open orders."""

        return [
            BrokerPositionSnapshot(
                broker_position_id=order.broker_position_id or order.broker_order_id or order.order_id,
                symbol=order.request.symbol,
                direction=order.request.direction,
                quantity=max(order.filled_quantity, order.request.quantity_units if order.status in {OrderStatus.OPEN_TRADE, OrderStatus.ACTIVE} else 0.0),
                entry_price=order.average_fill_price or order.request.entry_price,
                stop_loss=order.request.stop_loss,
                take_profit=order.request.take_profit,
                updated_at=order.created_at,
            )
            for order in self._orders.values()
            if order.status in {OrderStatus.OPEN_TRADE, OrderStatus.ACTIVE, OrderStatus.PARTIALLY_CLOSED}
        ]

    def reconcile(self) -> list[ExecutionOrder]:
        """Return all mock broker orders."""

        return list(self._orders.values())

    def _existing_order(self, order_id: str) -> ExecutionOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise KeyError(f"unknown broker order {order_id}") from exc


def build_execution_adapter(settings: AppSettings) -> ExecutionAdapter:
    """Build the configured execution adapter without enabling live by accident."""

    if settings.execution.mode in {"disabled", "paper"}:
        if settings.execution.mode == "paper" and not settings.execution_capabilities.paper_enabled:
            raise BrokerExecutionError("paper execution capability is disabled", BrokerErrorCategory.CONFIGURATION)
        return PaperExecutor(settings)
    if settings.execution.mode == "broker_sandbox" and not settings.execution_capabilities.broker_sandbox_enabled:
        raise BrokerExecutionError("broker_sandbox capability is disabled", BrokerErrorCategory.CONFIGURATION)
    if settings.execution.mode == "broker_live" and not settings.execution_capabilities.broker_live_enabled:
        raise BrokerExecutionError("broker_live capability is disabled", BrokerErrorCategory.CONFIGURATION)
    if settings.execution.mode == "broker_live" and not settings.broker.live_enabled:
        raise BrokerExecutionError("broker_live mode is disabled in config", BrokerErrorCategory.CONFIGURATION)
    if settings.execution.mode == "broker_live" and settings.broker.provider == "mock":
        raise BrokerExecutionError("mock provider is not allowed for broker_live", BrokerErrorCategory.CONFIGURATION)
    if settings.broker.provider == "mock":
        return MockBrokerExecutor(settings, mode=settings.execution.mode)
    from app.execution.mt5 import MT5BrokerExecutor

    return MT5BrokerExecutor(settings, mode=settings.execution.mode)


def append_broker_transition(
    order: ExecutionOrder,
    state: BrokerOrderState,
    event_type: TradeEventType,
    timestamp: datetime,
    *,
    reason: str | None = None,
    payload: dict[str, str | float | int | bool | None] | None = None,
) -> ExecutionOrder:
    """Append a broker state transition and matching journal event."""

    transition = BrokerTransition(
        transition_id=str(uuid.uuid4()),
        order_id=order.order_id,
        state=state,
        occurred_at=timestamp,
        reason=reason,
        payload=payload or {},
    )
    event = TradeEvent(
        event_id=str(uuid.uuid4()),
        trade_id=order.order_id,
        event_type=event_type,
        occurred_at=timestamp,
        symbol=order.request.symbol,
        status=state.value,
        reason=reason,
        payload=payload or {},
    )
    return order.model_copy(
        update={
            "broker_state": state,
            "broker_transitions": [*order.broker_transitions, transition],
            "events": [*order.events, event],
        }
    )


def _submission_summary(request: OrderRequest, broker: str, mode: str) -> dict[str, str | float | int | bool | None]:
    return {
        "broker": broker,
        "mode": mode,
        "symbol": request.symbol,
        "direction": request.direction.value,
        "quantity": request.quantity_units,
        "entry": request.entry_price,
        "stop_loss": request.stop_loss,
        "take_profit": request.take_profit,
        "tp1": request.tp1,
        "tp2": request.tp2,
        "tp3": request.tp3,
        "source_status": request.source_status,
    }
