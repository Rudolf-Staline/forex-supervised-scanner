"""Trading journal and event export utilities for paper trading review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from app.execution.models import ExecutionOrder, PaperBlockRecord, TradeEvent

JournalScalar = str | float | int | bool | None


class TradeJournalEntry(BaseModel):
    """Operator-facing journal row reconstructed from paper execution state."""

    signal_id: str
    trade_id: str
    symbol: str
    setup_family: str
    setup_subtype: str
    style: str | None = None
    signal_timestamp: datetime | None = None
    activation_timestamp: datetime | None = None
    entry_timestamp: datetime | None = None
    exit_timestamp: datetime | None = None
    status: str
    status_transitions: list[str] = Field(default_factory=list)
    entry_rationale_summary: str | None = None
    block_reasons: list[str] = Field(default_factory=list)
    downgrade_reasons: list[str] = Field(default_factory=list)
    execution_assumptions_used: dict[str, JournalScalar] = Field(default_factory=dict)
    session: str | None = None
    regime_context: str | None = None
    spread_at_signal: float | None = None
    spread_at_fill: float | None = None
    slippage_estimate: float | None = None
    execution_adjustment: float | None = None
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    stop_movement_history: list[dict[str, JournalScalar]] = Field(default_factory=list)
    partial_close_history: list[dict[str, JournalScalar]] = Field(default_factory=list)
    realized_pnl: float | None = None
    realized_r_multiple: float | None = None
    mae: float | None = None
    mfe: float | None = None
    time_in_trade_minutes: float | None = None
    invalidation_reason: str | None = None
    cancellation_reason: str | None = None
    expiration_reason: str | None = None
    portfolio_snapshot: dict[str, JournalScalar] = Field(default_factory=dict)
    broker_mode: str | None = None
    broker_name: str | None = None
    broker_order_id: str | None = None
    broker_position_id: str | None = None
    broker_state: str | None = None
    broker_transitions: list[dict[str, JournalScalar]] = Field(default_factory=list)
    broker_submission: dict[str, JournalScalar] = Field(default_factory=dict)
    broker_acknowledgement: dict[str, JournalScalar] = Field(default_factory=dict)
    reconciliation_status: str | None = None
    reconciliation_reason: str | None = None


def journal_entries_from_orders(
    orders: list[ExecutionOrder],
    blocks: list[PaperBlockRecord] | None = None,
) -> list[TradeJournalEntry]:
    """Build queryable journal entries from orders and blocked opportunities."""

    entries = [_entry_from_order(order) for order in orders]
    entries.extend(_entry_from_block(block) for block in (blocks or []))
    return sorted(entries, key=_entry_sort_timestamp)


def all_trade_events(orders: list[ExecutionOrder], blocks: list[PaperBlockRecord] | None = None) -> list[TradeEvent]:
    """Return a timestamp-sorted event stream for orders and guardrail blocks."""

    events: list[TradeEvent] = []
    for order in orders:
        events.extend(order.events)
    for block in blocks or []:
        events.extend(block.events)
    return sorted(events, key=_event_sort_timestamp)


def reconstruct_event_trail(trade_id: str, events: list[TradeEvent]) -> list[TradeEvent]:
    """Return the chronological event trail for one trade or block id."""

    return sorted((event for event in events if event.trade_id == trade_id), key=_event_sort_timestamp)


def export_trading_journal(
    orders: list[ExecutionOrder],
    blocks: list[PaperBlockRecord],
    output_dir: Path,
) -> dict[str, Path]:
    """Write CSV, JSON, and Markdown exports for operator review."""

    output_dir.mkdir(parents=True, exist_ok=True)
    entries = journal_entries_from_orders(orders, blocks)
    events = all_trade_events(orders, blocks)
    journal_frame = _journal_frame(entries)
    events_frame = _events_frame(events)
    summary = _journal_summary(entries, events)
    outputs = {
        "journal_csv": output_dir / "journal.csv",
        "journal_json": output_dir / "journal.json",
        "events_csv": output_dir / "events.csv",
        "events_json": output_dir / "events.json",
        "summary": output_dir / "summary.md",
    }
    journal_frame.to_csv(outputs["journal_csv"], index=False)
    outputs["journal_json"].write_text(json.dumps([entry.model_dump(mode="json") for entry in entries], indent=2) + "\n", encoding="utf-8")
    events_frame.to_csv(outputs["events_csv"], index=False)
    outputs["events_json"].write_text(json.dumps([event.model_dump(mode="json") for event in events], indent=2) + "\n", encoding="utf-8")
    outputs["summary"].write_text(_journal_summary_markdown(summary), encoding="utf-8")
    return outputs


def _entry_from_order(order: ExecutionOrder) -> TradeJournalEntry:
    return TradeJournalEntry(
        signal_id=order.request.source_opportunity_id or order.order_id,
        trade_id=order.order_id,
        symbol=order.request.symbol,
        setup_family=order.request.setup_family.value,
        setup_subtype=order.request.setup_subtype.value,
        style=order.request.style.value,
        signal_timestamp=order.signal_timestamp,
        activation_timestamp=order.activated_at,
        entry_timestamp=order.entry_timestamp,
        exit_timestamp=order.closed_at,
        status=order.status.value,
        status_transitions=[f"{event.occurred_at.isoformat()} {event.event_type.value}: {event.status}" for event in order.events],
        entry_rationale_summary=order.request.entry_rationale,
        execution_assumptions_used=order.execution_assumptions,
        session=order.request.session,
        regime_context=order.request.regime_context,
        spread_at_signal=order.request.spread_at_signal,
        spread_at_fill=order.spread_adjustment * 2.0 if order.spread_adjustment else None,
        slippage_estimate=order.estimated_slippage,
        execution_adjustment=order.estimated_slippage + order.spread_adjustment,
        stop_loss=order.request.stop_loss,
        tp1=order.request.tp1,
        tp2=order.request.tp2,
        tp3=order.request.tp3,
        stop_movement_history=[movement.model_dump(mode="json") for movement in order.stop_movements],
        partial_close_history=[partial.model_dump(mode="json") for partial in order.partial_exits],
        realized_pnl=order.realized_pnl,
        realized_r_multiple=order.realized_r,
        mae=order.mae,
        mfe=order.mfe,
        time_in_trade_minutes=order.time_in_trade_minutes,
        invalidation_reason=order.invalidation_reason,
        cancellation_reason=order.cancellation_reason,
        expiration_reason=order.expiration_reason,
        portfolio_snapshot=order.portfolio_snapshot,
        broker_mode=order.broker_mode,
        broker_name=order.broker_name,
        broker_order_id=order.broker_order_id,
        broker_position_id=order.broker_position_id,
        broker_state=order.broker_state.value if order.broker_state else None,
        broker_transitions=[transition.model_dump(mode="json") for transition in order.broker_transitions],
        broker_submission=order.broker_submission,
        broker_acknowledgement=order.broker_acknowledgement,
        reconciliation_status=order.reconciliation_status,
        reconciliation_reason=order.reconciliation_reason,
    )


def _entry_sort_timestamp(entry: TradeJournalEntry) -> datetime:
    timestamp = entry.signal_timestamp or entry.exit_timestamp
    if timestamp is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


def _event_sort_timestamp(event: TradeEvent) -> datetime:
    return event.occurred_at if event.occurred_at.tzinfo else event.occurred_at.replace(tzinfo=timezone.utc)


def _entry_from_block(block: PaperBlockRecord) -> TradeJournalEntry:
    return TradeJournalEntry(
        signal_id=block.block_id,
        trade_id=block.block_id,
        symbol=block.symbol,
        setup_family=block.setup_family,
        setup_subtype=block.setup_subtype,
        signal_timestamp=block.created_at,
        status="blocked",
        status_transitions=[f"{event.occurred_at.isoformat()} {event.event_type.value}: {event.status}" for event in block.events],
        block_reasons=block.reasons,
        portfolio_snapshot=block.portfolio_snapshot,
    )


def _journal_frame(entries: list[TradeJournalEntry]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for entry in entries:
        payload = entry.model_dump(mode="json")
        for key in (
            "status_transitions",
            "block_reasons",
            "downgrade_reasons",
            "execution_assumptions_used",
            "stop_movement_history",
            "partial_close_history",
            "portfolio_snapshot",
            "broker_transitions",
            "broker_submission",
            "broker_acknowledgement",
        ):
            payload[key] = json.dumps(payload[key], sort_keys=True)
        rows.append(payload)
    return pd.DataFrame(rows)


def _events_frame(events: list[TradeEvent]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": event.event_id,
                "trade_id": event.trade_id,
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at.isoformat(),
                "symbol": event.symbol,
                "status": event.status,
                "reason": event.reason,
                "payload": json.dumps(event.payload, sort_keys=True),
            }
            for event in events
        ]
    )


def _journal_summary(entries: list[TradeJournalEntry], events: list[TradeEvent]) -> dict[str, int | float]:
    closed = [entry for entry in entries if entry.realized_r_multiple is not None]
    wins = [entry for entry in closed if (entry.realized_r_multiple or 0.0) > 0.0]
    blocked = [entry for entry in entries if entry.status == "blocked"]
    return {
        "journal_entries": len(entries),
        "event_count": len(events),
        "closed_trades": len(closed),
        "blocked_trades": len(blocked),
        "win_rate": round(len(wins) / len(closed) * 100.0, 2) if closed else 0.0,
        "realized_r": round(sum(entry.realized_r_multiple or 0.0 for entry in closed), 4),
        "realized_pnl": round(sum(entry.realized_pnl or 0.0 for entry in closed), 4),
    }


def _journal_summary_markdown(summary: dict[str, int | float]) -> str:
    lines = [
        "# Trading Journal Summary",
        "",
        f"Journal entries: {summary['journal_entries']}",
        f"Lifecycle events: {summary['event_count']}",
        f"Closed trades: {summary['closed_trades']}",
        f"Blocked trades: {summary['blocked_trades']}",
        f"Win rate: {summary['win_rate']}%",
        f"Realized R: {summary['realized_r']}",
        f"Realized PnL: {summary['realized_pnl']}",
        "",
    ]
    return "\n".join(lines)
