"""Paper-only demo bot orchestration."""

from __future__ import annotations

import uuid
import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.config.safety import ensure_demo_bot_safe_mode
from app.config.instruments import AssetClass, instrument_for_symbol
from app.config.settings import AppSettings
from app.core.pipeline import ScannerService
from app.core.types import DirectionBias, MarketRegime, Opportunity, OpportunityStatus, SessionName, TradingStyle
from app.data.providers import MarketDataProvider
from app.execution.demo_bot_config import DemoBotConfig, EXECUTABLE_DEMO_STATUSES
from app.execution.models import ExecutionOrder, TradeEvent, TradeEventType
from app.execution.rejected_signals import RejectedSignalRecord
from app.journal.trade_journal import append_trade_journal, decision_to_journal_record
from app.market.sessions import get_market_session
from app.paper.trading import submit_signal_to_paper
from app.risk.daily_limits import DailyRiskConfig, DailyRiskSummary, evaluate_daily_limits, summarize_daily_risk
from app.storage.database import Database


class DemoBotDecision(BaseModel):
    """One bot decision for one scanned opportunity."""

    symbol: str
    status: str
    setup_subtype: str
    accepted: bool
    reasons: list[str] = Field(default_factory=list)
    order_ids: list[str] = Field(default_factory=list)
    final_score: float | None = None
    risk_reward: float | None = None
    detected_patterns: list[str] = Field(default_factory=list)
    pattern_score: float = 0.0


class DemoBotCycleResult(BaseModel):
    """Summary of one paper/demo bot cycle."""

    cycle_id: str
    started_at: datetime
    completed_at: datetime
    style: TradingStyle
    symbols: list[str]
    opportunities: int
    orders_created: int
    decisions: list[DemoBotDecision]
    scanned_opportunities: list[Opportunity] = Field(default_factory=list)
    logs: list[str]
    risk_summary: DailyRiskSummary


