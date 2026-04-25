"""Broker/internal state reconciliation for supervised execution review."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.execution.broker import append_broker_transition
from app.execution.models import BrokerOrderSnapshot, BrokerOrderState, BrokerPositionSnapshot, ExecutionOrder, OrderStatus, TradeEventType


class ReconciliationAnomalyType(str, Enum):
    """Supported broker reconciliation anomaly categories."""

    BROKER_ORDER_MISSING_INTERNALLY = "broker_order_missing_internally"
    INTERNAL_OPEN_MISSING_AT_BROKER = "internal_open_missing_at_broker"
    BROKER_POSITION_MISSING_INTERNALLY = "broker_position_missing_internally"
    PARTIAL_FILL_DIFFERENCE = "partial_fill_difference"
    STOP_TARGET_MISMATCH = "stop_target_mismatch"
    MANUAL_BROKER_SIDE_CHANGE = "manual_broker_side_change"
    DUPLICATE_ORDER_SUSPICION = "duplicate_order_suspicion"
    DELAYED_BROKER_UPDATE = "delayed_broker_update"
    STALE_LOCAL_STATE = "stale_local_state"
    STALE_BROKER_SNAPSHOT = "stale_broker_snapshot"
    BROKER_UNREACHABLE = "broker_unreachable"


class ReconciliationAnomaly(BaseModel):
    """One mismatch between internal and broker-reported execution state."""

    anomaly_id: str
    detected_at: datetime
    anomaly_type: ReconciliationAnomalyType
    severity: str
    symbol: str | None = None
    internal_order_id: str | None = None
    broker_order_id: str | None = None
    broker_position_id: str | None = None
    reason: str
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class ReconciliationReport(BaseModel):
    """Summary of one reconciliation pass."""

    run_id: str
    created_at: datetime
    checked_internal_orders: int
    checked_broker_orders: int
    checked_broker_positions: int
    anomalies: list[ReconciliationAnomaly] = Field(default_factory=list)

    @property
    def has_blocking_anomalies(self) -> bool:
        """Return true if any anomaly should block broker execution."""

        return any(anomaly.severity in {"high", "critical"} for anomaly in self.anomalies)


def reconcile_broker_state(
    internal_orders: list[ExecutionOrder],
    broker_orders: list[BrokerOrderSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
    *,
    stale_after_minutes: float = 60.0,
    delayed_update_grace_minutes: float = 5.0,
    broker_snapshot_stale_minutes: float = 15.0,
) -> tuple[ReconciliationReport, list[ExecutionOrder]]:
    """Compare internal order state with broker orders and positions."""

    now = datetime.now(timezone.utc)
    anomalies: list[ReconciliationAnomaly] = []
    broker_by_id = {order.broker_order_id: order for order in broker_orders}
    position_by_id = {position.broker_position_id: position for position in broker_positions}
    internal_by_broker = {order.broker_order_id: order for order in internal_orders if order.broker_order_id}
    updated_orders: list[ExecutionOrder] = []
    anomalies.extend(_duplicate_order_anomalies(broker_orders, broker_positions))

    for broker_order in broker_orders:
        if _snapshot_stale(broker_order.updated_at, now, broker_snapshot_stale_minutes):
            anomalies.append(
                _anomaly(
                    ReconciliationAnomalyType.STALE_BROKER_SNAPSHOT,
                    "medium",
                    broker_order.symbol,
                    f"broker order snapshot is older than {broker_snapshot_stale_minutes:.1f} minutes",
                    broker_order_id=broker_order.broker_order_id,
                )
            )
        if broker_order.broker_order_id not in internal_by_broker:
            anomalies.append(
                _anomaly(
                    ReconciliationAnomalyType.BROKER_ORDER_MISSING_INTERNALLY,
                    "critical",
                    broker_order.symbol,
                    "broker order exists but no internal tracked order was found",
                    broker_order_id=broker_order.broker_order_id,
                )
            )

    internal_position_ids = {order.broker_position_id for order in internal_orders if order.broker_position_id}
    for position in broker_positions:
        if _snapshot_stale(position.updated_at, now, broker_snapshot_stale_minutes):
            anomalies.append(
                _anomaly(
                    ReconciliationAnomalyType.STALE_BROKER_SNAPSHOT,
                    "medium",
                    position.symbol,
                    f"broker position snapshot is older than {broker_snapshot_stale_minutes:.1f} minutes",
                    broker_position_id=position.broker_position_id,
                )
            )
        if position.broker_position_id not in internal_position_ids:
            anomalies.append(
                _anomaly(
                    ReconciliationAnomalyType.BROKER_POSITION_MISSING_INTERNALLY,
                    "critical",
                    position.symbol,
                    "broker position exists but no internal tracked position was found",
                    broker_position_id=position.broker_position_id,
                )
            )

    for order in internal_orders:
        order_anomalies: list[ReconciliationAnomaly] = []
        broker_order = broker_by_id.get(order.broker_order_id or "")
        broker_position = position_by_id.get(order.broker_position_id or "")
        local_age_minutes = max(0.0, (now - (order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=timezone.utc))).total_seconds() / 60.0)
        if order.is_open and broker_order is None and broker_position is None and local_age_minutes <= delayed_update_grace_minutes:
            order_anomalies.append(
                _anomaly(
                    ReconciliationAnomalyType.DELAYED_BROKER_UPDATE,
                    "medium",
                    order.request.symbol,
                    f"broker has not reflected local open order within {delayed_update_grace_minutes:.1f} minute grace window",
                    internal_order_id=order.order_id,
                    broker_order_id=order.broker_order_id,
                    broker_position_id=order.broker_position_id,
                )
            )
        elif order.is_open and broker_order is None and broker_position is None:
            order_anomalies.append(
                _anomaly(
                    ReconciliationAnomalyType.INTERNAL_OPEN_MISSING_AT_BROKER,
                    "critical",
                    order.request.symbol,
                    "internal order is open but broker has no matching order or position",
                    internal_order_id=order.order_id,
                    broker_order_id=order.broker_order_id,
                    broker_position_id=order.broker_position_id,
                )
            )
        if broker_order is not None:
            if broker_order.filled_quantity is not None and abs(broker_order.filled_quantity - order.filled_quantity) > 1e-6:
                order_anomalies.append(
                    _anomaly(
                        ReconciliationAnomalyType.PARTIAL_FILL_DIFFERENCE,
                        "high",
                        order.request.symbol,
                        "internal filled quantity differs from broker filled quantity",
                        internal_order_id=order.order_id,
                        broker_order_id=broker_order.broker_order_id,
                        payload={"internal_filled": order.filled_quantity, "broker_filled": broker_order.filled_quantity},
                    )
                )
            order_anomalies.extend(_level_anomalies(order, broker_order))
        if broker_position is not None:
            order_anomalies.extend(_position_level_anomalies(order, broker_position))
        if order.created_at < now - timedelta(minutes=stale_after_minutes) and order.is_open:
            order_anomalies.append(
                _anomaly(
                    ReconciliationAnomalyType.STALE_LOCAL_STATE,
                    "medium",
                    order.request.symbol,
                    f"local open order has not reconciled within {stale_after_minutes:.1f} minutes",
                    internal_order_id=order.order_id,
                    broker_order_id=order.broker_order_id,
                )
            )
        if order_anomalies:
            anomalies.extend(order_anomalies)
            updated = order.model_copy(
                update={
                    "reconciliation_status": "mismatch",
                    "reconciliation_reason": "; ".join(item.reason for item in order_anomalies),
                    "status": OrderStatus.ACTIVE if order.is_open else order.status,
                }
            )
            updated = append_broker_transition(
                updated,
                BrokerOrderState.RECONCILIATION_MISMATCH,
                TradeEventType.RECONCILIATION_MISMATCH,
                now,
                reason=updated.reconciliation_reason,
            )
            updated_orders.append(updated)
        else:
            updated_orders.append(order.model_copy(update={"reconciliation_status": "ok", "reconciliation_reason": None}))

    report = ReconciliationReport(
        run_id=str(uuid.uuid4()),
        created_at=now,
        checked_internal_orders=len(internal_orders),
        checked_broker_orders=len(broker_orders),
        checked_broker_positions=len(broker_positions),
        anomalies=anomalies,
    )
    return report, updated_orders


def _level_anomalies(order: ExecutionOrder, broker_order: BrokerOrderSnapshot) -> list[ReconciliationAnomaly]:
    anomalies: list[ReconciliationAnomaly] = []
    if broker_order.stop_loss is not None and abs(broker_order.stop_loss - order.request.stop_loss) > 1e-8:
        anomalies.append(
            _anomaly(
                ReconciliationAnomalyType.STOP_TARGET_MISMATCH,
                "high",
                order.request.symbol,
                "broker stop loss differs from internal stop loss",
                internal_order_id=order.order_id,
                broker_order_id=broker_order.broker_order_id,
                payload={"internal_stop": order.request.stop_loss, "broker_stop": broker_order.stop_loss},
            )
        )
    if broker_order.take_profit is not None and abs(broker_order.take_profit - order.request.take_profit) > 1e-8:
        anomalies.append(
            _anomaly(
                ReconciliationAnomalyType.STOP_TARGET_MISMATCH,
                "high",
                order.request.symbol,
                "broker take profit differs from internal take profit",
                internal_order_id=order.order_id,
                broker_order_id=broker_order.broker_order_id,
                payload={"internal_target": order.request.take_profit, "broker_target": broker_order.take_profit},
            )
        )
    return anomalies


def _position_level_anomalies(order: ExecutionOrder, position: BrokerPositionSnapshot) -> list[ReconciliationAnomaly]:
    anomalies: list[ReconciliationAnomaly] = []
    if position.stop_loss is not None and abs(position.stop_loss - order.request.stop_loss) > 1e-8:
        anomalies.append(
            _anomaly(
                ReconciliationAnomalyType.MANUAL_BROKER_SIDE_CHANGE,
                "high",
                order.request.symbol,
                "broker position stop loss differs from internal stop loss",
                internal_order_id=order.order_id,
                broker_position_id=position.broker_position_id,
                payload={"internal_stop": order.request.stop_loss, "broker_stop": position.stop_loss},
            )
        )
    if position.take_profit is not None and abs(position.take_profit - order.request.take_profit) > 1e-8:
        anomalies.append(
            _anomaly(
                ReconciliationAnomalyType.MANUAL_BROKER_SIDE_CHANGE,
                "high",
                order.request.symbol,
                "broker position take profit differs from internal take profit",
                internal_order_id=order.order_id,
                broker_position_id=position.broker_position_id,
                payload={"internal_target": order.request.take_profit, "broker_target": position.take_profit},
            )
        )
    return anomalies


def _duplicate_order_anomalies(
    broker_orders: list[BrokerOrderSnapshot],
    broker_positions: list[BrokerPositionSnapshot],
) -> list[ReconciliationAnomaly]:
    counts: dict[tuple[str, str], int] = {}
    for order in broker_orders:
        direction = order.direction.value if order.direction else "unknown"
        key = (order.symbol, direction)
        counts[key] = counts.get(key, 0) + 1
    for position in broker_positions:
        direction = position.direction.value if position.direction else "unknown"
        key = (position.symbol, direction)
        counts[key] = counts.get(key, 0) + 1
    return [
        _anomaly(
            ReconciliationAnomalyType.DUPLICATE_ORDER_SUSPICION,
            "high",
            symbol,
            f"broker reports {count} open orders/positions for {symbol} {direction}",
            payload={"direction": direction, "count": count},
        )
        for (symbol, direction), count in counts.items()
        if count > 1
    ]


def _snapshot_stale(timestamp: datetime | None, now: datetime, stale_minutes: float) -> bool:
    if timestamp is None:
        return False
    normalized = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    return now - normalized > timedelta(minutes=stale_minutes)


def _anomaly(
    anomaly_type: ReconciliationAnomalyType,
    severity: str,
    symbol: str | None,
    reason: str,
    *,
    internal_order_id: str | None = None,
    broker_order_id: str | None = None,
    broker_position_id: str | None = None,
    payload: dict[str, str | float | int | bool | None] | None = None,
) -> ReconciliationAnomaly:
    return ReconciliationAnomaly(
        anomaly_id=str(uuid.uuid4()),
        detected_at=datetime.now(timezone.utc),
        anomaly_type=anomaly_type,
        severity=severity,
        symbol=symbol,
        internal_order_id=internal_order_id,
        broker_order_id=broker_order_id,
        broker_position_id=broker_position_id,
        reason=reason,
        payload=payload or {},
    )
