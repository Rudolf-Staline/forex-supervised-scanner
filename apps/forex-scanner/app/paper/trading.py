"""Paper trading orchestration for approved scanner opportunities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from app.config.safety import ensure_demo_bot_safe_mode, ensure_demo_safe_mode
from app.config.settings import AppSettings
from app.core.types import Opportunity, OpportunityStatus
from app.brokers.paper_broker import RealisticPaperBroker
from app.execution.models import CloseReason, ExecutionOrder, OrderRequest, PaperBlockRecord, TradeEvent, TradeEventType
from app.execution.paper import PaperExecutor
from app.execution.validation import PreTradeValidator
from app.risk.guardrails import PortfolioGuardrails
from app.storage.database import Database

PaperSignalSource = Literal["manual", "demo_bot"]


@dataclass(frozen=True)
class PaperTradingResult:
    """Orders created and opportunities blocked by guardrails."""

    orders: list[ExecutionOrder] = field(default_factory=list)
    blocked: dict[str, list[str]] = field(default_factory=dict)
    block_records: list[PaperBlockRecord] = field(default_factory=list)


@dataclass(frozen=True)
class PaperSignalSubmission:
    """Result returned by the simple paper-trading submission facade."""

    source: PaperSignalSource
    order: ExecutionOrder | None = None
    block_record: PaperBlockRecord | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def created(self) -> bool:
        return self.order is not None


class PaperTradingService:
    """Convert approved/premium scanner opportunities into guarded paper orders."""

    def __init__(
        self,
        settings: AppSettings,
        executor: PaperExecutor | None = None,
        guardrails: PortfolioGuardrails | None = None,
        validator: PreTradeValidator | None = None,
        existing_orders: list[ExecutionOrder] | None = None,
        paper_broker: RealisticPaperBroker | None = None,
    ) -> None:
        self.settings = settings
        ensure_demo_bot_safe_mode(settings, context="paper trading service")
        self.executor = executor or PaperExecutor(settings)
        if existing_orders:
            self.executor.seed_orders(existing_orders)
        self.guardrails = guardrails or PortfolioGuardrails(settings)
        self.validator = validator or PreTradeValidator(settings, self.guardrails)
        self.paper_broker = paper_broker or RealisticPaperBroker()

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
            fill = self.paper_broker.simulate_request(request)
            if not fill.accepted:
                blocked[key] = fill.reasons
                block_records.append(_block_record(opportunity, fill.reasons, {**decision.portfolio_snapshot, **fill.assumptions()}))
                continue
            order = self.executor.place_order(request)
            order = self.executor.set_portfolio_snapshot(order.order_id, decision.portfolio_snapshot)
            created.append(self.paper_broker.decorate_order(order, fill))
        return PaperTradingResult(orders=created, blocked=blocked, block_records=block_records)


def submit_signal_to_paper(
    signal: Opportunity,
    *,
    settings: AppSettings,
    database: Database,
    source: PaperSignalSource = "manual",
    notes: str | None = None,
) -> PaperSignalSubmission:
    """Validate one scanner signal, create one local paper order, and persist audit/journal data.

    The function is intentionally broker-free: it calls the central demo safety lock,
    reuses the existing paper executor and guardrails, writes the source/notes into
    the paper order, and stores an auditable event in SQLite.
    """

    ensure_demo_bot_safe_mode(settings, context="submit signal to paper")
    if source not in {"manual", "demo_bot"}:
        raise ValueError("paper source must be 'manual' or 'demo_bot'")

    validation_reasons = _manual_signal_validation_reasons(signal)
    if validation_reasons:
        block = _decorate_block(_block_record(signal, validation_reasons, {}), source, notes)
        database.save_paper_blocks([block])
        database.rebuild_trading_journal()
        return PaperSignalSubmission(source=source, block_record=block, reasons=validation_reasons)

    service = PaperTradingService(settings, existing_orders=database.load_paper_orders())
    result = service.submit_approved([signal])
    if result.orders:
        order = _decorate_order_submission(result.orders[0], source, notes)
        database.save_paper_orders([order])
        database.rebuild_trading_journal()
        return PaperSignalSubmission(source=source, order=order)

    block = result.block_records[0] if result.block_records else _block_record(signal, ["paper guardrails blocked submission"], {})
    block = _decorate_block(block, source, notes)
    database.save_paper_blocks([block])
    database.rebuild_trading_journal()
    reasons = block.reasons
    return PaperSignalSubmission(source=source, block_record=block, reasons=reasons)


def close_paper_order_manually(
    order: ExecutionOrder,
    *,
    settings: AppSettings,
    database: Database,
    exit_price: float | None = None,
    notes: str | None = None,
) -> ExecutionOrder:
    """Close a persisted paper order locally and append a manual audit event."""

    ensure_demo_safe_mode(settings, context="manual paper close")
    if not order.is_open:
        raise ValueError("only open or pending paper trades can be manually closed")
    executor = PaperExecutor(settings)
    executor.seed_orders([order])
    close_price = exit_price or order.simulated_entry or order.request.entry_price
    closed = executor.close_order(order.order_id, close_price, reason=CloseReason.MANUAL.value)
    closed = _decorate_manual_close(closed, notes)
    database.save_paper_orders([closed])
    database.rebuild_trading_journal()
    return closed


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


def _manual_signal_validation_reasons(signal: Opportunity) -> list[str]:
    reasons: list[str] = []
    if signal.status not in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}:
        reasons.append(f"status {signal.status.value} is not executable in paper trading")
    missing = [
        field_name
        for field_name, value in {
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "tp3": signal.tp3,
        }.items()
        if value is None
    ]
    if missing:
        reasons.append(f"missing paper levels: {', '.join(missing)}")
    return reasons


def _decorate_order_submission(order: ExecutionOrder, source: PaperSignalSource, notes: str | None) -> ExecutionOrder:
    timestamp = datetime.now(timezone.utc)
    clean_notes = (notes or "").strip()
    assumptions = {**order.execution_assumptions, "source": source}
    if clean_notes:
        assumptions["notes"] = clean_notes
    event = TradeEvent(
        event_id=str(uuid.uuid4()),
        trade_id=order.order_id,
        event_type=TradeEventType.PAPER_SIGNAL_SUBMITTED,
        occurred_at=timestamp,
        symbol=order.request.symbol,
        status=order.status.value,
        reason=f"paper order submitted from {source}",
        payload={
            "source": source,
            "notes": clean_notes or None,
            "entry": order.request.entry_price,
            "stop_loss": order.request.stop_loss,
            "tp1": order.request.tp1,
            "tp2": order.request.tp2,
            "tp3": order.request.tp3,
        },
    )
    return order.model_copy(update={"execution_assumptions": assumptions, "events": [*order.events, event]})


def _decorate_block(block: PaperBlockRecord, source: PaperSignalSource, notes: str | None) -> PaperBlockRecord:
    clean_notes = (notes or "").strip()
    snapshot = {**block.portfolio_snapshot, "source": source}
    if clean_notes:
        snapshot["notes"] = clean_notes
    events = [
        event.model_copy(update={"payload": {**event.payload, "source": source, "notes": clean_notes or None}})
        for event in block.events
    ]
    return block.model_copy(update={"portfolio_snapshot": snapshot, "events": events})


def _decorate_manual_close(order: ExecutionOrder, notes: str | None) -> ExecutionOrder:
    clean_notes = (notes or "").strip()
    assumptions = {**order.execution_assumptions, "manual_close": True}
    if clean_notes:
        assumptions["manual_close_notes"] = clean_notes
    if not order.events:
        return order.model_copy(update={"execution_assumptions": assumptions})
    events = list(order.events)
    last = events[-1]
    if last.event_type == TradeEventType.TRADE_CLOSED:
        events[-1] = last.model_copy(
            update={
                "reason": f"{last.reason or 'manual'}" + (f" | notes: {clean_notes}" if clean_notes else ""),
                "payload": {**last.payload, "source": "manual", "notes": clean_notes or None},
            }
        )
    return order.model_copy(update={"execution_assumptions": assumptions, "events": events})


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
