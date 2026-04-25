"""Broker-specific pre-trade validation and live safety guardrails."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.config.settings import AppSettings
from app.core.types import Opportunity, SessionName
from app.execution.models import BrokerAccountState, ExecutionOrder, OrderRequest
from app.execution.validation import PreTradeValidator
from app.risk.guardrails import PortfolioGuardrails


class BrokerValidationContext(BaseModel):
    """Operational broker context used by live/sandbox safety checks."""

    account_state: BrokerAccountState | None = None
    daily_submitted_trades: int = Field(default=0, ge=0)
    daily_risk_used_pct: float = Field(default=0.0, ge=0.0)
    repeated_rejects: int = Field(default=0, ge=0)
    reconciliation_anomalies: int = Field(default=0, ge=0)
    connectivity_failures: int = Field(default=0, ge=0)
    open_incidents: int = Field(default=0, ge=0)
    severe_incidents: int = Field(default=0, ge=0)
    degraded_state_flags: list[str] = Field(default_factory=list)
    operator_control_reasons: list[str] = Field(default_factory=list)


class BrokerValidationResult(BaseModel):
    """Broker validation decision with account and sizing diagnostics."""

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    account_state: BrokerAccountState | None = None
    resolved_quantity: float | None = Field(default=None, gt=0.0)
    estimated_notional: float | None = Field(default=None, ge=0.0)
    estimated_risk: float | None = Field(default=None, ge=0.0)


class BrokerPreTradeValidator:
    """Validate an opportunity and order request before broker submission."""

    def __init__(self, settings: AppSettings, guardrails: PortfolioGuardrails | None = None) -> None:
        self.settings = settings
        self.guardrails = guardrails or PortfolioGuardrails(settings)
        self.pre_trade = PreTradeValidator(settings, self.guardrails)

    def validate_opportunity(
        self,
        opportunity: Opportunity,
        open_orders: list[ExecutionOrder],
        closed_orders: list[ExecutionOrder],
        account_state: BrokerAccountState,
        *,
        now: datetime | None = None,
        context: BrokerValidationContext | None = None,
    ) -> BrokerValidationResult:
        """Run scanner, portfolio, account, and live-safety checks."""

        base = self.pre_trade.validate(opportunity, open_orders, closed_orders, now=now)
        reasons = list(base.reasons)
        context = context or BrokerValidationContext(account_state=account_state)
        reasons.extend(_broker_mode_reasons(self.settings, account_state))
        reasons.extend(_account_reasons(self.settings, account_state, current_time=now or datetime.now(timezone.utc)))
        reasons.extend(_operational_reasons(self.settings, context))
        if self.settings.broker_safety.block_poor_session and opportunity.session == SessionName.OFF_HOURS:
            reasons.append("broker execution blocked by poor/off-hours session")
        if self.settings.broker_safety.prevent_duplicate_symbol and _has_duplicate_symbol(opportunity.symbol, open_orders, account_state):
            reasons.append(f"duplicate broker exposure for {opportunity.symbol} is blocked")
        if len([order for order in open_orders if order.is_open]) + account_state.open_positions >= self.settings.broker_safety.max_open_broker_positions:
            reasons.append(f"open broker position cap {self.settings.broker_safety.max_open_broker_positions} would be exceeded")
        resolved_quantity = min(self.settings.broker.default_volume_lots, self.settings.broker.max_volume_lots)
        estimated_notional = _estimated_notional(opportunity.entry, resolved_quantity)
        estimated_risk = _estimated_risk(opportunity.entry, opportunity.stop_loss, resolved_quantity)
        reasons.extend(_risk_reasons(self.settings, account_state, estimated_notional, estimated_risk))
        return BrokerValidationResult(
            allowed=not reasons,
            reasons=_dedupe(reasons),
            account_state=account_state,
            resolved_quantity=resolved_quantity,
            estimated_notional=estimated_notional,
            estimated_risk=estimated_risk,
        )

    def validate_request(
        self,
        request: OrderRequest,
        open_orders: list[ExecutionOrder],
        account_state: BrokerAccountState,
        *,
        context: BrokerValidationContext | None = None,
    ) -> BrokerValidationResult:
        """Validate a broker-neutral order request when no full opportunity is available."""

        reasons: list[str] = []
        context = context or BrokerValidationContext(account_state=account_state)
        reasons.extend(_broker_mode_reasons(self.settings, account_state))
        reasons.extend(_account_reasons(self.settings, account_state, current_time=datetime.now(timezone.utc)))
        reasons.extend(_operational_reasons(self.settings, context))
        if self.settings.broker_safety.prevent_duplicate_symbol:
            if any(order.is_open and order.request.symbol == request.symbol for order in open_orders):
                reasons.append(f"duplicate broker exposure for {request.symbol} is blocked")
        if request.quantity_units <= 0.0:
            reasons.append("position sizing could not be resolved")
        if request.quantity_units > self.settings.broker.max_volume_lots:
            reasons.append("requested volume exceeds broker.max_volume_lots")
        estimated_notional = _estimated_notional(request.entry_price, request.quantity_units)
        estimated_risk = _estimated_risk(request.entry_price, request.stop_loss, request.quantity_units)
        reasons.extend(_risk_reasons(self.settings, account_state, estimated_notional, estimated_risk))
        return BrokerValidationResult(
            allowed=not reasons,
            reasons=_dedupe(reasons),
            account_state=account_state,
            resolved_quantity=request.quantity_units,
            estimated_notional=estimated_notional,
            estimated_risk=estimated_risk,
        )


def _broker_mode_reasons(settings: AppSettings, account_state: BrokerAccountState) -> list[str]:
    reasons: list[str] = []
    mode = settings.execution.mode
    if mode == "broker_live":
        if not settings.execution_capabilities.broker_live_enabled:
            reasons.append("broker_live capability is disabled")
        if not settings.broker.live_enabled:
            reasons.append("broker live mode is disabled in config")
        if os.getenv(settings.broker.kill_switch_env, "").strip().lower() in {"1", "true", "yes", "on"}:
            reasons.append("broker kill switch environment flag is active")
        if os.getenv(settings.broker.live_confirmation_env) != settings.broker.live_confirmation_value:
            reasons.append(f"missing live confirmation env var {settings.broker.live_confirmation_env}")
    elif mode == "broker_sandbox":
        if not settings.execution_capabilities.broker_sandbox_enabled:
            reasons.append("broker_sandbox capability is disabled")
        if not settings.broker.sandbox_enabled:
            reasons.append("broker sandbox mode is disabled in config")
        if settings.broker.sandbox_requires_demo_account and account_state.is_demo is False:
            reasons.append("broker sandbox requires a demo/sandbox account")
    else:
        reasons.append(f"execution mode {mode} is not a broker mode")
    if mode == "broker_live" and account_state.is_demo is True:
        reasons.append("broker_live mode is connected to a demo account")
    return reasons


def _account_reasons(settings: AppSettings, account_state: BrokerAccountState, *, current_time: datetime) -> list[str]:
    reasons: list[str] = []
    if settings.broker_safety.require_connectivity and not account_state.connected:
        reasons.append("broker connectivity is not healthy")
    if settings.broker_safety.require_account_state and account_state.balance is None:
        reasons.append("broker account state could not be retrieved")
    if settings.broker_safety.require_account_state and account_state.retrieved_at is not None:
        retrieved_at = account_state.retrieved_at if account_state.retrieved_at.tzinfo else account_state.retrieved_at.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (current_time - retrieved_at).total_seconds())
        if age_seconds > settings.broker_safety.max_account_state_age_seconds:
            reasons.append(f"broker account state age {age_seconds:.1f}s exceeds {settings.broker_safety.max_account_state_age_seconds:.1f}s")
    if settings.broker_safety.block_on_unstable_connectivity and account_state.consecutive_failures > settings.broker_safety.max_connectivity_failures:
        reasons.append(f"broker connectivity failures {account_state.consecutive_failures} exceed {settings.broker_safety.max_connectivity_failures}")
    if not account_state.can_trade:
        reasons.append("broker account is not tradable")
    if account_state.free_margin is not None and account_state.free_margin <= 0.0:
        reasons.append("broker free margin is not positive")
    return reasons


def _operational_reasons(settings: AppSettings, context: BrokerValidationContext) -> list[str]:
    reasons: list[str] = []
    safety = settings.broker_safety
    if context.daily_submitted_trades >= safety.max_daily_submitted_trades:
        reasons.append(f"daily broker-submitted trade cap {safety.max_daily_submitted_trades} reached")
    if context.daily_risk_used_pct >= safety.max_daily_risk_pct:
        reasons.append(f"daily broker risk budget {safety.max_daily_risk_pct:.2f}% reached")
    if context.repeated_rejects >= safety.max_repeated_rejects:
        reasons.append(f"repeated broker reject cap {safety.max_repeated_rejects} reached")
    if safety.block_on_reconciliation_anomaly and context.reconciliation_anomalies > safety.max_reconciliation_anomalies:
        reasons.append(f"open reconciliation anomalies {context.reconciliation_anomalies} exceed {safety.max_reconciliation_anomalies}")
    if safety.block_on_unstable_connectivity and context.connectivity_failures > safety.max_connectivity_failures:
        reasons.append(f"broker connectivity failures {context.connectivity_failures} exceed {safety.max_connectivity_failures}")
    if context.severe_incidents > 0:
        reasons.append(f"broker operational incidents require manual intervention ({context.severe_incidents} severe)")
    if "manual_intervention_required" in context.degraded_state_flags:
        reasons.append("broker health requires manual intervention")
    if "broker_unavailable" in context.degraded_state_flags:
        reasons.append("broker health is unavailable")
    reasons.extend(context.operator_control_reasons)
    return reasons


def _risk_reasons(settings: AppSettings, account_state: BrokerAccountState, estimated_notional: float | None, estimated_risk: float | None) -> list[str]:
    reasons: list[str] = []
    if estimated_notional is None or estimated_risk is None:
        reasons.append("position sizing could not be resolved")
        return reasons
    if estimated_notional > settings.broker_safety.max_notional_per_trade:
        reasons.append(f"estimated notional {estimated_notional:.2f} exceeds cap {settings.broker_safety.max_notional_per_trade:.2f}")
    equity = account_state.equity or account_state.balance
    if equity is not None and equity > 0.0:
        risk_pct = estimated_risk / equity * 100.0
        if risk_pct > settings.broker_safety.max_risk_per_trade_pct:
            reasons.append(f"estimated risk {risk_pct:.3f}% exceeds cap {settings.broker_safety.max_risk_per_trade_pct:.3f}%")
    return reasons


def _has_duplicate_symbol(symbol: str, open_orders: list[ExecutionOrder], account_state: BrokerAccountState) -> bool:
    return any(order.is_open and order.request.symbol == symbol for order in open_orders)


def _estimated_notional(entry: float | None, quantity: float | None) -> float | None:
    if entry is None or quantity is None or entry <= 0.0 or quantity <= 0.0:
        return None
    return abs(entry * quantity * 100_000.0)


def _estimated_risk(entry: float | None, stop_loss: float | None, quantity: float | None) -> float | None:
    if entry is None or stop_loss is None or quantity is None or quantity <= 0.0:
        return None
    return abs(entry - stop_loss) * quantity * 100_000.0


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
