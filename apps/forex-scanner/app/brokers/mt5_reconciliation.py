"""Read-only MT5 demo reconciliation before any limited demo execution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.config.instruments import instrument_for_symbol
from app.execution.models import ExecutionOrder

FOREX_SCANNER_MAGIC_NUMBER_ENV = "FOREX_SCANNER_MAGIC_NUMBER"
DEFAULT_FOREX_SCANNER_MAGIC_NUMBER = 260522
ORDER_COMMENT_PREFIX = "ForexSupervisor"


@dataclass(frozen=True)
class MT5Exposure:
    """Normalized MT5 position/order/history item used for reconciliation."""

    ticket: str
    symbol: str
    volume: float
    magic: int | None
    comment: str
    item_type: str
    setup: str | None = None
    cycle_id: str | None = None

    @property
    def is_bot_item(self) -> bool:
        """Return true when magic/comment identify Forex Supervisor."""

        return self.magic == forex_scanner_magic_number() and self.comment.startswith(f"{ORDER_COMMENT_PREFIX}|")


@dataclass(frozen=True)
class MT5ReconciliationReport:
    """Read-only reconciliation result."""

    mt5_connected: bool
    account_server: str
    demo_only: bool
    open_positions: int
    pending_orders: int
    bot_positions: int
    foreign_positions: int
    duplicate_risk: bool
    reconciliation_status: str
    reasons: list[str] = field(default_factory=list)
    positions: list[MT5Exposure] = field(default_factory=list)
    pending: list[MT5Exposure] = field(default_factory=list)
    history: list[MT5Exposure] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    magic_number: int = DEFAULT_FOREX_SCANNER_MAGIC_NUMBER

    @property
    def block_new_orders(self) -> bool:
        """Return whether new bot orders should be blocked."""

        return self.reconciliation_status != "OK"


def forex_scanner_magic_number() -> int:
    """Return the Forex Supervisor MT5 magic number."""

    raw = os.getenv(FOREX_SCANNER_MAGIC_NUMBER_ENV, str(DEFAULT_FOREX_SCANNER_MAGIC_NUMBER)).strip()
    return int(raw or DEFAULT_FOREX_SCANNER_MAGIC_NUMBER)


def build_standard_order_comment(*, asset_class: str, symbol: str, setup: str, cycle_id: str) -> str:
    """Build the standard bot order comment for MT5 demo submissions."""

    return f"{ORDER_COMMENT_PREFIX}|{asset_class}|{symbol}|{setup}|{cycle_id}"[:31]


def build_order_comment_for_order(order: ExecutionOrder, *, cycle_id: str | None = None) -> str:
    """Build a standard MT5 comment from a local execution order."""

    instrument = instrument_for_symbol(order.request.symbol)
    return build_standard_order_comment(
        asset_class=instrument.asset_class.value,
        symbol=order.request.symbol,
        setup=order.request.setup_subtype.value,
        cycle_id=cycle_id or order.request.source_opportunity_id or order.order_id,
    )


def reconcile_mt5_demo(
    mt5: object,
    *,
    account: object | None = None,
    local_orders: list[ExecutionOrder] | None = None,
    max_open_positions: int = 2,
    history_days: int = 7,
) -> MT5ReconciliationReport:
    """Read MT5 state and compare it with local bot/journal state."""

    account = account if account is not None else _safe_call(mt5, "account_info")
    server = str(getattr(account, "server", "") or "")
    demo_only = _is_demo_account(mt5, account)
    positions = [_row_to_exposure(row, "position") for row in _safe_sequence_call(mt5, "positions_get")]
    pending = [_row_to_exposure(row, "pending_order") for row in _safe_sequence_call(mt5, "orders_get")]
    history = _load_history(mt5, history_days=history_days)
    symbols = [str(getattr(row, "name", row)) for row in _safe_sequence_call(mt5, "symbols_get")]
    local_orders = local_orders or []

    bot_positions = [item for item in positions if item.is_bot_item]
    foreign_positions = [item for item in positions if not item.is_bot_item]
    reasons: list[str] = []
    if not demo_only:
        reasons.append("account is not demo-only")
    if foreign_positions:
        reasons.append(f"warning_foreign_position count={len(foreign_positions)}")
    if len(positions) > max_open_positions:
        reasons.append(f"open positions {len(positions)} exceed limit {max_open_positions}")
    duplicate_reasons = _duplicate_reasons([*positions, *pending])
    reasons.extend(duplicate_reasons)
    reasons.extend(_local_consistency_reasons(bot_positions, pending, local_orders))
    status = "OK" if not reasons else "BLOCKED"
    return MT5ReconciliationReport(
        mt5_connected=account is not None,
        account_server=server,
        demo_only=demo_only,
        open_positions=len(positions),
        pending_orders=len(pending),
        bot_positions=len(bot_positions),
        foreign_positions=len(foreign_positions),
        duplicate_risk=bool(duplicate_reasons),
        reconciliation_status=status,
        reasons=reasons,
        positions=positions,
        pending=pending,
        history=history,
        symbols=symbols,
        magic_number=forex_scanner_magic_number(),
    )


def _load_history(mt5: object, *, history_days: int) -> list[MT5Exposure]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=history_days)
    rows: list[object] = []
    rows.extend(_safe_history_call(mt5, "history_orders_get", start, now))
    rows.extend(_safe_history_call(mt5, "history_deals_get", start, now))
    return [_row_to_exposure(row, "history") for row in rows]


def _duplicate_reasons(items: Iterable[MT5Exposure]) -> list[str]:
    reasons: list[str] = []
    by_symbol: dict[str, int] = {}
    by_setup_cycle: dict[tuple[str | None, str | None, str], int] = {}
    for item in items:
        if not item.is_bot_item:
            continue
        by_symbol[item.symbol] = by_symbol.get(item.symbol, 0) + 1
        key = (item.setup, item.cycle_id, item.symbol)
        by_setup_cycle[key] = by_setup_cycle.get(key, 0) + 1
    for symbol, count in by_symbol.items():
        if count > 1:
            reasons.append(f"duplicate bot exposure on symbol {symbol}")
    for (setup, cycle_id, symbol), count in by_setup_cycle.items():
        if setup and cycle_id and count > 1:
            reasons.append(f"duplicate bot setup/cycle on {symbol} setup={setup} cycle_id={cycle_id}")
    return reasons


def _local_consistency_reasons(
    bot_positions: list[MT5Exposure],
    pending: list[MT5Exposure],
    local_orders: list[ExecutionOrder],
) -> list[str]:
    if not local_orders:
        return []
    local_symbols = {order.request.symbol.replace("/", "") for order in local_orders if order.is_open or order.broker_order_id}
    reasons: list[str] = []
    for item in [*bot_positions, *[order for order in pending if order.is_bot_item]]:
        normalized = item.symbol.replace("/", "")
        if normalized not in local_symbols and item.ticket not in {str(order.broker_order_id or "") for order in local_orders}:
            reasons.append(f"bot MT5 exposure not found in local journal symbol={item.symbol} ticket={item.ticket}")
    return reasons


def _row_to_exposure(row: object, item_type: str) -> MT5Exposure:
    comment = str(getattr(row, "comment", "") or "")
    setup, cycle_id = _setup_cycle_from_comment(comment)
    return MT5Exposure(
        ticket=str(getattr(row, "ticket", getattr(row, "order", "")) or ""),
        symbol=str(getattr(row, "symbol", "") or ""),
        volume=float(getattr(row, "volume", getattr(row, "volume_initial", 0.0)) or 0.0),
        magic=_optional_int(getattr(row, "magic", None)),
        comment=comment,
        item_type=item_type,
        setup=setup,
        cycle_id=cycle_id,
    )


def _setup_cycle_from_comment(comment: str) -> tuple[str | None, str | None]:
    parts = comment.split("|")
    if len(parts) >= 5 and parts[0] == ORDER_COMMENT_PREFIX:
        return parts[3] or None, parts[4] or None
    return None, None


def _is_demo_account(mt5: object, account: object | None) -> bool:
    if account is None:
        return False
    demo_constant = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
    trade_mode = getattr(account, "trade_mode", None)
    if demo_constant is not None and trade_mode is not None:
        return int(trade_mode) == int(demo_constant)
    return "demo" in str(getattr(account, "server", "")).lower()


def _safe_call(source: object, name: str):
    fn = getattr(source, name, None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def _safe_sequence_call(source: object, name: str) -> list[object]:
    result = _safe_call(source, name)
    return list(result or [])


def _safe_history_call(source: object, name: str, start: datetime, end: datetime) -> list[object]:
    fn = getattr(source, name, None)
    if not callable(fn):
        return []
    try:
        return list(fn(start, end) or [])
    except Exception:
        return []


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
