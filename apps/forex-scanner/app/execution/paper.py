"""Paper execution adapter for approved scanner opportunities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from app.config.settings import AppSettings
from app.core.types import DirectionBias
from app.data.validation import pips_to_price
from app.execution.models import BrokerAccountState, CloseReason, ExecutionOrder, OrderRequest, OrderStatus, PartialExit, StopMovement, TradeEvent, TradeEventType


@dataclass
class PaperFillResult:
    """Summary of order-state changes after processing market data."""

    activated: int = 0
    partials: int = 0
    closed: int = 0
    canceled: int = 0
    missed: int = 0
    expired: int = 0


class PaperExecutor:
    """In-memory paper executor with spread/slippage, partial exits, and lifecycle tracking."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._orders: dict[str, ExecutionOrder] = {}

    def place_order(self, request: OrderRequest) -> ExecutionOrder:
        """Create a pending paper order for later activation."""

        request = self.create_order_intent(request)
        slippage = pips_to_price(request.symbol, self.settings.execution.estimated_slippage_pips)
        order = ExecutionOrder(
            order_id=str(uuid.uuid4()),
            request=request,
            status=OrderStatus.PENDING_OPPORTUNITY,
            created_at=datetime.now(timezone.utc),
            signal_timestamp=request.signal_timestamp,
            initial_stop_loss=request.stop_loss,
            estimated_slippage=slippage,
            execution_assumptions=_execution_assumptions(self.settings),
        )
        order = _append_event(
            order,
            TradeEventType.SIGNAL_PREMIUM if request.source_status == "premium" else TradeEventType.SIGNAL_APPROVED,
            request.signal_timestamp or order.created_at,
            payload={
                "final_score": request.final_score,
                "provider": request.provider,
                "session": request.session,
                "data_quality_score": request.data_quality_score,
            },
        )
        self._orders[order.order_id] = order
        return order

    def create_order_intent(self, request: OrderRequest) -> OrderRequest:
        """Return the validated broker-neutral intent used by paper execution."""

        return request

    def modify_order(
        self,
        order_id: str,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> ExecutionOrder:
        """Modify stop or target on an open paper order."""

        order = self._existing_order(order_id)
        if order.status not in {OrderStatus.PENDING_OPPORTUNITY, OrderStatus.PENDING, OrderStatus.OPEN_TRADE, OrderStatus.ACTIVE, OrderStatus.PARTIALLY_CLOSED}:
            raise ValueError("only pending or open paper orders can be modified")
        request = order.request.model_copy(
            update={
                key: value
                for key, value in {"stop_loss": stop_loss, "take_profit": take_profit}.items()
                if value is not None
            }
        )
        updated = order.model_copy(update={"request": request})
        if stop_loss is not None and stop_loss != order.request.stop_loss:
            movement = StopMovement(
                timestamp=datetime.now(timezone.utc),
                from_stop=order.request.stop_loss,
                to_stop=stop_loss,
                reason="manual stop modification",
            )
            updated = updated.model_copy(update={"stop_movements": [*updated.stop_movements, movement]})
            updated = _append_event(
                updated,
                TradeEventType.STOP_MOVED,
                movement.timestamp,
                reason=movement.reason,
                payload={"from_stop": movement.from_stop, "to_stop": movement.to_stop},
            )
        self._orders[order_id] = updated
        return updated

    def close_order(self, order_id: str, exit_price: float, reason: str = "manual") -> ExecutionOrder:
        """Manually close a pending/open paper order."""

        order = self._existing_order(order_id)
        if order.status in {OrderStatus.CLOSED, OrderStatus.FULLY_CLOSED}:
            return order
        close_reason = CloseReason.MANUAL if reason == "manual" else CloseReason(reason)
        updated = _fully_closed_order(order, _adverse_exit_price(order, exit_price), datetime.now(timezone.utc), close_reason, order.bars_in_trade or 0)
        self._orders[order_id] = updated
        return updated

    def partial_close_order(self, order_id: str, exit_price: float, fraction: float, reason: str = "manual_partial") -> ExecutionOrder:
        """Manually close a fraction of an open paper order."""

        if not 0.0 < fraction <= 1.0:
            raise ValueError("partial close fraction must be between 0 and 1")
        order = self._existing_order(order_id)
        if order.status not in {OrderStatus.OPEN_TRADE, OrderStatus.ACTIVE, OrderStatus.PARTIALLY_CLOSED}:
            raise ValueError("only open paper orders can be partially closed")
        timestamp = datetime.now(timezone.utc)
        if fraction >= order.remaining_fraction:
            updated = _fully_closed_order(order, _adverse_exit_price(order, exit_price), timestamp, CloseReason.MANUAL, order.bars_in_trade or 0)
        else:
            updated = _apply_partial_exit(order, reason, exit_price, fraction, timestamp, 0.0)
        self._orders[order_id] = updated
        return updated

    def cancel_order(self, order_id: str) -> ExecutionOrder:
        """Cancel a pending paper opportunity."""

        order = self._existing_order(order_id)
        if order.status not in {OrderStatus.PENDING_OPPORTUNITY, OrderStatus.PENDING}:
            raise ValueError("only pending paper opportunities can be canceled")
        timestamp = datetime.now(timezone.utc)
        updated = order.model_copy(
            update={
                "status": OrderStatus.CANCELLED_TRADE,
                "closed_at": timestamp,
                "close_reason": CloseReason.CANCELED,
                "cancellation_reason": "manual cancellation before activation",
            }
        )
        updated = _append_event(updated, TradeEventType.TRADE_CANCELLED, timestamp, reason="manual cancellation before activation")
        self._orders[order_id] = updated
        return updated

    def sync_positions(self) -> list[ExecutionOrder]:
        """Return pending and open paper orders."""

        return [order for order in self._orders.values() if order.is_open]

    def reconcile(self) -> list[ExecutionOrder]:
        """Return all tracked orders; paper mode has no external broker state."""

        return self.all_orders()

    def query_order_status(self, order_id: str) -> ExecutionOrder:
        """Return the current paper order state."""

        return self._existing_order(order_id)

    def query_account_state(self) -> BrokerAccountState:
        """Return a simulated account state for the paper adapter."""

        open_orders = [order for order in self._orders.values() if order.is_open]
        return BrokerAccountState(
            broker="paper",
            mode="paper",
            connected=True,
            can_trade=True,
            balance=100_000.0,
            equity=100_000.0 + sum(order.realized_pnl or 0.0 for order in self._orders.values()),
            free_margin=100_000.0,
            currency="USD",
            open_positions=sum(1 for order in open_orders if order.status in {OrderStatus.OPEN_TRADE, OrderStatus.PARTIALLY_CLOSED, OrderStatus.ACTIVE}),
            pending_orders=sum(1 for order in open_orders if order.status in {OrderStatus.PENDING_OPPORTUNITY, OrderStatus.PENDING}),
            is_demo=True,
        )

    def set_portfolio_snapshot(self, order_id: str, snapshot: dict[str, str | float | int]) -> ExecutionOrder:
        """Attach portfolio context captured at paper-order creation."""

        order = self._existing_order(order_id)
        updated = order.model_copy(update={"portfolio_snapshot": snapshot})
        self._orders[order_id] = updated
        return updated

    def all_orders(self) -> list[ExecutionOrder]:
        """Return every order currently tracked by the in-memory executor."""

        return list(self._orders.values())

    def seed_orders(self, orders: list[ExecutionOrder]) -> None:
        """Load persisted paper orders so guardrails and manual actions see current state."""

        self._orders.update({order.order_id: order for order in orders})

    def process_market_data(self, symbol: str, bars: pd.DataFrame) -> PaperFillResult:
        """Advance pending/open orders for one symbol using completed bars."""

        result = PaperFillResult()
        if bars.empty:
            return result
        for order in list(self._orders.values()):
            if order.request.symbol != symbol or not order.is_open:
                continue
            updated = order
            for offset, (timestamp, row) in enumerate(bars.iterrows(), start=1):
                moment = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
                open_price = float(row.get("open", row["close"]))
                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])
                spread = float(row["spread"]) if "spread" in row and pd.notna(row["spread"]) else 0.0
                spread_adjustment = _spread_adjustment(self.settings, spread)
                if updated.status in {OrderStatus.PENDING_OPPORTUNITY, OrderStatus.PENDING}:
                    updated, event = _process_pending_order(updated, open_price, high, low, moment, offset, spread_adjustment, self.settings)
                    if event == "activated":
                        result.activated += 1
                    elif event == "missed":
                        result.missed += 1
                        break
                    elif event == "expired":
                        result.expired += 1
                        break
                    elif event == "canceled":
                        result.canceled += 1
                        break
                if updated.status in {OrderStatus.OPEN_TRADE, OrderStatus.ACTIVE, OrderStatus.PARTIALLY_CLOSED}:
                    before_partials = len(updated.partial_exits)
                    updated = _update_excursions(updated, high, low)
                    updated, closed_now = _process_open_order(updated, high, low, close, moment, offset, spread_adjustment, self.settings)
                    result.partials += max(0, len(updated.partial_exits) - before_partials)
                    if closed_now:
                        result.closed += 1
                        break
            self._orders[updated.order_id] = updated
        return result

    def _existing_order(self, order_id: str) -> ExecutionOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise KeyError(f"unknown paper order {order_id}") from exc


