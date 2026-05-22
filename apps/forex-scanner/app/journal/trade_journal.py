"""CSV audit journal for every demo-bot decision."""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config.instruments import instrument_for_symbol
from app.core.types import Opportunity
from app.data.mt5_symbol_resolver import mt5_symbol_override_for
from app.execution.models import ExecutionOrder
from app.market.sessions import get_market_session
from app.notifications.notifier import safety_status_for_broker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRADE_JOURNAL_PATH = PROJECT_ROOT / "reports" / "trade_journal.csv"


@dataclass(frozen=True)
class TradeJournalDecision:
    """One audit row for a bot decision."""

    timestamp: str
    cycle_id: str
    asset_class: str
    logical_symbol: str
    mt5_symbol: str
    provider: str
    broker: str
    mode: str
    session_name: str
    is_tradable_session: bool
    setup: str
    status: str
    direction: str
    score: float | None
    risk_reward: float | None
    pattern_score: float
    detected_patterns: str
    spread_atr: float | None
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    tp1: float | None
    tp2: float | None
    tp3: float | None
    decision: str
    rejection_reasons: str
    scan_only_reason: str
    order_id: str
    position_size: float | None
    risk_percent: float | None
    created_order: bool
    safety_status: str


FIELDNAMES = list(TradeJournalDecision.__dataclass_fields__)


def decision_to_journal_record(
    *,
    cycle_id: str,
    opportunity: Opportunity,
    decision: Any,
    order: ExecutionOrder | None,
    timestamp: datetime,
    broker_mode: str | None = None,
    mode: str = "paper",
    risk_percent: float | None = None,
) -> TradeJournalDecision:
    """Convert one opportunity/decision pair to a CSV journal row."""

    broker = (broker_mode or os.getenv("BROKER_MODE", "paper")).strip().lower() or "paper"
    instrument = instrument_for_symbol(opportunity.symbol)
    session = get_market_session(opportunity.timestamp, instrument.asset_class, opportunity.symbol)
    return TradeJournalDecision(
        timestamp=timestamp.astimezone(timezone.utc).isoformat(),
        cycle_id=cycle_id,
        asset_class=instrument.asset_class.value,
        logical_symbol=opportunity.symbol,
        mt5_symbol=_mt5_symbol(opportunity.symbol),
        provider=opportunity.provider,
        broker=broker,
        mode=mode,
        session_name=session.session_name,
        is_tradable_session=session.is_tradable_session,
        setup=opportunity.setup_subtype.value,
        status=opportunity.status.value,
        direction=opportunity.direction.value,
        score=decision.final_score,
        risk_reward=decision.risk_reward,
        pattern_score=decision.pattern_score,
        detected_patterns="; ".join(decision.detected_patterns),
        spread_atr=_spread_atr(opportunity),
        entry=opportunity.entry,
        stop_loss=opportunity.stop_loss,
        take_profit=opportunity.take_profit,
        tp1=opportunity.tp1,
        tp2=opportunity.tp2,
        tp3=opportunity.tp3,
        decision="ACCEPT" if decision.accepted else "REJECT",
        rejection_reasons="; ".join(decision.reasons),
        scan_only_reason=_scan_only_reason(decision.reasons),
        order_id=order.order_id if order is not None else "",
        position_size=order.request.quantity_units if order is not None else None,
        risk_percent=risk_percent,
        created_order=order is not None,
        safety_status=safety_status_for_broker(broker),
    )


def append_trade_journal(records: Iterable[TradeJournalDecision], path: Path = TRADE_JOURNAL_PATH) -> None:
    """Append decision rows to the local audit CSV."""

    rows = list(records)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def load_trade_journal(path: Path = TRADE_JOURNAL_PATH) -> list[dict[str, str]]:
    """Load journal CSV rows."""

    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _mt5_symbol(symbol: str) -> str:
    override = mt5_symbol_override_for(symbol)
    if override:
        return override
    config = instrument_for_symbol(symbol)
    return config.mt5_symbol_candidates[0] if config.mt5_symbol_candidates else symbol.replace("/", "")


def _spread_atr(opportunity: Opportunity) -> float | None:
    if opportunity.spread is None or opportunity.atr is None or opportunity.atr <= 0:
        return None
    return opportunity.spread / opportunity.atr


def _scan_only_reason(reasons: list[str]) -> str:
    for reason in reasons:
        if "scan_only" in reason:
            return reason
    return ""
