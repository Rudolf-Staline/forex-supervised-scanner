"""Portfolio/session guardrails for paper execution readiness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config.settings import AppSettings
from app.core.types import DirectionBias, Opportunity, OpportunityStatus, SessionName, TIMEFRAME_MINUTES
from app.execution.models import ExecutionOrder, OrderStatus


@dataclass(frozen=True)
class GuardrailDecision:
    """Decision returned before creating a paper order."""

    allowed: bool
    reasons: list[str] = field(default_factory=list)


class PortfolioGuardrails:
    """Apply optional exposure, drawdown, cooldown, spread, and data-quality checks."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def evaluate(
        self,
        opportunity: Opportunity,
        open_orders: list[ExecutionOrder],
        closed_orders: list[ExecutionOrder],
        *,
        now: datetime | None = None,
    ) -> GuardrailDecision:
        """Return whether an opportunity can be paper-executed now."""

        config = self.settings.portfolio_risk
        if not config.enabled:
            return GuardrailDecision(allowed=True)
        reasons: list[str] = []
        if opportunity.status not in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}:
            reasons.append(f"status {opportunity.status.value} is not executable")

        active_orders = [order for order in open_orders if order.is_open]
        if len(active_orders) >= config.max_simultaneous_trades:
            reasons.append(f"max simultaneous trades reached ({config.max_simultaneous_trades})")

        symbol_count = sum(1 for order in active_orders if order.request.symbol == opportunity.symbol)
        if symbol_count >= config.max_exposure_per_symbol:
            reasons.append(f"max exposure for {opportunity.symbol} would be exceeded")

        family_count = sum(1 for order in active_orders if order.request.setup_family == opportunity.setup_family)
        if family_count >= config.max_exposure_per_setup_family:
            reasons.append(f"max exposure for setup family {opportunity.setup_family.value} would be exceeded")

        subtype_count = sum(1 for order in active_orders if order.request.setup_subtype == opportunity.setup_subtype)
        if subtype_count >= config.max_exposure_per_setup_subtype:
            reasons.append(f"max exposure for setup subtype {opportunity.setup_subtype.value} would be exceeded")

        if opportunity.session is not None:
            session_count = sum(1 for order in active_orders if order.request.session == opportunity.session.value)
            if session_count >= config.max_exposure_per_session:
                reasons.append(f"max exposure for session {opportunity.session.value} would be exceeded")

        currency_exposure = _currency_exposure(active_orders)
        for currency, exposure in _opportunity_currency_exposure(opportunity).items():
            if abs(currency_exposure.get(currency, 0) + exposure) > config.max_exposure_per_currency:
                reasons.append(f"max exposure for {currency} would be exceeded")

        gross_currency_exposure = _gross_currency_exposure(active_orders)
        for currency in _opportunity_currency_exposure(opportunity):
            if gross_currency_exposure.get(currency, 0) + 1 > config.max_gross_exposure_per_currency:
                reasons.append(f"max gross exposure for {currency} would be exceeded")

        correlated_count = _correlated_symbol_count(active_orders, opportunity.symbol)
        if correlated_count >= config.max_correlated_symbol_exposure:
            reasons.append(f"correlated-symbol exposure for {opportunity.symbol} would be exceeded")

        data_quality_score = opportunity.data_quality.score if opportunity.data_quality else 100.0
        if data_quality_score < config.min_data_quality_for_entry:
            reasons.append(f"data quality {data_quality_score:.1f} below guardrail {config.min_data_quality_for_entry:.1f}")

        if opportunity.spread is not None and opportunity.atr is not None and opportunity.atr > 0.0:
            spread_to_atr = opportunity.spread / opportunity.atr
            if spread_to_atr > config.max_spread_to_atr_ratio:
                reasons.append(f"spread/ATR {spread_to_atr:.3f} above guardrail {config.max_spread_to_atr_ratio:.3f}")

        if config.block_off_hours and opportunity.session == SessionName.OFF_HOURS:
            reasons.append("off-hours session is blocked by portfolio guardrails")

        today = (now or datetime.now(timezone.utc)).date()
        daily_r = sum(order.realized_r or 0.0 for order in closed_orders if order.closed_at is not None and order.closed_at.date() == today)
        if daily_r <= -config.max_daily_loss_r:
            reasons.append(f"daily paper loss {daily_r:.2f} R reached limit {config.max_daily_loss_r:.2f} R")

        loss_streak = _consecutive_losses(closed_orders)
        if loss_streak >= config.cooldown_after_consecutive_losses:
            latest_loss = _latest_closed_loss(closed_orders)
            cooldown_minutes = TIMEFRAME_MINUTES[self.settings.styles[opportunity.style].entry_timeframe] * config.cooldown_bars
            current_time = now or datetime.now(timezone.utc)
            if latest_loss is None or latest_loss.closed_at is None or current_time - latest_loss.closed_at < timedelta(minutes=cooldown_minutes):
                reasons.append(f"cooldown after {config.cooldown_after_consecutive_losses} consecutive losses")

        return GuardrailDecision(allowed=not reasons, reasons=reasons)

    def snapshot(self, open_orders: list[ExecutionOrder], closed_orders: list[ExecutionOrder]) -> dict[str, str | float | int]:
        """Return compact portfolio state for paper-order persistence."""

        active_orders = [order for order in open_orders if order.is_open]
        closed_r = [order.realized_r or 0.0 for order in closed_orders if order.realized_r is not None]
        return {
            "open_orders": len(active_orders),
            "closed_orders": len(closed_orders),
            "realized_r": round(sum(closed_r), 4),
            "currency_exposure": str(_currency_exposure(active_orders)),
            "gross_currency_exposure": str(_gross_currency_exposure(active_orders)),
            "symbol_exposure": str(_count_by(active_orders, lambda order: order.request.symbol)),
            "family_exposure": str(_count_by(active_orders, lambda order: order.request.setup_family.value)),
            "subtype_exposure": str(_count_by(active_orders, lambda order: order.request.setup_subtype.value)),
            "session_exposure": str(_count_by(active_orders, lambda order: order.request.session or "unknown")),
        }