def _process_pending_order(
    order: ExecutionOrder,
    open_price: float,
    high: float,
    low: float,
    timestamp: datetime,
    bars_to_activation: int,
    spread_adjustment: float,
    settings: AppSettings,
) -> tuple[ExecutionOrder, str | None]:
    if settings.execution.cancel_on_invalidation_before_activation and _invalidation_touched(order.request, high, low):
        canceled = order.model_copy(
                update={
                    "status": OrderStatus.CANCELLED_TRADE,
                    "closed_at": timestamp,
                    "close_reason": CloseReason.SETUP_INVALIDATED,
                    "bars_to_activation": bars_to_activation,
                    "invalidation_reason": "setup invalidated before entry activation",
                }
            )
        canceled = _append_event(
            canceled,
            TradeEventType.TRADE_CANCELLED,
            timestamp,
            reason="setup invalidated before entry activation",
            payload={"bars_to_activation": bars_to_activation},
        )
        return (
            canceled,
            "canceled",
        )
    if _gap_missed_entry(order.request, open_price, high, low):
        if settings.execution.gap_through_entry_policy == "fill_at_open":
            return _activate_order(order, timestamp, bars_to_activation, spread_adjustment, fill_price=open_price), "activated"
        missed = order.model_copy(
                update={
                    "status": OrderStatus.MISSED_TRADE,
                    "closed_at": timestamp,
                    "close_reason": CloseReason.MISSED_TRIGGER,
                    "bars_to_activation": bars_to_activation,
                    "expiration_reason": "price gapped through entry without a tradable touch",
                }
            )
        missed = _append_event(
            missed,
            TradeEventType.TRADE_MISSED,
            timestamp,
            reason="price gapped through entry without a tradable touch",
            payload={"bars_to_activation": bars_to_activation},
        )
        return (
            missed,
            "missed",
        )
    if _entry_touched(order.request, high, low):
        return _activate_order(order, timestamp, bars_to_activation, spread_adjustment), "activated"
    if bars_to_activation >= settings.execution.activation_timeout_bars:
        expired = order.model_copy(
                update={
                    "status": OrderStatus.EXPIRED_TRADE,
                    "closed_at": timestamp,
                    "close_reason": CloseReason.EXPIRED,
                    "bars_to_activation": bars_to_activation,
                    "expiration_reason": f"entry not activated within {settings.execution.activation_timeout_bars} bars",
                }
            )
        expired = _append_event(
            expired,
            TradeEventType.TRADE_EXPIRED,
            timestamp,
            reason=f"entry not activated within {settings.execution.activation_timeout_bars} bars",
            payload={"bars_to_activation": bars_to_activation},
        )
        return (
            expired,
            "expired",
        )
    return order, None


