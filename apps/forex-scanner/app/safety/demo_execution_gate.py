"""Strict pre-submit gate for MT5 demo orders.

The gate is intentionally read-only: it evaluates a paper order and returns
human-readable blockers before any MT5 order_send call can happen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config.instruments import AssetClass, instrument_for_symbol
from app.config.safety import DemoSafetyError, ensure_demo_safe_mode, ensure_mt5_demo_safe_mode
from app.config.settings import AppSettings
from app.core.types import DirectionBias, OpportunityStatus
from app.data.mt5_symbol_resolver import MT5SymbolResolver
from app.execution.models import BrokerAccountState, ExecutionOrder
from app.market.sessions import get_market_session
from app.risk.daily_limits import DailyRiskConfig, evaluate_daily_limits
from app.risk.position_sizing import PositionSizeResult, calculate_position_size

ALLOW_MULTI_ASSET_DEMO_TRADING_ENV = "ALLOW_MULTI_ASSET_DEMO_TRADING"
ENABLE_DEMO_EXECUTION_ENV = "ENABLE_DEMO_EXECUTION"
MAX_DEMO_ORDER_VOLUME_ENV = "MAX_DEMO_ORDER_VOLUME"
MAX_DEMO_ORDERS_PER_DAY_ENV = "MAX_DEMO_ORDERS_PER_DAY"
DEFAULT_MAX_DEMO_ORDER_VOLUME = 0.01
DEFAULT_MAX_DEMO_ORDERS_PER_DAY = 1


class DemoExecutionGateBlocked(RuntimeError):
    """Raised when an MT5 demo order does not pass the strict demo gate."""


@dataclass(frozen=True)
class DemoExecutionGateContext:
    """Inputs needed to explain or enforce MT5 demo executability."""

    settings: AppSettings
    order: ExecutionOrder
    broker_mode: str
    existing_orders: list[ExecutionOrder] = field(default_factory=list)
    account: BrokerAccountState | None = None
    mt5: object | None = None
    mt5_symbol: str | None = None
    symbol_info: object | None = None
    symbol_health_ok: bool | None = None
    demo_execution_confirmed: bool = False
    now: datetime | None = None
    daily_risk_config: DailyRiskConfig | None = None


@dataclass(frozen=True)
class DemoExecutionGateResult:
    """Allow/block result with terminal-friendly details."""

    allowed: bool
    reasons: list[str]
    details: dict[str, str | float | int | bool | None]
    position_size: PositionSizeResult | None = None

    @property
    def status(self) -> str:
        """Return the compact gate status string used in logs."""

        return "allowed" if self.allowed else "blocked"


def evaluate_demo_execution_gate(context: DemoExecutionGateContext) -> DemoExecutionGateResult:
    """Return whether a paper order may be submitted to MT5 demo."""

    order = context.order
    request = order.request
    instrument = instrument_for_symbol(request.symbol)
    now = context.now or datetime.now(timezone.utc)
    broker_mode = (context.broker_mode or "").strip().lower()
    reasons: list[str] = []

    _append_execution_environment_reasons(reasons)
    if broker_mode != "mt5_demo":
        reasons.append("broker=mt5_demo must be explicitly requested before MT5 demo execution")
        _append_demo_safe_mode_reasons(context.settings, reasons)
    else:
        _append_mt5_demo_safe_mode_reasons(context.settings, reasons)
        if os.getenv(ENABLE_DEMO_EXECUTION_ENV, "false").strip().lower() != "true":
            reasons.append("ENABLE_DEMO_EXECUTION must be true for ultra-limited MT5 demo execution")
        if not context.demo_execution_confirmed:
            reasons.append("--demo-execution-confirmed is required before any MT5 demo order")

    if context.account is None:
        reasons.append("MT5 demo account state is unavailable")
    else:
        server = context.account.server or ""
        if "demo" not in server.lower():
            reasons.append(f"account server must contain Demo, got {server or 'unknown'}")
        if context.account.is_demo is False:
            reasons.append("account is not marked as demo")
        if not context.account.can_trade:
            reasons.append("MT5 demo account is connected but not tradable")

    mt5_symbol = context.mt5_symbol
    symbol_health_ok = context.symbol_health_ok
    if context.mt5 is not None:
        resolution = MT5SymbolResolver(context.mt5, require_bars=True).resolve(request.symbol, require_bars=True)
        mt5_symbol = mt5_symbol or resolution.mt5_symbol
        symbol_health_ok = resolution.ok if symbol_health_ok is None else symbol_health_ok
        if not resolution.ok:
            reasons.append(f"symbol health check failed: {resolution.reason}")
    elif symbol_health_ok is False:
        reasons.append("symbol health check failed")
    elif symbol_health_ok is None:
        reasons.append("symbol health check is unavailable")

    session = get_market_session(request.signal_timestamp or now, instrument.asset_class, request.symbol)
    if not session.is_tradable_session:
        reasons.append(f"session is not tradable: {session.session_name}; next={session.next_tradable_window}")

    source_status = (request.source_status or "").strip().lower()
    allowed_statuses = {OpportunityStatus.APPROVED.value, OpportunityStatus.PREMIUM.value}
    if source_status not in allowed_statuses:
        reasons.append(f"status {source_status or 'missing'} is not executable by MT5 demo gate")

    score = request.final_score
    if score is None:
        reasons.append("missing final_score")
    elif score < instrument.min_score:
        reasons.append(f"score {score:.1f} below asset_class threshold {instrument.min_score:.1f}")

    risk_reward = _risk_reward(request.entry_price, request.stop_loss, request.take_profit, request.direction)
    if risk_reward is None:
        reasons.append("risk_reward is unavailable")
    elif risk_reward < instrument.min_risk_reward:
        reasons.append(f"risk_reward {risk_reward:.2f} below asset_class threshold {instrument.min_risk_reward:.2f}")

    spread_atr = _spread_atr(order)
    if spread_atr is None:
        reasons.append("spread_atr is unavailable")
    elif spread_atr > instrument.max_spread_atr:
        reasons.append(f"spread_atr {spread_atr:.3f} above asset_class threshold {instrument.max_spread_atr:.3f}")

    if instrument.asset_class != AssetClass.FOREX and os.getenv(ALLOW_MULTI_ASSET_DEMO_TRADING_ENV, "false").strip().lower() != "true":
        reasons.append(f"ALLOW_MULTI_ASSET_DEMO_TRADING is false for asset_class={instrument.asset_class.value}")

    comparable_orders = [item for item in context.existing_orders if item.order_id != order.order_id]
    daily = evaluate_daily_limits(
        comparable_orders,
        symbol=request.symbol,
        now=now,
        config=context.daily_risk_config or DailyRiskConfig.from_env(),
        risk_per_trade_percent=instrument.risk_percent,
    )
    reasons.extend(daily.reasons)
    if any(
        item.is_open and item.request.symbol == request.symbol and item.request.setup_subtype == request.setup_subtype
        for item in comparable_orders
    ):
        reasons.append(f"duplicate open trade for {request.symbol}/{request.setup_subtype.value}")
    demo_orders_today = _demo_orders_today(comparable_orders, now)
    max_demo_orders = _env_int(MAX_DEMO_ORDERS_PER_DAY_ENV, DEFAULT_MAX_DEMO_ORDERS_PER_DAY)
    if demo_orders_today >= max_demo_orders:
        reasons.append(f"MAX_DEMO_ORDERS_PER_DAY reached: {demo_orders_today}/{max_demo_orders}")

    position_size = _position_size(context, instrument, reasons)
    max_demo_volume = _env_float(MAX_DEMO_ORDER_VOLUME_ENV, DEFAULT_MAX_DEMO_ORDER_VOLUME)
    if position_size is not None and position_size.final_volume > max_demo_volume:
        reasons.append(f"final_volume {position_size.final_volume:.4f} exceeds MAX_DEMO_ORDER_VOLUME {max_demo_volume:.4f}")
    details: dict[str, str | float | int | bool | None] = {
        "broker": broker_mode or "unknown",
        "asset_class": instrument.asset_class.value,
        "logical_symbol": request.symbol,
        "mt5_symbol": mt5_symbol,
        "session_name": session.session_name,
        "is_tradable_session": session.is_tradable_session,
        "status": source_status or None,
        "score": score,
        "min_score": instrument.min_score,
        "risk_reward": risk_reward,
        "min_risk_reward": instrument.min_risk_reward,
        "spread_atr": spread_atr,
        "max_spread_atr": instrument.max_spread_atr,
        "daily_risk_status": daily.summary.bot_risk_status,
        "open_trades": daily.summary.open_trades,
        "trades_today": daily.summary.trades_today,
        "position_sizing_status": "available" if position_size is not None else "unavailable",
        "final_volume": position_size.final_volume if position_size is not None else None,
        "max_demo_order_volume": max_demo_volume,
        "demo_orders_today": demo_orders_today,
        "max_demo_orders_per_day": max_demo_orders,
        "demo_execution_confirmed": context.demo_execution_confirmed,
    }
    return DemoExecutionGateResult(allowed=not reasons, reasons=reasons, details=details, position_size=position_size)


def ensure_demo_execution_allowed(context: DemoExecutionGateContext) -> DemoExecutionGateResult:
    """Raise if the MT5 demo gate blocks a candidate order."""

    result = evaluate_demo_execution_gate(context)
    if not result.allowed:
        raise DemoExecutionGateBlocked("; ".join(result.reasons))
    return result


def format_demo_execution_gate_result(order_id: str, result: DemoExecutionGateResult) -> str:
    """Render one concise terminal line for operator diagnostics."""

    details = result.details
    reasons = "; ".join(result.reasons) if result.reasons else "all checks passed"
    return (
        f"demo_execution_gate={result.status} "
        f"paper_order_id={order_id} "
        f"symbol={details.get('logical_symbol')} "
        f"mt5_symbol={details.get('mt5_symbol') or '-'} "
        f"asset_class={details.get('asset_class')} "
        f"session_name={details.get('session_name')} "
        f"score={_fmt(details.get('score'))} "
        f"rr={_fmt(details.get('risk_reward'))} "
        f"spread_atr={_fmt(details.get('spread_atr'))} "
        f"position_sizing_status={details.get('position_sizing_status')} "
        f"volume={_fmt(details.get('final_volume'))} "
        f"reason={reasons}"
    )


def _append_execution_environment_reasons(reasons: list[str]) -> None:
    execution_mode = os.getenv("EXECUTION_MODE", "").strip().lower()
    if execution_mode not in {"paper", "demo"}:
        reasons.append(f"EXECUTION_MODE must be paper or demo, got {execution_mode or 'missing'}")
    allow_live = os.getenv("ALLOW_LIVE_TRADING", "").strip().lower()
    if allow_live != "false":
        reasons.append(f"ALLOW_LIVE_TRADING must be false, got {allow_live or 'missing'}")
    mt5_demo_only = os.getenv("MT5_DEMO_ONLY", "").strip().lower()
    if mt5_demo_only != "true":
        reasons.append(f"MT5_DEMO_ONLY must be true, got {mt5_demo_only or 'missing'}")


def _append_demo_safe_mode_reasons(settings: AppSettings, reasons: list[str]) -> None:
    try:
        ensure_demo_safe_mode(settings, context="demo execution gate")
    except DemoSafetyError as exc:
        reasons.append(str(exc))


def _append_mt5_demo_safe_mode_reasons(settings: AppSettings, reasons: list[str]) -> None:
    try:
        ensure_mt5_demo_safe_mode(settings, context="demo execution gate")
    except DemoSafetyError as exc:
        reasons.append(str(exc))


def _position_size(
    context: DemoExecutionGateContext,
    instrument,
    reasons: list[str],
) -> PositionSizeResult | None:
    if context.account is None or context.symbol_info is None:
        reasons.append("position_sizing_unavailable: missing account or symbol_info")
        return None
    try:
        max_demo_volume = _env_float(MAX_DEMO_ORDER_VOLUME_ENV, DEFAULT_MAX_DEMO_ORDER_VOLUME)
        return calculate_position_size(
            balance=context.account.balance or 0.0,
            risk_percent=instrument.risk_percent,
            entry_price=context.order.request.entry_price,
            stop_loss=context.order.request.stop_loss,
            symbol_info=context.symbol_info,
            max_volume=min(instrument.max_volume, max_demo_volume),
            require_tick_value=instrument.asset_class != AssetClass.FOREX,
        )
    except ValueError as exc:
        reasons.append(str(exc))
        return None


def _risk_reward(entry: float, stop_loss: float, take_profit: float, direction: DirectionBias) -> float | None:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    if direction == DirectionBias.LONG:
        reward = take_profit - entry
    elif direction == DirectionBias.SHORT:
        reward = entry - take_profit
    else:
        return None
    if reward <= 0:
        return None
    return reward / risk


def _spread_atr(order: ExecutionOrder) -> float | None:
    spread = order.request.spread_at_signal
    atr = order.request.atr_at_signal
    if spread is None or atr is None or atr <= 0:
        return None
    return spread / atr


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return "n/a"
    return str(value)


def _demo_orders_today(orders: list[ExecutionOrder], now: datetime) -> int:
    today = now.date()
    count = 0
    for order in orders:
        if order.created_at.date() != today:
            continue
        if order.broker_mode == "mt5_demo" or order.broker_name == "mt5" or bool(order.broker_order_id):
            count += 1
    return count


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default