def _currency_exposure(orders: list[ExecutionOrder]) -> dict[str, int]:
    exposure: dict[str, int] = {}
    for order in orders:
        for currency, direction in _symbol_direction_exposure(order.request.symbol, order.request.direction).items():
            exposure[currency] = exposure.get(currency, 0) + direction
    return exposure


def _gross_currency_exposure(orders: list[ExecutionOrder]) -> dict[str, int]:
    exposure: dict[str, int] = {}
    for order in orders:
        for currency in _symbol_direction_exposure(order.request.symbol, order.request.direction):
            exposure[currency] = exposure.get(currency, 0) + 1
    return exposure


def _correlated_symbol_count(orders: list[ExecutionOrder], symbol: str) -> int:
    currencies = set(symbol.split("/"))
    if len(currencies) != 2:
        return 0
    count = 0
    for order in orders:
        order_currencies = set(order.request.symbol.split("/"))
        if len(order_currencies) == 2 and currencies.intersection(order_currencies):
            count += 1
    return count


def _count_by(orders: list[ExecutionOrder], key_fn: Callable[[ExecutionOrder], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for order in orders:
        key = str(key_fn(order))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _opportunity_currency_exposure(opportunity: Opportunity) -> dict[str, int]:
    return _symbol_direction_exposure(opportunity.symbol, opportunity.direction)


def _symbol_direction_exposure(symbol: str, direction: DirectionBias) -> dict[str, int]:
    parts = symbol.split("/")
    if len(parts) != 2 or direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
        return {}
    base, quote = parts
    if direction == DirectionBias.LONG:
        return {base: 1, quote: -1}
    return {base: -1, quote: 1}


def _consecutive_losses(closed_orders: list[ExecutionOrder]) -> int:
    count = 0
    fallback_time = datetime.min.replace(tzinfo=timezone.utc)
    terminal = {OrderStatus.CLOSED, OrderStatus.FULLY_CLOSED}
    for order in sorted((item for item in closed_orders if item.status in terminal), key=lambda item: item.closed_at or fallback_time, reverse=True):
        if (order.realized_r or 0.0) < 0.0:
            count += 1
            continue
        break
    return count


def _latest_closed_loss(closed_orders: list[ExecutionOrder]) -> ExecutionOrder | None:
    losses = [
        order
        for order in closed_orders
        if order.status in {OrderStatus.CLOSED, OrderStatus.FULLY_CLOSED} and (order.realized_r or 0.0) < 0.0 and order.closed_at is not None
    ]
    if not losses:
        return None
    return max(losses, key=lambda item: item.closed_at or datetime.min.replace(tzinfo=timezone.utc))
