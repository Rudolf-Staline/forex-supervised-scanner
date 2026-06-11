"""Realtime paper position lifecycle manager.

This module is deliberately local-paper only.  It advances persisted paper orders
from fresh market candles, records auditable lifecycle events already produced by
``PaperExecutor``, and never submits broker/live orders.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from app.config.safety import demo_safety_status
from app.config.settings import AppSettings, PROJECT_ROOT
from app.core.types import TIMEFRAME_MINUTES, Timeframe
from app.data.providers import MarketDataProvider
from app.execution.models import ExecutionOrder, OrderStatus, TradeEvent, TradeEventType
from app.execution.paper import PaperExecutor
from app.storage.database import Database

DEFAULT_REALTIME_POSITIONS_JSON = "realtime_paper_positions.json"
DEFAULT_REALTIME_POSITIONS_TXT = "realtime_paper_positions.txt"
DEFAULT_REALTIME_POSITIONS_DIR = PROJECT_ROOT / "reports"

_OPEN_STATUSES = {OrderStatus.OPEN_TRADE, OrderStatus.ACTIVE, OrderStatus.PARTIALLY_CLOSED}
_PENDING_STATUSES = {OrderStatus.PENDING_OPPORTUNITY, OrderStatus.PENDING}
_TERMINAL_STATUSES = {
    OrderStatus.FULLY_CLOSED,
    OrderStatus.CLOSED,
    OrderStatus.CANCELLED_TRADE,
    OrderStatus.CANCELED,
    OrderStatus.MISSED_TRADE,
    OrderStatus.EXPIRED_TRADE,
    OrderStatus.REJECTED,
}


class RealtimePaperPositionState(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    UNCHANGED = "UNCHANGED"


class RealtimePaperPositionEvent(BaseModel):
    order_id: str
    symbol: str
    event_type: str
    occurred_at: datetime
    status: str
    reason: str | None = None
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class RealtimePaperPositionUpdate(BaseModel):
    order_id: str
    symbol: str
    before_status: str
    after_status: str
    state: RealtimePaperPositionState
    events_created: list[RealtimePaperPositionEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    dry_run: bool = False


class RealtimePaperPositionConfig(BaseModel):
    provider: str = "auto"
    symbols: list[str]
    timeframe: Timeframe = Timeframe.M1
    dry_run: bool = True
    export_json: bool = False
    export_txt: bool = False
    reports_dir: Path = DEFAULT_REALTIME_POSITIONS_DIR
    max_age_seconds: float | None = None
    bars_to_fetch: int = Field(default=260, ge=2, le=2000)
    max_spread_atr_ratio: float = Field(default=0.25, gt=0.0, le=10.0)
    block_on_wide_spread: bool = False
    max_open_bars: int | None = Field(default=None, ge=1)
    session_close_warning_utc_hour: int | None = Field(default=21, ge=0, le=23)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        symbols = [symbol.strip().upper() for symbol in value if symbol.strip()]
        if not symbols:
            raise ValueError("at least one symbol is required")
        return symbols

    @field_validator("timeframe", mode="before")
    @classmethod
    def normalize_timeframe(cls, value: object) -> object:
        return value.value if isinstance(value, Timeframe) else str(value).upper() if isinstance(value, str) else value

    @property
    def effective_max_age_seconds(self) -> float:
        if self.max_age_seconds is not None:
            return self.max_age_seconds
        return float(TIMEFRAME_MINUTES[self.timeframe] * 60 * 4)


class RealtimePaperPositionReport(BaseModel):
    started_at: datetime
    completed_at: datetime
    provider: str
    symbols: list[str]
    timeframe: Timeframe
    positions_seen: int = 0
    pending_orders_seen: int = 0
    positions_updated: int = 0
    positions_closed: int = 0
    partial_exits_created: int = 0
    breakeven_moves: int = 0
    invalidations: int = 0
    activations: int = 0
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    safety_flags: dict[str, object] = Field(default_factory=dict)
    output_paths: dict[str, str] = Field(default_factory=dict)
    updates: list[RealtimePaperPositionUpdate] = Field(default_factory=list)
    dry_run: bool = True
    live_execution_allowed: bool = False
    order_send_called: bool = False


class RealtimePaperPositionManagerService:
    """Advance local paper orders through activation/open/exit lifecycle."""

    def __init__(
        self,
        settings: AppSettings,
        provider: MarketDataProvider,
        database: Database,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def update_pending_orders(self, config: RealtimePaperPositionConfig) -> RealtimePaperPositionReport:
        return self.evaluate_position_lifecycle(config)

    def update_open_positions(self, config: RealtimePaperPositionConfig) -> RealtimePaperPositionReport:
        return self.evaluate_position_lifecycle(config)

    def evaluate_position_lifecycle(self, config: RealtimePaperPositionConfig) -> RealtimePaperPositionReport:
        started_at = self.now_fn()
        config.reports_dir.mkdir(parents=True, exist_ok=True)
        original_orders = [order for order in self.database.load_paper_orders() if order.request.symbol.upper() in set(config.symbols)]
        positions_seen = sum(1 for order in original_orders if order.status in _OPEN_STATUSES)
        pending_seen = sum(1 for order in original_orders if order.status in _PENDING_STATUSES)
        updates: list[RealtimePaperPositionUpdate] = []
        warnings: list[str] = []
        blocking: list[str] = []

        tradable_orders = [order for order in original_orders if order.status not in _TERMINAL_STATUSES]
        if config.session_close_warning_utc_hour is not None and started_at.hour >= config.session_close_warning_utc_hour:
            warnings.append("session close is approaching; review open paper positions before the configured UTC close hour")

        refreshed_by_symbol: dict[str, pd.DataFrame] = {}
        for symbol in config.symbols:
            symbol_orders = [order for order in tradable_orders if order.request.symbol.upper() == symbol]
            if not symbol_orders:
                continue
            try:
                start = started_at - timedelta(minutes=TIMEFRAME_MINUTES[config.timeframe] * config.bars_to_fetch)
                bars = self.provider.get_ohlcv(symbol, config.timeframe, start=start, end=started_at)
            except Exception as exc:
                reason = f"provider failed for {symbol}: {exc}"
                blocking.append(reason)
                for order in symbol_orders:
                    updates.append(_blocked_update(order, reason, config.dry_run))
                continue
            fresh, stale_reason = _fresh_enough(bars, started_at, config.effective_max_age_seconds, symbol)
            spread_warning, spread_block = _spread_findings(bars, symbol, config.max_spread_atr_ratio)
            if spread_warning:
                warnings.append(spread_warning)
            if spread_block and config.block_on_wide_spread:
                blocking.append(spread_block)
                for order in symbol_orders:
                    updates.append(_blocked_update(order, spread_block, config.dry_run))
                continue
            if not fresh:
                blocking.append(stale_reason)
                for order in symbol_orders:
                    updates.append(_blocked_update(order, stale_reason, config.dry_run))
                continue
            refreshed_by_symbol[symbol] = bars

        executor = PaperExecutor(self.settings)
        executor.seed_orders(original_orders)
        before = {order.order_id: order for order in original_orders}
        for symbol, bars in refreshed_by_symbol.items():
            executor.process_market_data(symbol, bars)
        after_orders = executor.all_orders()
        changed_orders = []
        for after in after_orders:
            before_order = before.get(after.order_id)
            if before_order is None:
                continue
            if config.max_open_bars is not None and after.status in _OPEN_STATUSES and (after.bars_in_trade or 0) > config.max_open_bars:
                reason = f"stale paper position {after.order_id} exceeded {config.max_open_bars} bars in trade"
                blocking.append(reason)
                updates.append(_blocked_update(after, reason, config.dry_run))
                continue
            update = _build_update(before_order, after, config.dry_run)
            if update.events_created or update.before_status != update.after_status:
                updates.append(update)
                changed_orders.append(after)

        if changed_orders and not config.dry_run:
            self.database.save_paper_orders(changed_orders)
            self.database.rebuild_trading_journal()

        all_update_events = [event for update in updates for event in update.events_created]
        report = RealtimePaperPositionReport(
            started_at=started_at,
            completed_at=self.now_fn(),
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            positions_seen=positions_seen,
            pending_orders_seen=pending_seen,
            positions_updated=sum(1 for update in updates if update.state not in {RealtimePaperPositionState.BLOCKED, RealtimePaperPositionState.UNCHANGED}),
            positions_closed=sum(1 for update in updates if update.state in {RealtimePaperPositionState.CLOSED, RealtimePaperPositionState.CANCELLED}),
            partial_exits_created=sum(1 for event in all_update_events if event.event_type == TradeEventType.TRADE_PARTIALLY_CLOSED.value),
            breakeven_moves=sum(1 for event in all_update_events if event.event_type == TradeEventType.STOP_MOVED.value),
            invalidations=sum(1 for event in all_update_events if event.event_type == TradeEventType.TRADE_CANCELLED.value),
            activations=sum(1 for event in all_update_events if event.event_type == TradeEventType.TRADE_ACTIVATED.value),
            warnings=warnings,
            blocking_reasons=blocking,
            safety_flags=realtime_position_safety_flags(self.settings),
            updates=updates,
            dry_run=config.dry_run,
        )
        return export_realtime_position_report(report, config.reports_dir, export_json=config.export_json, export_txt=config.export_txt)

    def export_realtime_position_report(
        self,
        report: RealtimePaperPositionReport,
        reports_dir: Path,
        *,
        export_json: bool = True,
        export_txt: bool = True,
    ) -> RealtimePaperPositionReport:
        return export_realtime_position_report(report, reports_dir, export_json=export_json, export_txt=export_txt)


def export_realtime_position_report(
    report: RealtimePaperPositionReport,
    reports_dir: Path,
    *,
    export_json: bool = True,
    export_txt: bool = True,
) -> RealtimePaperPositionReport:
    output_paths: dict[str, str] = {}
    if export_json:
        output_paths["json"] = str(reports_dir / DEFAULT_REALTIME_POSITIONS_JSON)
    if export_txt:
        output_paths["txt"] = str(reports_dir / DEFAULT_REALTIME_POSITIONS_TXT)
    exported = report.model_copy(update={"output_paths": output_paths})
    if export_json:
        export_realtime_paper_positions_json(exported, reports_dir)
    if export_txt:
        export_realtime_paper_positions_txt(exported, reports_dir)
    return exported


def export_realtime_paper_positions_json(report: RealtimePaperPositionReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_POSITIONS_JSON
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_realtime_paper_positions_txt(report: RealtimePaperPositionReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_POSITIONS_TXT
    lines = [
        "realtime_paper_positions=completed",
        f"started_at={report.started_at.isoformat()}",
        f"completed_at={report.completed_at.isoformat()}",
        f"provider={report.provider}",
        f"symbols={','.join(report.symbols)}",
        f"timeframe={report.timeframe.value}",
        f"positions_seen={report.positions_seen}",
        f"pending_orders_seen={report.pending_orders_seen}",
        f"positions_updated={report.positions_updated}",
        f"positions_closed={report.positions_closed}",
        f"partial_exits_created={report.partial_exits_created}",
        f"breakeven_moves={report.breakeven_moves}",
        f"invalidations={report.invalidations}",
        f"order_send_called={str(report.order_send_called).lower()}",
        f"live_execution_allowed={str(report.live_execution_allowed).lower()}",
    ]
    for warning in report.warnings:
        lines.append(f"warning={warning}")
    for reason in report.blocking_reasons:
        lines.append(f"block={reason}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path



def realtime_position_safety_flags(settings: AppSettings) -> dict[str, object]:
    return demo_safety_status(settings) | {
        "paper_demo_only": True,
        "live_trading_enabled": False,
        "live_execution_allowed": False,
        "broker_live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_order_execution_allowed": False,
        "order_send_called": False,
        "env_mutation_performed": False,
    }

def _build_update(before: ExecutionOrder, after: ExecutionOrder, dry_run: bool) -> RealtimePaperPositionUpdate:
    new_events = after.events[len(before.events) :]
    state = _state_for(after.status)
    return RealtimePaperPositionUpdate(
        order_id=after.order_id,
        symbol=after.request.symbol,
        before_status=before.status.value,
        after_status=after.status.value,
        state=state,
        events_created=[_event_from_trade(after.order_id, event) for event in new_events],
        dry_run=dry_run,
    )


def _event_from_trade(order_id: str, event: TradeEvent) -> RealtimePaperPositionEvent:
    return RealtimePaperPositionEvent(
        order_id=order_id,
        symbol=event.symbol,
        event_type=event.event_type.value,
        occurred_at=event.occurred_at,
        status=event.status,
        reason=event.reason,
        payload=event.payload,
    )


def _state_for(status: OrderStatus) -> RealtimePaperPositionState:
    if status in _PENDING_STATUSES:
        return RealtimePaperPositionState.PENDING
    if status == OrderStatus.PARTIALLY_CLOSED:
        return RealtimePaperPositionState.PARTIALLY_CLOSED
    if status in _OPEN_STATUSES:
        return RealtimePaperPositionState.OPEN
    if status in {OrderStatus.CANCELLED_TRADE, OrderStatus.CANCELED, OrderStatus.MISSED_TRADE, OrderStatus.EXPIRED_TRADE}:
        return RealtimePaperPositionState.CANCELLED
    if status in {OrderStatus.FULLY_CLOSED, OrderStatus.CLOSED}:
        return RealtimePaperPositionState.CLOSED
    return RealtimePaperPositionState.UNCHANGED


def _blocked_update(order: ExecutionOrder, reason: str, dry_run: bool) -> RealtimePaperPositionUpdate:
    return RealtimePaperPositionUpdate(
        order_id=order.order_id,
        symbol=order.request.symbol,
        before_status=order.status.value,
        after_status=order.status.value,
        state=RealtimePaperPositionState.BLOCKED,
        blocking_reasons=[reason],
        dry_run=dry_run,
    )


def _fresh_enough(bars: pd.DataFrame, now: datetime, max_age_seconds: float, symbol: str) -> tuple[bool, str]:
    if bars.empty or not isinstance(bars.index, pd.DatetimeIndex):
        return False, f"latest candle is stale or unavailable for {symbol}"
    index = pd.to_datetime(bars.index, utc=True).sort_values()
    latest = index[-1].to_pydatetime()
    age = max(0.0, (now - latest).total_seconds())
    if age > max_age_seconds:
        return False, f"latest candle for {symbol} is stale: age_seconds={age:.1f} max_age_seconds={max_age_seconds:.1f}"
    return True, ""


def _spread_findings(bars: pd.DataFrame, symbol: str, max_spread_atr_ratio: float) -> tuple[str | None, str | None]:
    if bars.empty or "spread" not in bars.columns:
        return None, None
    spread_series = pd.to_numeric(bars["spread"], errors="coerce").dropna()
    if spread_series.empty:
        return None, None
    atr = _average_true_range(bars)
    if atr <= 0:
        return None, None
    ratio = float(spread_series.iloc[-1]) / atr
    if ratio > max_spread_atr_ratio:
        text = f"spread too wide for {symbol}: spread_atr_ratio={ratio:.3f} max={max_spread_atr_ratio:.3f}"
        return text, text
    return None, None


def _average_true_range(df: pd.DataFrame, window: int = 14) -> float:
    if df.empty or not {"high", "low", "close"}.issubset(df.columns):
        return 0.0
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    previous_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    value = tr.tail(window).mean()
    return float(value) if pd.notna(value) else 0.0
