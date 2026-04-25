"""Paper trading orchestration for approved scanner opportunities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config.settings import AppSettings
from app.core.types import Opportunity, OpportunityStatus
from app.execution.models import ExecutionOrder, OrderRequest, PaperBlockRecord, TradeEvent, TradeEventType
from app.execution.paper import PaperExecutor
from app.execution.validation import PreTradeValidator
from app.risk.guardrails import PortfolioGuardrails


@dataclass(frozen=True)
class PaperTradingResult:
    """Orders created and opportunities blocked by guardrails."""

    orders: list[ExecutionOrder] = field(default_factory=list)
    blocked: dict[str, list[str]] = field(default_factory=dict)
    block_records: list[PaperBlockRecord] = field(default_factory=list)


class PaperTradingService:
    """Convert approved/premium scanner opportunities into guarded paper orders."""

    def __init__(
        self,
        settings: AppSettings,
        executor: PaperExecutor | None = None,
        guardrails: PortfolioGuardrails | None = None,
        validator: PreTradeValidator | None = None,
    ) -> None:
        self.settings = settings
        self.executor = executor or PaperExecutor(settings)
        self.guardrails = guardrails or PortfolioGuardrails(settings)
        self.validator = validator or PreTradeValidator(settings, self.guardrails)

    def submit_approved(self, opportunities: list[Opportunity]) -> PaperTradingResult:
        """Create paper orders for approved or premium opportunities that pass guardrails."""

        created: list[ExecutionOrder] = []
        blocked: dict[str, list[str]] = {}
        block_records: list[PaperBlockRecord] = []
        for opportunity in opportunities:
            key = f"{opportunity.symbol}:{opportunity.setup_subtype.value}:{opportunity.status.value}"
            if opportunity.status not in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}:
                continue
            open_orders = self.executor.sync_positions()
            closed_orders = _closed_orders(self.executor.all_orders())
            decision = self.validator.validate(opportunity, open_orders, closed_orders)
            if not decision.allowed:
                blocked[key] = decision.reasons
                block_records.append(_block_record(opportunity, decision.reasons, decision.portfolio_snapshot))
                continue
            try:
                request = _request_from_opportunity(opportunity, self.settings)
            except ValueError as exc:
                blocked[key] = [str(exc)]
                block_records.append(_block_record(opportunity, [str(exc)], decision.portfolio_snapshot))
                continue
            order = self.executor.place_order(request)
            created.append(self.executor.set_portfolio_snapshot(order.order_id, decision.portfolio_snapshot))
        return PaperTradingResult(orders=created, blocked=blocked, block_records=block_records)


def _request_from_opportunity(opportunity: Opportunity, settings: AppSettings) -> OrderRequest:
    if opportunity.entry is None or opportunity.stop_loss is None or opportunity.take_profit is None:
        raise ValueError("approved opportunity is missing executable entry, stop, or target")
    return OrderRequest(
        symbol=opportunity.symbol,
        style=opportunity.style,
        setup_family=opportunity.setup_family,
        setup_subtype=opportunity.setup_subtype,
        direction=opportunity.direction,
        quantity_units=settings.execution.default_quantity_units,
        entry_price=opportunity.entry,
        stop_loss=opportunity.stop_loss,
        take_profit=opportunity.take_profit,
        tp1=opportunity.tp1,
        tp2=opportunity.tp2,
        tp3=opportunity.tp3,
        signal_timestamp=opportunity.timestamp,
        source_status=opportunity.status.value,
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


def _closed_orders(orders: list[ExecutionOrder]) -> list[ExecutionOrder]:
    return [order for order in orders if not order.is_open]


def _block_record(opportunity: Opportunity, reasons: list[str], snapshot: dict[str, str | float | int]) -> PaperBlockRecord:
    block_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    events = [
        TradeEvent(
            event_id=str(uuid.uuid4()),
            trade_id=block_id,
            event_type=TradeEventType.TRADE_BLOCKED,
            occurred_at=created_at,
            symbol=opportunity.symbol,
            status=opportunity.status.value,
            reason="; ".join(reasons),
            payload={"final_score": opportunity.final_score, "setup_subtype": opportunity.setup_subtype.value},
        )
    ]
    events.extend(
        TradeEvent(
            event_id=str(uuid.uuid4()),
            trade_id=block_id,
            event_type=TradeEventType.GUARDRAIL_TRIGGERED,
            occurred_at=created_at,
            symbol=opportunity.symbol,
            status=opportunity.status.value,
            reason=reason,
            payload={"final_score": opportunity.final_score},
        )
        for reason in reasons
    )
    return PaperBlockRecord(
        block_id=block_id,
        created_at=created_at,
        symbol=opportunity.symbol,
        status=opportunity.status.value,
        setup_family=opportunity.setup_family.value,
        setup_subtype=opportunity.setup_subtype.value,
        direction=opportunity.direction.value,
        final_score=opportunity.final_score,
        reasons=reasons,
        portfolio_snapshot=snapshot,
        events=events,
    )