def _activate_order(
    order: ExecutionOrder,
    timestamp: datetime,
    bars_to_activation: int,
    spread_adjustment: float,
    fill_price: float | None = None,
) -> ExecutionOrder:
    request = order.request
    base_entry = request.entry_price if fill_price is None else fill_price
    if request.direction == DirectionBias.LONG:
        entry = base_entry + order.estimated_slippage + spread_adjustment
    else:
        entry = base_entry - order.estimated_slippage - spread_adjustment
    activated = order.model_copy(
        update={
            "status": OrderStatus.OPEN_TRADE,
            "activated_at": timestamp,
            "entry_timestamp": timestamp,
            "simulated_entry": entry,
            "spread_adjustment": spread_adjustment,
            "bars_to_activation": bars_to_activation,
            "bars_in_trade": 0,
        }
    )
    activated = _append_event(
        activated,
        TradeEventType.TRADE_ACTIVATED,
        timestamp,
        payload={"bars_to_activation": bars_to_activation, "entry": entry},
    )
    return _append_event(
        activated,
        TradeEventType.TRADE_ENTERED,
        timestamp,
        payload={"entry": entry, "spread_adjustment": spread_adjustment, "slippage": order.estimated_slippage},
    )


def _process_open_order(
    order: ExecutionOrder,
    high: float,
    low: float,
    close: float,
    timestamp: datetime,
    bar_offset: int,
    spread_adjustment: float,
    settings: AppSettings,
) -> tuple[ExecutionOrder, bool]:
    updated = order
    stop_hit = _stop_touched(updated.request, high, low)
    if stop_hit:
        return _fully_closed_order(updated, _adverse_exit_price(updated, updated.request.stop_loss, spread_adjustment), timestamp, CloseReason.STOP_LOSS, _bars_in_trade(updated, bar_offset)), True

    for target_name, target_price, fraction in _target_plan(updated.request, settings):
        if _target_already_hit(updated, target_name) or not _target_touched(updated.request.direction, target_price, high, low):
            continue
        updated = _apply_partial_exit(updated, target_name, target_price, fraction, timestamp, spread_adjustment)
        if settings.execution.move_stop_to_breakeven_after_tp1 and target_name == "tp1":
            updated = _move_stop_to_breakeven(updated, timestamp)
    if updated.remaining_fraction <= 1e-9:
        return _fully_closed_order(updated, _adverse_exit_price(updated, updated.partial_exits[-1].price if updated.partial_exits else close, spread_adjustment), timestamp, CloseReason.TAKE_PROFIT, _bars_in_trade(updated, bar_offset)), True
    status = OrderStatus.PARTIALLY_CLOSED if updated.partial_exits else OrderStatus.OPEN_TRADE
    return updated.model_copy(update={"status": status, "bars_in_trade": _bars_in_trade(updated, bar_offset), "time_in_trade_minutes": _time_in_trade_minutes(updated, timestamp)}), False


