"""Daily paper/demo risk limits for the automatic demo bot."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.execution.models import ExecutionOrder


class DailyRiskConfig(BaseModel):
    """Prudent runtime limits for one local demo bot session."""

    max_trades_per_day: int = Field(default=3, ge=1, le=100)
    max_open_trades: int = Field(default=2, ge=1, le=100)
    max_daily_loss_percent: float = Field(default=2.0, gt=0.0, le=25.0)
    max_consecutive_losses: int = Field(default=3, ge=1, le=20)
    cooldown_after_loss_minutes: float = Field(default=60.0, ge=0.0, le=10080.0)
    cooldown_after_trade_minutes: float = Field(default=30.0, ge=0.0, le=10080.0)

    @classmethod
    def from_env(cls) -> "DailyRiskConfig":
        """Load new env names, with legacy AUTO_BOT values as compatibility fallbacks."""

        return cls(
            max_trades_per_day=_env_int("MAX_TRADES_PER_DAY", _env_int("AUTO_BOT_MAX_TRADES_PER_DAY", 3)),
            max_open_trades=_env_int("MAX_OPEN_TRADES", _env_int("AUTO_BOT_MAX_OPEN_TRADES", 2)),
            max_daily_loss_percent=_env_float("MAX_DAILY_LOSS_PERCENT", 2.0),
            max_consecutive_losses=_env_int("MAX_CONSECUTIVE_LOSSES", 3),
            cooldown_after_loss_minutes=_env_float("COOLDOWN_AFTER_LOSS_MINUTES", 60.0),
            cooldown_after_trade_minutes=_env_float("COOLDOWN_AFTER_TRADE_MINUTES", _env_float("AUTO_BOT_COOLDOWN_MINUTES", 30.0)),
        )


class DailyRiskSummary(BaseModel):
    """Cycle-level risk status shown in logs and audit events."""

    trades_today: int
    open_trades: int
    daily_pnl: float
    daily_loss_percent: float
    remaining_trade_slots: int
    bot_risk_status: str
    consecutive_losses: int


class DailyLimitDecision(BaseModel):
    """Blocking decision for one candidate opportunity."""

    allowed: bool
    reasons: list[str]
    summary: DailyRiskSummary


def evaluate_daily_limits(
    orders: list[ExecutionOrder],
    *,
    symbol: str,
    now: datetime,
    config: DailyRiskConfig,
    risk_per_trade_percent: float = 0.25,
) -> DailyLimitDecision:
    """Return a conservative allow/block decision before creating a demo trade."""

    summary = summarize_daily_risk(orders, now=now, config=config, risk_per_trade_percent=risk_per_trade_percent)
    reasons: list[str] = []
    if any(order.is_open and order.request.symbol == symbol for order in orders):
        reasons.append(f"open paper position already exists for {symbol}")
    if summary.open_trades >= config.max_open_trades:
        reasons.append(f"max open trades {config.max_open_trades} reached")
    if summary.trades_today >= config.max_trades_per_day:
        reasons.append(f"daily trade cap {config.max_trades_per_day} reached")
    if summary.daily_loss_percent >= config.max_daily_loss_percent:
        reasons.append(f"daily loss {summary.daily_loss_percent:.2f}% reached limit {config.max_daily_loss_percent:.2f}%")
    if summary.consecutive_losses >= config.max_consecutive_losses:
        reasons.append(f"consecutive losses {summary.consecutive_losses} reached limit {config.max_consecutive_losses}")
    loss_cooldown = _loss_cooldown_reason(orders, now, config.cooldown_after_loss_minutes)
    if loss_cooldown:
        reasons.append(loss_cooldown)
    trade_cooldown = _trade_cooldown_reason(orders, symbol, now, config.cooldown_after_trade_minutes)
    if trade_cooldown:
        reasons.append(trade_cooldown)
    return DailyLimitDecision(allowed=not reasons, reasons=reasons, summary=summary)


def summarize_daily_risk(
    orders: list[ExecutionOrder],
    *,
    now: datetime,
    config: DailyRiskConfig,
    risk_per_trade_percent: float = 0.25,
) -> DailyRiskSummary:
    """Summarize current paper/demo exposure and today's realized outcome."""

    today = now.astimezone(timezone.utc).date()
    today_orders = [order for order in orders if order.created_at.astimezone(timezone.utc).date() == today]
    open_trades = sum(1 for order in orders if order.is_open)
    daily_pnl = round(sum(float(order.realized_pnl or 0.0) for order in today_orders), 4)
    daily_loss_percent = round(_daily_loss_percent(today_orders, risk_per_trade_percent), 4)
    remaining_slots = max(config.max_trades_per_day - len(today_orders), 0)
    status = "blocked" if (
        open_trades >= config.max_open_trades
        or len(today_orders) >= config.max_trades_per_day
        or daily_loss_percent >= config.max_daily_loss_percent
        or _consecutive_losses(orders) >= config.max_consecutive_losses
    ) else "ok"
    return DailyRiskSummary(
        trades_today=len(today_orders),
        open_trades=open_trades,
        daily_pnl=daily_pnl,
        daily_loss_percent=daily_loss_percent,
        remaining_trade_slots=remaining_slots,
        bot_risk_status=status,
        consecutive_losses=_consecutive_losses(orders),
    )


def _daily_loss_percent(today_orders: list[ExecutionOrder], risk_per_trade_percent: float) -> float:
    loss_r = sum(min(float(order.realized_r or 0.0), 0.0) for order in today_orders)
    return abs(loss_r) * risk_per_trade_percent


def _consecutive_losses(orders: list[ExecutionOrder]) -> int:
    closed = sorted((order for order in orders if order.closed_at is not None), key=lambda order: order.closed_at, reverse=True)
    count = 0
    for order in closed:
        if (order.realized_r or 0.0) < 0.0:
            count += 1
            continue
        break
    return count


def _loss_cooldown_reason(orders: list[ExecutionOrder], now: datetime, cooldown_minutes: float) -> str | None:
    if cooldown_minutes <= 0:
        return None
    latest_loss = max((order.closed_at for order in orders if order.closed_at is not None and (order.realized_r or 0.0) < 0.0), default=None)
    if latest_loss is not None and latest_loss >= now - timedelta(minutes=cooldown_minutes):
        return "cooldown after loss is active"
    return None


def _trade_cooldown_reason(orders: list[ExecutionOrder], symbol: str, now: datetime, cooldown_minutes: float) -> str | None:
    if cooldown_minutes <= 0:
        return None
    cutoff = now - timedelta(minutes=cooldown_minutes)
    symbol_times = [
        timestamp
        for order in orders
        if order.request.symbol == symbol
        for timestamp in [order.created_at, order.closed_at]
        if timestamp is not None
    ]
    if max(symbol_times, default=None) is not None and max(symbol_times) >= cutoff:
        return f"cooldown active for {symbol}"
    latest = max((order.created_at for order in orders if order.created_at is not None), default=None)
    if latest is not None and latest >= now - timedelta(minutes=cooldown_minutes):
        return "cooldown after trade is active"
    return None


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None or not raw.strip() else float(raw)