class DemoBotService:
    """Run supervised demo cycles that can only create paper orders."""

    def __init__(self, settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database
        self.config = DemoBotConfig.from_settings(settings)
        self.daily_risk_config = DailyRiskConfig.from_env()

    def run_cycle(self, style: TradingStyle, symbols: list[str], watchlist: str | None = None) -> DemoBotCycleResult:
        """Scan, filter, guard, create paper orders, and persist decision events."""

        ensure_demo_bot_safe_mode(self.settings, context="demo bot cycle")
        cycle_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        events = [_event(cycle_id, TradeEventType.DEMO_BOT_CYCLE_STARTED, started, "BOT", "started", "cycle started")]
        logs = [f"Cycle {cycle_id} started for {style.value} on {', '.join(symbols)}."]

        report = ScannerService(self.settings, self.provider, self.database).scan(style, symbols, timestamp=started)
        open_orders = [order for order in self.database.load_paper_orders() if order.is_open]
        existing_orders = self.database.load_paper_orders()
        controls = self.database.load_operator_controls()
        decisions: list[DemoBotDecision] = []
        created_orders: list[ExecutionOrder] = []
        rejected_records: list[RejectedSignalRecord] = []
        journal_records = []
        if controls.maintenance_mode:
            logs.append("Operator maintenance mode is active; every opportunity will be blocked.")
        if controls.degraded_mode:
            logs.append("Operator degraded mode is active; every opportunity will be blocked.")

        for opportunity in report.opportunities:
            created_order: ExecutionOrder | None = None
            decision = self._decide(
                opportunity,
                existing_orders=[*existing_orders, *created_orders],
                maintenance_mode=controls.maintenance_mode,
                degraded_mode=controls.degraded_mode,
                now=started,
            )
            if decision.accepted:
                submission = submit_signal_to_paper(opportunity, settings=self.settings, database=self.database, source="demo_bot")
                if submission.order:
                    created_order = submission.order
                    created_orders.append(submission.order)
                    decision.order_ids.append(submission.order.order_id)
                    logs.append(
                        f"ACCEPT {opportunity.symbol}: created paper order {', '.join(decision.order_ids)} "
                        f"detected_patterns={','.join(decision.detected_patterns) or '-'} pattern_score={decision.pattern_score:.2f}."
                    )
                else:
                    decision.accepted = False
                    decision.reasons.extend(submission.reasons)
                    if not decision.reasons:
                        decision.reasons.append("paper executor did not create an order")
                    logs.append(
                        f"REJECT {opportunity.symbol}: {'; '.join(decision.reasons)} "
                        f"detected_patterns={','.join(decision.detected_patterns) or '-'} pattern_score={decision.pattern_score:.2f}."
                    )
            else:
                logs.append(
                    f"REJECT {opportunity.symbol}: {'; '.join(decision.reasons)} "
                    f"detected_patterns={','.join(decision.detected_patterns) or '-'} pattern_score={decision.pattern_score:.2f}."
                )
            decisions.append(decision)
            journal_records.append(
                decision_to_journal_record(
                    cycle_id=cycle_id,
                    opportunity=opportunity,
                    decision=decision,
                    order=created_order,
                    timestamp=started,
                    broker_mode=os.getenv("BROKER_MODE", "paper"),
                    mode=self.settings.execution.mode,
                    risk_percent=self._risk_per_trade_percent(),
                )
            )
            events.append(_decision_event(cycle_id, opportunity, decision, started))
            if not decision.accepted:
                rejected_records.append(_rejected_signal_record(cycle_id, opportunity, decision, started, watchlist=watchlist))

        self.database.rebuild_trading_journal()
        append_trade_journal(journal_records)
        completed = datetime.now(timezone.utc)
        risk_summary = summarize_daily_risk(
            [*existing_orders, *created_orders],
            now=completed,
            config=self.daily_risk_config,
            risk_per_trade_percent=self._risk_per_trade_percent(),
        )
        logs.append(
            "Risk summary: "
            f"trades_today={risk_summary.trades_today} "
            f"open_trades={risk_summary.open_trades} "
            f"daily_pnl={risk_summary.daily_pnl:.4f} "
            f"remaining_trade_slots={risk_summary.remaining_trade_slots} "
            f"bot_risk_status={risk_summary.bot_risk_status}."
        )
        events.append(
            _event(
                cycle_id,
                TradeEventType.DEMO_BOT_CYCLE_COMPLETED,
                completed,
                "BOT",
                "completed",
                f"orders_created={len(created_orders)} decisions={len(decisions)}",
                payload={
                    "orders_created": len(created_orders),
                    "decisions": len(decisions),
                    "trades_today": risk_summary.trades_today,
                    "open_trades": risk_summary.open_trades,
                    "daily_pnl": risk_summary.daily_pnl,
                    "remaining_trade_slots": risk_summary.remaining_trade_slots,
                    "bot_risk_status": risk_summary.bot_risk_status,
                },
            )
        )
        self.database.save_rejected_signals(rejected_records)
        self.database.save_trade_events(events)
        return DemoBotCycleResult(
            cycle_id=cycle_id,
            started_at=started,
            completed_at=completed,
            style=style,
            symbols=symbols,
            opportunities=len(report.opportunities),
            orders_created=len(created_orders),
            decisions=decisions,
            scanned_opportunities=report.opportunities,
            logs=logs,
            risk_summary=risk_summary,
        )

    def _decide(
        self,
        opportunity: Opportunity,
        *,
        existing_orders: list[ExecutionOrder],
        maintenance_mode: bool,
        degraded_mode: bool,
        now: datetime,
    ) -> DemoBotDecision:
        reasons: list[str] = []
        score = opportunity.final_score
        if maintenance_mode:
            reasons.append("operator maintenance mode is active")
        if degraded_mode:
            reasons.append("operator degraded mode is active")
        if score == 0.0:
            reasons.extend(_zero_score_reasons(opportunity, self.settings))
        reasons.extend(_instrument_reasons(opportunity))
        if opportunity.status.value not in EXECUTABLE_DEMO_STATUSES:
            reasons.append(f"status {opportunity.status.value} is not executable by demo bot")
        elif opportunity.status.value not in self.config.allowed_statuses:
            reasons.append(f"status {opportunity.status.value} disabled by AUTO_BOT_ALLOWED_STATUSES")
        if score is None:
            reasons.append("missing final_score")
        else:
            # Respect adaptive threshold if it was successfully applied by the pipeline.
            is_adaptive = getattr(opportunity, "adaptive_threshold_enabled", False)
            mode_str = getattr(self.settings, "adaptive_thresholds", None).mode if getattr(self.settings, "adaptive_thresholds", None) else "report_only"
            can_use_adaptive = is_adaptive and mode_str == "scanner_effective"

            effective_min = getattr(opportunity, "effective_min_score", self.config.min_score)
            if effective_min is None:
                effective_min = self.config.min_score

            # We apply the adaptive minimum if adaptive logic is formally engaged,
            # trusting the engine to enforce hard_floor. Otherwise, use static config min_score.
            strict_min = effective_min if can_use_adaptive else self.config.min_score

            if score < strict_min:
                label = "adaptive threshold" if can_use_adaptive else "minimum"
                reasons.append(f"score {score:.1f} below {label} {strict_min:.1f}")

        if opportunity.risk_reward is None or opportunity.risk_reward < self.config.min_rr:
            reasons.append(f"risk/reward {opportunity.risk_reward or 0.0:.2f} below minimum {self.config.min_rr:.2f}")
        if _data_quality_score(opportunity) < self.settings.portfolio_risk.min_data_quality_for_entry:
            reasons.append(
                f"data quality {_data_quality_score(opportunity):.1f} below paper-entry threshold "
                f"{self.settings.portfolio_risk.min_data_quality_for_entry:.1f}"
            )
        reasons.extend(_level_reasons(opportunity))
        spread_reason = _spread_friction_reason(opportunity, self.settings)
        if spread_reason:
            reasons.append(spread_reason)
        reasons.extend(_market_context_reasons(opportunity))
        if opportunity.direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
            reasons.append("direction is not executable")
        risk_decision = evaluate_daily_limits(
            existing_orders,
            symbol=opportunity.symbol,
            now=now,
            config=self.daily_risk_config,
            risk_per_trade_percent=self._risk_per_trade_percent(),
        )
        reasons.extend(risk_decision.reasons)
        return DemoBotDecision(
            symbol=opportunity.symbol,
            status=opportunity.status.value,
            setup_subtype=opportunity.setup_subtype.value,
            accepted=not reasons,
            reasons=reasons,
            final_score=score,
            risk_reward=opportunity.risk_reward,
            detected_patterns=opportunity.detected_patterns,
            pattern_score=opportunity.pattern_score,
        )

    def _risk_per_trade_percent(self) -> float:
        from os import getenv

        raw = getenv("RISK_PER_TRADE_PERCENT")
        if raw is None or not raw.strip():
            return 0.25
        return float(raw)


def _level_reasons(opportunity: Opportunity) -> list[str]:
    reasons: list[str] = []
    missing_core = [
        field_name
        for field_name, value in {
            "entry": opportunity.entry,
            "stop_loss": opportunity.stop_loss,
            "take_profit": opportunity.take_profit,
        }.items()
        if value is None
    ]
    if missing_core:
        reasons.append(f"missing executable levels: {', '.join(missing_core)}")

    staged_targets = {"tp1": opportunity.tp1, "tp2": opportunity.tp2, "tp3": opportunity.tp3}
    missing_targets = [name for name, value in staged_targets.items() if value is None]
    if missing_targets:
        reasons.append(f"missing staged targets: {', '.join(missing_targets)}")

    if missing_core:
        return reasons
    entry = opportunity.entry
    stop_loss = opportunity.stop_loss
    take_profit = opportunity.take_profit
    if entry is None or stop_loss is None or take_profit is None:
        return reasons

    if opportunity.direction == DirectionBias.LONG:
        if stop_loss >= entry:
            reasons.append("stop loss must be below entry for a long setup")
        if take_profit <= entry:
            reasons.append("take profit must be above entry for a long setup")
        for name, value in staged_targets.items():
            if value is not None and value <= entry:
                reasons.append(f"{name} must be above entry for a long setup")
    elif opportunity.direction == DirectionBias.SHORT:
        if stop_loss <= entry:
            reasons.append("stop loss must be above entry for a short setup")
        if take_profit >= entry:
            reasons.append("take profit must be below entry for a short setup")
        for name, value in staged_targets.items():
            if value is not None and value >= entry:
                reasons.append(f"{name} must be below entry for a short setup")
    return reasons


def _zero_score_reasons(opportunity: Opportunity, settings: AppSettings) -> list[str]:
    """Return explicit machine-readable reasons for diagnostic score=0 rows."""

    reasons: list[str] = []
    if opportunity.setup_subtype.value == "none" or opportunity.raw_setup_family is None or opportunity.setup_family.value == "no_trade":
        reasons.append("no_setup_detected")
    if opportunity.direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
        reasons.append("missing_direction")
    if opportunity.entry is None:
        reasons.append("missing_entry")
    if opportunity.stop_loss is None:
        reasons.append("missing_stop_loss")
    if opportunity.take_profit is None:
        reasons.append("missing_take_profit")
    if opportunity.risk_reward is None or opportunity.risk_reward <= 0.0:
        reasons.append("invalid_risk_reward")
    if _data_quality_score(opportunity) < settings.portfolio_risk.min_data_quality_for_entry:
        reasons.append("data_quality_failed")
    return reasons


def _spread_friction_reason(opportunity: Opportunity, settings: AppSettings) -> str | None:
    if opportunity.spread is None:
        return None
    if opportunity.atr is None or opportunity.atr <= 0.0:
        return "spread is available but ATR is missing for friction check"
    spread_to_atr = opportunity.spread / opportunity.atr
    if spread_to_atr > settings.portfolio_risk.max_spread_to_atr_ratio:
        return f"spread/ATR {spread_to_atr:.3f} above demo bot threshold {settings.portfolio_risk.max_spread_to_atr_ratio:.3f}"
    return None


def _market_context_reasons(opportunity: Opportunity) -> list[str]:
    reasons: list[str] = []
    if opportunity.session == SessionName.OFF_HOURS:
        instrument = instrument_for_symbol(opportunity.symbol)
        session_info = get_market_session(opportunity.timestamp, instrument.asset_class, opportunity.symbol)
        reasons.append(
            "off-hours session is not executable by demo bot; "
            f"asset_class={session_info.asset_class} "
            f"session_name={session_info.session_name} "
            f"is_tradable_session={str(session_info.is_tradable_session).lower()} "
            f"next_tradable_window={session_info.next_tradable_window}"
        )

    blocked_regimes = {MarketRegime.HIGH_VOLATILITY, MarketRegime.NO_TRADE}
    for label, regime in {
        "market": opportunity.regime,
        "higher-timeframe": opportunity.htf_regime,
        "entry-timeframe": opportunity.entry_regime,
        "trigger-timeframe": opportunity.trigger_regime,
    }.items():
        if regime in blocked_regimes:
            reasons.append(f"{label} regime {regime.value} is not executable")
    return reasons


def _data_quality_score(opportunity: Opportunity) -> float:
    if opportunity.data_quality is None:
        return 100.0
    return opportunity.data_quality.score


def _instrument_reasons(opportunity: Opportunity) -> list[str]:
    config = instrument_for_symbol(opportunity.symbol)
    reasons: list[str] = []
    if config.asset_class != AssetClass.FOREX and config.scan_only and os.getenv("ALLOW_MULTI_ASSET_DEMO_TRADING", "false").strip().lower() != "true":
        reasons.append(f"scan_only reason=ALLOW_MULTI_ASSET_DEMO_TRADING is false for asset_class={config.asset_class.value}")
    if opportunity.final_score is not None and opportunity.final_score < config.min_score:
        reasons.append(f"instrument min_score {opportunity.final_score:.1f} below {config.min_score:.1f} for asset_class={config.asset_class.value}")
    if opportunity.risk_reward is not None and opportunity.risk_reward < config.min_risk_reward:
        reasons.append(f"instrument risk/reward {opportunity.risk_reward:.2f} below {config.min_risk_reward:.2f} for asset_class={config.asset_class.value}")
    spread_atr = _spread_atr(opportunity)
    if spread_atr is not None and spread_atr > config.max_spread_atr:
        reasons.append(f"instrument spread/ATR {spread_atr:.3f} above {config.max_spread_atr:.3f} for asset_class={config.asset_class.value}")
    return reasons


def _decision_event(cycle_id: str, opportunity: Opportunity, decision: DemoBotDecision, timestamp: datetime) -> TradeEvent:
    event_type = TradeEventType.DEMO_BOT_DECISION_ACCEPTED if decision.accepted else TradeEventType.DEMO_BOT_DECISION_REJECTED
    trade_id = decision.order_ids[0] if decision.order_ids else cycle_id
    return _event(
        trade_id,
        event_type,
        timestamp,
        opportunity.symbol,
        "accepted" if decision.accepted else "rejected",
        "; ".join(decision.reasons) if decision.reasons else "paper trade accepted",
        payload={
            "cycle_id": cycle_id,
            "score": decision.final_score,
            "risk_reward": decision.risk_reward,
            "setup_subtype": decision.setup_subtype,
            "order_ids": ",".join(decision.order_ids),
            "detected_patterns": ",".join(decision.detected_patterns),
            "pattern_score": decision.pattern_score,
        },
    )


def _rejected_signal_record(
    cycle_id: str,
    opportunity: Opportunity,
    decision: DemoBotDecision,
    timestamp: datetime,
    *,
    watchlist: str | None = None,
) -> RejectedSignalRecord:
    return RejectedSignalRecord(
        id=str(uuid.uuid4()),
        cycle_id=cycle_id,
        timestamp=timestamp,
        symbol=opportunity.symbol,
        setup=opportunity.setup_subtype.value,
        status=opportunity.status.value,
        score=decision.final_score,
        risk_reward=decision.risk_reward,
        pattern_score=decision.pattern_score,
        detected_patterns=list(decision.detected_patterns),
        market_regime=opportunity.regime.value if opportunity.regime else None,
        spread_atr=_spread_atr(opportunity),
        rejection_reasons=list(decision.reasons),
        entry=opportunity.entry,
        stop_loss=opportunity.stop_loss,
        tp1=opportunity.tp1,
        tp2=opportunity.tp2,
        tp3=opportunity.tp3,
        provider=opportunity.provider,
        broker=os.getenv("BROKER_MODE", "paper").strip().lower() or "paper",
        style=opportunity.style.value,
        watchlist=watchlist,
    )


def _spread_atr(opportunity: Opportunity) -> float | None:
    if opportunity.spread is None or opportunity.atr is None or opportunity.atr <= 0.0:
        return None
    return opportunity.spread / opportunity.atr


def _event(
    trade_id: str,
    event_type: TradeEventType,
    timestamp: datetime,
    symbol: str,
    status: str,
    reason: str,
    *,
    payload: dict[str, str | float | int | bool | None] | None = None,
) -> TradeEvent:
    return TradeEvent(
        event_id=str(uuid.uuid4()),
        trade_id=trade_id,
        event_type=event_type,
        occurred_at=timestamp,
        symbol=symbol,
        status=status,
        reason=reason,
        payload=payload or {},
    )


def demo_bot_control_event(event_type: TradeEventType, status: str, reason: str) -> TradeEvent:
    now = datetime.now(timezone.utc)
    return _event(str(uuid.uuid4()), event_type, now, "BOT", status, reason)