def _apply_partial_exit(
    order: ExecutionOrder,
    target_name: str,
    target_price: float,
    fraction: float,
    timestamp: datetime,
    spread_adjustment: float,
) -> ExecutionOrder:
    entry = order.simulated_entry or order.request.entry_price
    close_fraction = min(order.remaining_fraction, fraction)
    exit_price = _adverse_exit_price(order, target_price, spread_adjustment)
    realized_r = _r_multiple(order.request.direction, entry, order.initial_stop_loss or order.request.stop_loss, exit_price)
    partial = PartialExit(
        target=target_name,
        timestamp=timestamp,
        price=exit_price,
        fraction=round(close_fraction, 4),
        realized_r=round(realized_r, 4),
        realized_pnl=round(realized_r * close_fraction * order.request.quantity_units, 4),
    )
    updates: dict[str, object] = {
        "partial_exits": [*order.partial_exits, partial],
        "remaining_fraction": round(max(0.0, order.remaining_fraction - close_fraction), 4),
        "realized_r": round(sum(item.realized_r * item.fraction for item in [*order.partial_exits, partial]), 4),
        "realized_pnl": round(sum(item.realized_pnl for item in [*order.partial_exits, partial]), 4),
        "status": OrderStatus.PARTIALLY_CLOSED,
    }
    if target_name == "tp1":
        updates["tp1_exit_price"] = exit_price
    elif target_name == "tp2":
        updates["tp2_exit_price"] = exit_price
    elif target_name == "tp3":
        updates["tp3_exit_price"] = exit_price
    updated = order.model_copy(update=updates)
    return _append_event(
        updated,
        TradeEventType.TRADE_PARTIALLY_CLOSED,
        timestamp,
        payload={
            "target": target_name,
            "price": exit_price,
            "fraction": round(close_fraction, 4),
            "realized_r": round(realized_r, 4),
        },
    )


