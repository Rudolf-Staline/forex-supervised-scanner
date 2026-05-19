"""Paper-only demo bot orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.config.safety import ensure_demo_safe_mode
from app.config.settings import AppSettings
from app.core.pipeline import ScannerService
from app.core.types import DirectionBias, MarketRegime, Opportunity, OpportunityStatus, SessionName, TradingStyle
from app.data.providers import MarketDataProvider
from app.execution.demo_bot_config import DemoBotConfig, EXECUTABLE_DEMO_STATUSES
from app.execution.models import ExecutionOrder, TradeEvent, TradeEventType
from app.paper.trading import submit_signal_to_paper
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
    logs: list[str]


class DemoBotService:
    """Run supervised demo cycles that can only create paper orders."""

    def __init__(self, settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database
        self.config = DemoBotConfig.from_settings(settings)

    def run_cycle(self, style: TradingStyle, symbols: list[str]) -> DemoBotCycleResult:
        """Scan, filter, guard, create paper orders, and persist decision events."""

        ensure_demo_safe_mode(self.settings, context="demo bot cycle")
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
        open_symbols = {order.request.symbol for order in open_orders}
        daily_count = _trades_today(existing_orders, started)

        if controls.maintenance_mode:
            logs.append("Operator maintenance mode is active; every opportunity will be blocked.")
        if controls.degraded_mode:
            logs.append("Operator degraded mode is active; every opportunity will be blocked.")

        for opportunity in report.opportunities:
            decision = self._decide(
                opportunity,
                open_symbols=open_symbols,
                open_count=len(open_orders) + len(created_orders),
                daily_count=daily_count,
                existing_orders=existing_orders,
                maintenance_mode=controls.maintenance_mode,
                degraded_mode=controls.degraded_mode,
                now=started,
            )
            if decision.accepted:
                submission = submit_signal_to_paper(opportunity, settings=self.settings, database=self.database, source="demo_bot")
                if submission.order:
                    created_orders.append(submission.order)
                    existing_orders.append(submission.order)
                    decision.order_ids.append(submission.order.order_id)
                    open_symbols.add(opportunity.symbol)
                    daily_count += 1
                    logs.append(f"ACCEPT {opportunity.symbol}: created paper order {', '.join(decision.order_ids)}.")
                else:
                    decision.accepted = False
                    decision.reasons.extend(submission.reasons)
                    if not decision.reasons:
                        decision.reasons.append("paper executor did not create an order")
                    logs.append(f"REJECT {opportunity.symbol}: {'; '.join(decision.reasons)}.")
            else:
                logs.append(f"REJECT {opportunity.symbol}: {'; '.join(decision.reasons)}.")
            decisions.append(decision)
            events.append(_decision_event(cycle_id, opportunity, decision, started))

        self.database.rebuild_trading_journal()
        completed = datetime.now(timezone.utc)
        events.append(
            _event(
                cycle_id,
                TradeEventType.DEMO_BOT_CYCLE_COMPLETED,
                completed,
                "BOT",
                "completed",
                f"orders_created={len(created_orders)} decisions={len(decisions)}",
                payload={"orders_created": len(created_orders), "decisions": len(decisions)},
            )
        )
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
            logs=logs,
        )

    def _decide(
        self,
        opportunity: Opportunity,
        *,
        open_symbols: set[str],
        open_count: int,
        daily_count: int,
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
        if opportunity.status.value not in EXECUTABLE_DEMO_STATUSES:
            reasons.append(f"status {opportunity.status.value} is not executable by demo bot")
        elif opportunity.status.value not in self.config.allowed_statuses:
            reasons.append(f"status {opportunity.status.value} disabled by AUTO_BOT_ALLOWED_STATUSES")
        if score is None:
            reasons.append("missing final_score")
        elif score < self.config.min_score:
            reasons.append(f"score {score:.1f} below minimum {self.config.min_score:.1f}")
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
        if opportunity.symbol in open_symbols:
            reasons.append(f"open paper position already exists for {opportunity.symbol}")
        if open_count >= self.config.max_open_trades:
            reasons.append(f"max open trades {self.config.max_open_trades} reached")
        if daily_count >= self.config.max_trades_per_day:
            reasons.append(f"daily trade cap {self.config.max_trades_per_day} reached")
        cooldown = _cooldown_reason(opportunity.symbol, existing_orders, now, self.config.cooldown_minutes)
        if cooldown:
            reasons.append(cooldown)
        return DemoBotDecision(
            symbol=opportunity.symbol,
            status=opportunity.status.value,
            setup_subtype=opportunity.setup_subtype.value,
            accepted=not reasons,
            reasons=reasons,
            final_score=score,
            risk_reward=opportunity.risk_reward,
        )


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
        reasons.append("off-hours session is not executable by demo bot")

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


def _trades_today(orders: list[ExecutionOrder], now: datetime) -> int:
    today = now.astimezone(timezone.utc).date()
    return sum(1 for order in orders if order.created_at.astimezone(timezone.utc).date() == today)


def _cooldown_reason(symbol: str, orders: list[ExecutionOrder], now: datetime, cooldown_minutes: float) -> str | None:
    if cooldown_minutes <= 0.0:
        return None
    cutoff = now - timedelta(minutes=cooldown_minutes)
    matching_times = [
        timestamp
        for order in orders
        if order.request.symbol == symbol
        for timestamp in [order.created_at, order.closed_at]
        if timestamp is not None
    ]
    latest = max(matching_times, default=None)
    if latest is not None and latest >= cutoff:
        return f"cooldown active for {symbol}"
    return None


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
        },
    )


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
