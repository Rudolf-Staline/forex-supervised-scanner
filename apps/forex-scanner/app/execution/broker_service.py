"""Guarded broker submission orchestration for approved scanner opportunities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config.settings import AppSettings
from app.core.types import DirectionBias, Opportunity, OpportunityStatus
from app.execution.base import ExecutionAdapter
from app.execution.broker import append_broker_transition, build_execution_adapter
from app.execution.broker_validation import BrokerPreTradeValidator, BrokerValidationContext
from app.execution.models import BrokerOrderState, ExecutionOrder, OrderRequest, OrderStatus, TradeEventType


@dataclass(frozen=True)
class BrokerSubmissionResult:
    """Broker orders submitted and validation-failed broker intents."""

    submitted: list[ExecutionOrder] = field(default_factory=list)
    blocked: list[ExecutionOrder] = field(default_factory=list)


class BrokerExecutionService:
    """Create broker intents only after validation and live-safety checks."""

    def __init__(
        self,
        settings: AppSettings,
        adapter: ExecutionAdapter | None = None,
        validator: BrokerPreTradeValidator | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter or build_execution_adapter(settings)
        self.validator = validator or BrokerPreTradeValidator(settings)

    def submit_approved(
        self,
        opportunities: list[Opportunity],
        *,
        context: BrokerValidationContext | None = None,
    ) -> BrokerSubmissionResult:
        """Submit approved/premium opportunities to broker sandbox/live mode."""

        submitted: list[ExecutionOrder] = []
        blocked: list[ExecutionOrder] = []
        open_orders = self.adapter.sync_positions()
        closed_orders: list[ExecutionOrder] = []
        account = self.adapter.query_account_state()
        for opportunity in opportunities:
            if opportunity.status not in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}:
                continue
            validation = self.validator.validate_opportunity(opportunity, open_orders, closed_orders, account, context=context)
            if not validation.allowed:
                blocked.append(_validation_failed_order(self.settings, opportunity, validation.reasons))
                continue
            request = _request_from_opportunity(opportunity, validation.resolved_quantity or self.settings.broker.default_volume_lots)
            try:
                order = self.adapter.place_order(request)
            except Exception as exc:
                blocked.append(_validation_failed_order(self.settings, opportunity, [str(exc)]))
                continue
            submitted.append(order)
            open_orders.append(order)
        return BrokerSubmissionResult(submitted=submitted, blocked=blocked)


def _request_from_opportunity(opportunity: Opportunity, quantity: float) -> OrderRequest:
    if opportunity.entry is None or opportunity.stop_loss is None or opportunity.take_profit is None:
        raise ValueError("approved opportunity is missing executable entry, stop, or target")
    return OrderRequest(
        symbol=opportunity.symbol,
        style=opportunity.style,
        setup_family=opportunity.setup_family,
        setup_subtype=opportunity.setup_subtype,
        direction=opportunity.direction,
        quantity_units=quantity,
        entry_price=opportunity.entry,
        stop_loss=opportunity.stop_loss,
        take_profit=opportunity.take_profit,
        tp1=opportunity.tp1,
        tp2=opportunity.tp2,
        tp3=opportunity.tp3,
        source_status=opportunity.status.value,
        signal_timestamp=opportunity.timestamp,
        entry_rationale=opportunity.explanation,
        regime_context=opportunity.regime.value,
        final_score=opportunity.final_score,
        provider=opportunity.provider,
        session=opportunity.session.value if opportunity.session else None,
        spread_at_signal=opportunity.spread,
        atr_at_signal=opportunity.atr,
        data_quality_score=opportunity.data_quality.score if opportunity.data_quality else None,
        data_warning=opportunity.data_warning,
    )


def _validation_failed_order(settings: AppSettings, opportunity: Opportunity, reasons: list[str]) -> ExecutionOrder:
    now = datetime.now(timezone.utc)
    entry = opportunity.entry or 1.0
    if opportunity.direction == DirectionBias.SHORT:
        stop_loss = opportunity.stop_loss or entry + 0.001
        take_profit = opportunity.take_profit or entry - 0.001
    else:
        stop_loss = opportunity.stop_loss or entry - 0.001
        take_profit = opportunity.take_profit or entry + 0.001
    request = OrderRequest(
        symbol=opportunity.symbol,
        style=opportunity.style,
        setup_family=opportunity.setup_family,
        setup_subtype=opportunity.setup_subtype,
        direction=opportunity.direction,
        quantity_units=settings.broker.default_volume_lots,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        source_status=opportunity.status.value,
        signal_timestamp=opportunity.timestamp,
        entry_rationale=opportunity.explanation,
        regime_context=opportunity.regime.value,
        final_score=opportunity.final_score,
        provider=opportunity.provider,
        session=opportunity.session.value if opportunity.session else None,
        spread_at_signal=opportunity.spread,
        atr_at_signal=opportunity.atr,
        data_quality_score=opportunity.data_quality.score if opportunity.data_quality else None,
        data_warning=opportunity.data_warning,
    )
    order = ExecutionOrder(
        order_id=str(uuid.uuid4()),
        request=request,
        status=OrderStatus.REJECTED,
        created_at=now,
        signal_timestamp=opportunity.timestamp,
        initial_stop_loss=request.stop_loss,
        rejection_reason="; ".join(reasons),
        broker_mode=settings.execution.mode,
        broker_name=settings.broker.provider,
        broker_state=BrokerOrderState.VALIDATION_FAILED,
        execution_assumptions={"broker": settings.broker.provider, "mode": settings.execution.mode, "live_money": settings.execution.mode == "broker_live"},
    )
    order = append_broker_transition(order, BrokerOrderState.VALIDATION_FAILED, TradeEventType.BROKER_VALIDATION_FAILED, now, reason=order.rejection_reason)
    for reason in reasons:
        order = append_broker_transition(order, BrokerOrderState.VALIDATION_FAILED, TradeEventType.LIVE_GUARDRAIL_TRIGGERED, now, reason=reason)
    return order