def _move_stop_to_breakeven(order: ExecutionOrder, timestamp: datetime) -> ExecutionOrder:
    old_stop = order.request.stop_loss
    request = order.request.model_copy(update={"stop_loss": order.request.entry_price})
    movement = StopMovement(
        timestamp=timestamp,
        from_stop=old_stop,
        to_stop=request.stop_loss,
        reason="move stop to breakeven after TP1",
    )
    updated = order.model_copy(update={"request": request, "stop_movements": [*order.stop_movements, movement]})
    return _append_event(
        updated,
        TradeEventType.STOP_MOVED,
        timestamp,
        reason=movement.reason,
        payload={"from_stop": movement.from_stop, "to_stop": movement.to_stop},
    )


def _fully_closed_order(
    order: ExecutionOrder,
    exit_price: float,
    timestamp: datetime,
    reason: CloseReason,
    bars_in_trade: int,
) -> ExecutionOrder:
    entry = order.simulated_entry or order.request.entry_price
    remaining_fraction = order.remaining_fraction
    exit_r = _r_multiple(order.request.direction, entry, order.initial_stop_loss or order.request.stop_loss, exit_price)
    partial_r = sum(item.realized_r * item.fraction for item in order.partial_exits)
    partial_pnl = sum(item.realized_pnl for item in order.partial_exits)
    total_r = partial_r + exit_r * remaining_fraction
    total_pnl = partial_pnl + exit_r * remaining_fraction * order.request.quantity_units
    closed = order.model_copy(
        update={
            "status": OrderStatus.FULLY_CLOSED,
            "closed_at": timestamp,
            "exit_price": exit_price,
            "close_reason": reason,
            "bars_in_trade": bars_in_trade,
            "time_in_trade_minutes": _time_in_trade_minutes(order, timestamp),
            "remaining_fraction": 0.0,
            "realized_r": round(total_r, 4),
            "realized_pnl": round(total_pnl, 4),
        }
    )
    return _append_event(
        closed,
        TradeEventType.TRADE_CLOSED,
        timestamp,
        reason=reason.value,
        payload={"exit_price": exit_price, "realized_r": round(total_r, 4), "realized_pnl": round(total_pnl, 4)},
    )


def _target_plan(request: OrderRequest, settings: AppSettings) -> list[tuple[str, float, float]]:
    fractions = settings.execution.partial_exit_fractions
    targets = [
        ("tp1", request.tp1, fractions.tp1),
        ("tp2", request.tp2 or request.take_profit, fractions.tp2),
        ("tp3", request.tp3, fractions.tp3),
    ]
    return [(name, float(price), fraction) for name, price, fraction in targets if price is not None and fraction > 0.0]


def _target_already_hit(order: ExecutionOrder, target_name: str) -> bool:
    return any(item.target == target_name for item in order.partial_exits)


def _entry_touched(request: OrderRequest, high: float, low: float) -> bool:
    return low <= request.entry_price <= high


