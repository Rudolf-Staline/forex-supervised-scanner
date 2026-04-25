"""Typed execution models shared by paper and future broker adapters."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle


class OrderStatus(str, Enum):
    """Execution lifecycle state for a simulated or broker-backed order."""

    PENDING_OPPORTUNITY = "pending_opportunity"
    OPEN_TRADE = "open_trade"
    PARTIALLY_CLOSED = "partially_closed_trade"
    FULLY_CLOSED = "fully_closed_trade"
    MISSED_TRADE = "missed_trade"
    CANCELLED_TRADE = "cancelled_trade"
    EXPIRED_TRADE = "expired_trade"
    PENDING = "pending"
    ACTIVE = "active"
    CLOSED = "closed"
    CANCELED = "canceled"
    REJECTED = "rejected"


class CloseReason(str, Enum):
    """Reason an order left the market."""

    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    MANUAL = "manual"
    CANCELED = "canceled"
    EXPIRED = "expired"
    MISSED_TRIGGER = "missed_trigger"
    SETUP_INVALIDATED = "setup_invalidated"
    ACTIVATION_TIMEOUT = "activation_timeout"
    REJECTED = "rejected"


class TradeEventType(str, Enum):
    """Structured lifecycle events used to reconstruct paper-trade history."""

    SIGNAL_DETECTED = "signal_detected"
    SIGNAL_WATCHLISTED = "signal_watchlisted"
    SIGNAL_APPROVED = "signal_approved"
    SIGNAL_PREMIUM = "signal_premium"
    TRADE_ACTIVATED = "trade_activated"
    TRADE_BLOCKED = "trade_blocked"
    TRADE_CANCELLED = "trade_cancelled"
    TRADE_EXPIRED = "trade_expired"
    TRADE_MISSED = "trade_missed"
    TRADE_ENTERED = "trade_entered"
    TRADE_PARTIALLY_CLOSED = "trade_partially_closed"
    TRADE_CLOSED = "trade_closed"
    STOP_MOVED = "stop_moved"
    GUARDRAIL_TRIGGERED = "guardrail_triggered"
    BROKER_INTENT_CREATED = "broker_intent_created"
    BROKER_PRETRADE_VALIDATED = "broker_pretrade_validated"
    BROKER_VALIDATION_FAILED = "broker_validation_failed"
    BROKER_SUBMIT_REQUESTED = "broker_submit_requested"
    BROKER_SUBMITTED = "broker_submitted"
    BROKER_ACKNOWLEDGED = "broker_acknowledged"
    BROKER_PARTIALLY_FILLED = "broker_partially_filled"
    BROKER_FILLED = "broker_filled"
    BROKER_REJECTED = "broker_rejected"
    BROKER_CANCELLED = "broker_cancelled"
    BROKER_CANCEL_REQUESTED = "broker_cancel_requested"
    BROKER_MODIFY_REQUESTED = "broker_modify_requested"
    BROKER_MODIFIED = "broker_modified"
    BROKER_CLOSE_REQUESTED = "broker_close_requested"
    BROKER_CLOSED = "broker_closed"
    BROKER_UNREACHABLE = "broker_unreachable"
    BROKER_RETRY_EXHAUSTED = "broker_retry_exhausted"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"
    BROKER_HEALTH_DEGRADED = "broker_health_degraded"
    BROKER_RECONNECT_ATTEMPTED = "broker_reconnect_attempted"
    BROKER_RECOVERY_ACTION = "broker_recovery_action"
    BROKER_STARTUP_RESYNC = "broker_startup_resync"
    BROKER_INCIDENT_OPENED = "broker_incident_opened"
    BROKER_INCIDENT_CLOSED = "broker_incident_closed"
    BROKER_EXECUTION_BLOCKED_OPERATIONAL = "broker_execution_blocked_operational"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    LIVE_GUARDRAIL_TRIGGERED = "live_guardrail_triggered"
    OPERATOR_OVERRIDE = "operator_override"


class BrokerOrderState(str, Enum):
    """Broker-facing order state machine used by sandbox/live adapters."""

    INTENT_CREATED = "intent_created"
    PRETRADE_VALIDATED = "pretrade_validated"
    VALIDATION_FAILED = "validation_failed"
    SUBMIT_REQUESTED = "submit_requested"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    MODIFY_REQUESTED = "modify_requested"
    MODIFIED = "modified"
    CLOSE_REQUESTED = "close_requested"
    CLOSED = "closed"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    BROKER_UNREACHABLE = "broker_unreachable"
    RETRY_EXHAUSTED = "retry_exhausted"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"


class BrokerErrorCategory(str, Enum):
    """Structured broker failure categories for retries, reports, and audits."""

    CONFIGURATION = "configuration"
    CONNECTIVITY = "connectivity"
    ACCOUNT_UNAVAILABLE = "account_unavailable"
    MARKET_DATA_UNAVAILABLE = "market_data_unavailable"
    ORDER_REJECTED = "order_rejected"
    TIMEOUT = "timeout"
    RETRY_EXHAUSTED = "retry_exhausted"
    UNKNOWN = "unknown"


class BrokerTransition(BaseModel):
    """One broker order-state transition with structured audit context."""

    transition_id: str
    order_id: str
    state: BrokerOrderState
    occurred_at: datetime
    reason: str | None = None
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class BrokerAccountState(BaseModel):
    """Normalized account snapshot returned by broker adapters."""

    broker: str
    mode: str
    connected: bool
    can_trade: bool
    balance: float | None = None
    equity: float | None = None
    free_margin: float | None = None
    currency: str | None = None
    open_positions: int = Field(default=0, ge=0)
    pending_orders: int = Field(default=0, ge=0)
    account_id: str | None = None
    server: str | None = None
    is_demo: bool | None = None
    raw_summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    retrieved_at: datetime | None = None
    health_status: str = "unknown"
    last_error: str | None = None
    error_category: BrokerErrorCategory | None = None
    consecutive_failures: int = Field(default=0, ge=0)


class BrokerOrderSnapshot(BaseModel):
    """Minimal broker-reported order snapshot used for reconciliation."""

    broker_order_id: str
    symbol: str
    direction: DirectionBias | None = None
    state: BrokerOrderState
    quantity: float | None = Field(default=None, ge=0.0)
    filled_quantity: float | None = Field(default=None, ge=0.0)
    entry_price: float | None = Field(default=None, gt=0.0)
    stop_loss: float | None = Field(default=None, gt=0.0)
    take_profit: float | None = Field(default=None, gt=0.0)
    updated_at: datetime | None = None
    raw_summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class BrokerPositionSnapshot(BaseModel):
    """Minimal broker-reported position snapshot used for reconciliation."""

    broker_position_id: str
    symbol: str
    direction: DirectionBias | None = None
    quantity: float = Field(ge=0.0)
    entry_price: float | None = Field(default=None, gt=0.0)
    stop_loss: float | None = Field(default=None, gt=0.0)
    take_profit: float | None = Field(default=None, gt=0.0)
    unrealized_pnl: float | None = None
    updated_at: datetime | None = None
    raw_summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class TradeEvent(BaseModel):
    """One auditable event in the lifecycle of a signal, order, or block."""

    event_id: str
    trade_id: str
    event_type: TradeEventType
    occurred_at: datetime
    symbol: str
    status: str
    reason: str | None = None
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class PartialExit(BaseModel):
    """One partial paper exit at a configured target."""

    target: str
    timestamp: datetime
    price: float = Field(gt=0.0)
    fraction: float = Field(gt=0.0, le=1.0)
    realized_r: float
    realized_pnl: float


class StopMovement(BaseModel):
    """One stop-loss movement made during paper execution."""

    timestamp: datetime
    from_stop: float = Field(gt=0.0)
    to_stop: float = Field(gt=0.0)
    reason: str


class OrderRequest(BaseModel):
    """Validated order request produced from an approved scanner opportunity."""

    symbol: str
    style: TradingStyle
    setup_family: SetupFamily
    setup_subtype: SetupSubtype
    direction: DirectionBias
    quantity_units: float = Field(gt=0.0)
    entry_price: float = Field(gt=0.0)
    stop_loss: float = Field(gt=0.0)
    take_profit: float = Field(gt=0.0)
    tp1: float | None = Field(default=None, gt=0.0)
    tp2: float | None = Field(default=None, gt=0.0)
    tp3: float | None = Field(default=None, gt=0.0)
    source_opportunity_id: str | None = None
    signal_timestamp: datetime | None = None
    source_status: str | None = None
    entry_rationale: str | None = None
    regime_context: str | None = None
    final_score: float | None = Field(default=None, ge=0.0, le=100.0)
    provider: str | None = None
    session: str | None = None
    spread_at_signal: float | None = Field(default=None, ge=0.0)
    atr_at_signal: float | None = Field(default=None, ge=0.0)
    data_quality_score: float | None = Field(default=None, ge=0.0, le=100.0)
    data_warning: str | None = None

    @model_validator(mode="after")
    def ensure_executable_direction_and_levels(self) -> "OrderRequest":
        if self.direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
            raise ValueError("paper execution requires a long or short direction")
        if self.direction == DirectionBias.LONG and not (self.stop_loss < self.entry_price < self.take_profit):
            raise ValueError("long order requires stop below entry and target above entry")
        if self.direction == DirectionBias.SHORT and not (self.take_profit < self.entry_price < self.stop_loss):
            raise ValueError("short order requires target below entry and stop above entry")
        return self


class ExecutionOrder(BaseModel):
    """Tracked execution order with activation and realized paper outcome fields."""

    order_id: str
    request: OrderRequest
    status: OrderStatus
    created_at: datetime
    signal_timestamp: datetime | None = None
    activated_at: datetime | None = None
    entry_timestamp: datetime | None = None
    closed_at: datetime | None = None
    simulated_entry: float | None = Field(default=None, gt=0.0)
    initial_stop_loss: float | None = Field(default=None, gt=0.0)
    exit_price: float | None = Field(default=None, gt=0.0)
    tp1_exit_price: float | None = Field(default=None, gt=0.0)
    tp2_exit_price: float | None = Field(default=None, gt=0.0)
    tp3_exit_price: float | None = Field(default=None, gt=0.0)
    close_reason: CloseReason | None = None
    bars_to_activation: int | None = Field(default=None, ge=0)
    bars_in_trade: int | None = Field(default=None, ge=0)
    time_in_trade_minutes: float | None = Field(default=None, ge=0.0)
    estimated_slippage: float = Field(default=0.0, ge=0.0)
    spread_adjustment: float = Field(default=0.0, ge=0.0)
    remaining_fraction: float = Field(default=1.0, ge=0.0, le=1.0)
    partial_exits: list[PartialExit] = Field(default_factory=list)
    stop_movements: list[StopMovement] = Field(default_factory=list)
    events: list[TradeEvent] = Field(default_factory=list)
    realized_r: float | None = None
    realized_pnl: float | None = None
    mae: float = 0.0
    mfe: float = 0.0
    rejection_reason: str | None = None
    cancellation_reason: str | None = None
    expiration_reason: str | None = None
    invalidation_reason: str | None = None
    execution_assumptions: dict[str, str | float | bool] = Field(default_factory=dict)
    portfolio_snapshot: dict[str, str | float | int] = Field(default_factory=dict)
    broker_mode: str | None = None
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_position_id: str | None = None
    broker_state: BrokerOrderState | None = None
    broker_transitions: list[BrokerTransition] = Field(default_factory=list)
    broker_submission: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    broker_acknowledgement: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    filled_quantity: float = Field(default=0.0, ge=0.0)
    average_fill_price: float | None = Field(default=None, gt=0.0)
    reconciliation_status: str | None = None
    reconciliation_reason: str | None = None

    @property
    def is_open(self) -> bool:
        """Return true while the order can still become or remain a position."""

        return self.status in {
            OrderStatus.PENDING,
            OrderStatus.PENDING_OPPORTUNITY,
            OrderStatus.ACTIVE,
            OrderStatus.OPEN_TRADE,
            OrderStatus.PARTIALLY_CLOSED,
        }


class PaperBlockRecord(BaseModel):
    """A paper-trading opportunity blocked by portfolio or execution guardrails."""

    block_id: str
    created_at: datetime
    symbol: str
    status: str
    setup_family: str
    setup_subtype: str
    direction: str
    final_score: float | None = None
    reasons: list[str]
    portfolio_snapshot: dict[str, str | float | int] = Field(default_factory=dict)
    events: list[TradeEvent] = Field(default_factory=list)
