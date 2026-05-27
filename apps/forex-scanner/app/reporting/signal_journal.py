"""Signal journal writer for multi-asset scanner cycles."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config.instruments import instrument_for_symbol
from app.execution.demo_bot import DemoBotCycleResult
from app.execution.rejected_signals import RejectedSignalRecord
from app.execution.models import ExecutionOrder

SIGNAL_JOURNAL_PATH = Path("reports/signal_journal.jsonl")


def append_cycle_signal_journal(
    result: DemoBotCycleResult,
    *,
    provider: str,
    broker: str,
    mode: str,
    watchlist: str,
    rejected_records: list[RejectedSignalRecord],
    created_orders: list[ExecutionOrder],
    output_path: Path = SIGNAL_JOURNAL_PATH,
) -> int:
    """Append one JSONL row per symbol decision."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_by_symbol = {record.symbol: record for record in rejected_records if record.cycle_id == result.cycle_id}
    created_by_symbol = {order.request.symbol: order for order in created_orders if order.request.source_opportunity_id == result.cycle_id}

    rows = []
    for decision in result.decisions:
        row = _base_row(result.cycle_id, provider=provider, broker=broker, mode=mode, watchlist=watchlist, symbol=decision.symbol)
        rejected = rejected_by_symbol.get(decision.symbol)
        created = created_by_symbol.get(decision.symbol)
        row.update(
            {
                "status": decision.status,
                "setup": decision.setup_subtype,
                "direction": None,
                "score": decision.final_score,
                "risk_reward": decision.risk_reward,
                "pattern_score": decision.pattern_score,
                "detected_patterns": decision.detected_patterns,
                "decision": "accepted" if decision.accepted else "rejected",
                "rejection_reasons": decision.reasons,
                "created_order": bool(decision.order_ids),
                "order_ids": decision.order_ids,
                "safety_status": "demo_only:true,live_trading_disabled:true",
            }
        )
        if rejected is not None:
            row.update(
                {
                    "entry": rejected.entry,
                    "stop_loss": rejected.stop_loss,
                    "take_profit": rejected.tp1,
                    "tp1": rejected.tp1,
                    "tp2": rejected.tp2,
                    "tp3": rejected.tp3,
                    "spread_atr": rejected.spread_atr,
                    "scan_only_reason": "; ".join([r for r in rejected.rejection_reasons if "scan_only" in r.lower()]) or None,
                    "adaptive_threshold_enabled": rejected.opportunity.adaptive_threshold_enabled if hasattr(rejected, "opportunity") and rejected.opportunity else None,
                    "base_min_score": rejected.opportunity.base_min_score if hasattr(rejected, "opportunity") and rejected.opportunity else None,
                    "adaptive_min_score": rejected.opportunity.adaptive_min_score if hasattr(rejected, "opportunity") and rejected.opportunity else None,
                    "effective_min_score": rejected.opportunity.effective_min_score if hasattr(rejected, "opportunity") and rejected.opportunity else None,
                    "adaptive_threshold_confidence": rejected.opportunity.adaptive_threshold_confidence if hasattr(rejected, "opportunity") and rejected.opportunity else None,
                    "adaptive_threshold_reason": rejected.opportunity.adaptive_threshold_reason if hasattr(rejected, "opportunity") and rejected.opportunity else None,
                }
            )
        if created is not None:
            row.update(
                {
                    "direction": created.request.direction.value,
                    "entry": created.request.entry_price,
                    "stop_loss": created.request.stop_loss,
                    "take_profit": created.request.take_profit,
                    "tp1": created.request.tp1,
                    "tp2": created.request.tp2,
                    "tp3": created.request.tp3,
                    "spread_atr": _safe_spread_atr(created),
                }
            )
            # Fetch adaptive info from source opportunity if available
            opp = created.request.extra_context.get("source_opportunity")

            if opp:
                row.update({
                    "adaptive_threshold_enabled": getattr(opp, "adaptive_threshold_enabled", None),
                    "base_min_score": getattr(opp, "base_min_score", None),
                    "adaptive_min_score": getattr(opp, "adaptive_min_score", None),
                    "effective_min_score": getattr(opp, "effective_min_score", None),
                    "adaptive_threshold_confidence": getattr(opp, "adaptive_threshold_confidence", None),
                    "adaptive_threshold_reason": getattr(opp, "adaptive_threshold_reason", None),
                })
        rows.append(row)

    with output_path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def _base_row(cycle_id: str, *, provider: str, broker: str, mode: str, watchlist: str, symbol: str) -> dict[str, object]:
    asset = instrument_for_symbol(symbol).asset_class.value
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cycle_id": cycle_id,
        "logical_symbol": symbol,
        "mt5_symbol": None,
        "asset_class": asset,
        "provider": provider,
        "broker": broker,
        "mode": mode,
        "watchlist": watchlist,
        "style": None,
        "session_name": None,
        "is_tradable_session": None,
        "next_tradable_window": None,
        "setup": None,
        "status": None,
        "direction": None,
        "score": None,
        "risk_reward": None,
        "pattern_score": None,
        "detected_patterns": [],
        "spread_atr": None,
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "decision": None,
        "rejection_reasons": [],
        "scan_only_reason": None,
        "executable_candidate": False,
        "created_order": False,
        "order_ids": [],
        "safety_status": None,
    }


def _safe_spread_atr(order: ExecutionOrder) -> float | None:
    spread = order.request.spread_at_signal or 0.0
    atr = order.request.atr_at_signal or 0.0
    if atr <= 0:
        return None
    return float(spread / atr)