def _gap_missed_entry(request: OrderRequest, open_price: float, high: float, low: float) -> bool:
    if request.direction == DirectionBias.LONG:
        return open_price > request.entry_price and low > request.entry_price
    return open_price < request.entry_price and high < request.entry_price


def _invalidation_touched(request: OrderRequest, high: float, low: float) -> bool:
    return _stop_touched(request, high, low)


def _stop_touched(request: OrderRequest, high: float, low: float) -> bool:
    if request.direction == DirectionBias.LONG:
        return low <= request.stop_loss
    return high >= request.stop_loss


def _target_touched(direction: DirectionBias, target: float, high: float, low: float) -> bool:
    if direction == DirectionBias.LONG:
        return high >= target
    return low <= target


def _update_excursions(order: ExecutionOrder, high: float, low: float) -> ExecutionOrder:
    entry = order.simulated_entry or order.request.entry_price
    risk = abs(entry - (order.initial_stop_loss or order.request.stop_loss))
    if order.request.direction == DirectionBias.LONG:
        mfe = max(order.mfe, (high - entry) / max(risk, 1e-12))
        mae = max(order.mae, (entry - low) / max(risk, 1e-12))
    else:
        mfe = max(order.mfe, (entry - low) / max(risk, 1e-12))
        mae = max(order.mae, (high - entry) / max(risk, 1e-12))
    return order.model_copy(update={"mfe": round(max(0.0, mfe), 4), "mae": round(max(0.0, mae), 4)})


def _adverse_exit_price(order: ExecutionOrder, price: float, spread_adjustment: float | None = None) -> float:
    adjustment = order.estimated_slippage + (order.spread_adjustment if spread_adjustment is None else spread_adjustment)
    if order.request.direction == DirectionBias.LONG:
        return price - adjustment
    return price + adjustment


def _r_multiple(direction: DirectionBias, entry: float, stop_loss: float, exit_price: float) -> float:
    risk_distance = abs(entry - stop_loss)
    if direction == DirectionBias.LONG:
        gross = exit_price - entry
    else:
        gross = entry - exit_price
    return gross / max(risk_distance, 1e-12)


def _bars_in_trade(order: ExecutionOrder, bar_offset: int) -> int:
    return max(0, bar_offset - (order.bars_to_activation or 0))


def _time_in_trade_minutes(order: ExecutionOrder, timestamp: datetime) -> float | None:
    if order.entry_timestamp is None:
        return None
    return round(max(0.0, (timestamp - order.entry_timestamp).total_seconds() / 60.0), 2)


def _spread_adjustment(settings: AppSettings, spread: float) -> float:
    if not settings.execution.spread_aware_fills or spread <= 0.0:
        return 0.0
    return spread / 2.0


def _execution_assumptions(settings: AppSettings) -> dict[str, str | float | bool]:
    fractions = settings.execution.partial_exit_fractions
    return {
        "spread_aware_fills": settings.execution.spread_aware_fills,
        "slippage_pips": settings.execution.estimated_slippage_pips,
        "tp1_fraction": fractions.tp1,
        "tp2_fraction": fractions.tp2,
        "tp3_fraction": fractions.tp3,
        "move_stop_to_breakeven_after_tp1": settings.execution.move_stop_to_breakeven_after_tp1,
        "activation_timeout_bars": settings.execution.activation_timeout_bars,
        "gap_through_entry_policy": settings.execution.gap_through_entry_policy,
        "cancel_on_invalidation_before_activation": settings.execution.cancel_on_invalidation_before_activation,
    }


def _append_event(
    order: ExecutionOrder,
    event_type: TradeEventType,
    timestamp: datetime,
    *,
    reason: str | None = None,
    payload: dict[str, str | float | int | bool | None] | None = None,
) -> ExecutionOrder:
    event = TradeEvent(
        event_id=str(uuid.uuid4()),
        trade_id=order.order_id,
        event_type=event_type,
        occurred_at=timestamp,
        symbol=order.request.symbol,
        status=order.status.value,
        reason=reason,
        payload=payload or {},
    )
    return order.model_copy(update={"events": [*order.events, event]})
