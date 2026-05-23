"""MT5 demo reconciliation tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.brokers.mt5_reconciliation import (
    DEFAULT_FOREX_SCANNER_MAGIC_NUMBER,
    build_standard_order_comment,
    forex_scanner_magic_number,
    reconcile_mt5_demo,
)
from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.models import ExecutionOrder, OrderRequest, OrderStatus


def test_magic_number_defaults_to_required_value(monkeypatch) -> None:
    monkeypatch.delenv("FOREX_SCANNER_MAGIC_NUMBER", raising=False)

    assert forex_scanner_magic_number() == 260522
    assert DEFAULT_FOREX_SCANNER_MAGIC_NUMBER == 260522


def test_standard_order_comment_contains_bot_metadata() -> None:
    comment = build_standard_order_comment(
        asset_class="forex",
        symbol="EUR/USD",
        setup="ema50_pullback",
        cycle_id="cycle-123",
    )

    assert comment.startswith("ForexSupervisor|forex|EUR/USD|ema50_pullback|cycle-123"[:31])
    assert len(comment) <= 31


def test_reconciliation_reports_ok_when_account_is_demo_and_empty() -> None:
    report = reconcile_mt5_demo(_FakeMT5())

    assert report.mt5_connected
    assert report.account_server == "Deriv-Demo"
    assert report.demo_only is True
    assert report.open_positions == 0
    assert report.pending_orders == 0
    assert report.reconciliation_status == "OK"
    assert not report.block_new_orders


def test_reconciliation_flags_foreign_position_and_blocks_new_orders() -> None:
    fake = _FakeMT5(positions=[_Row(ticket=1, symbol="EURUSD", magic=999, comment="manual")])

    report = reconcile_mt5_demo(fake)

    assert report.foreign_positions == 1
    assert report.reconciliation_status == "BLOCKED"
    assert report.block_new_orders
    assert "warning_foreign_position count=1" in report.reasons


def test_reconciliation_flags_duplicate_bot_symbol_and_cycle() -> None:
    comment = build_standard_order_comment(asset_class="fx", symbol="EU", setup="ema", cycle_id="c1")
    fake = _FakeMT5(
        positions=[
            _Row(ticket=1, symbol="EURUSD", magic=260522, comment=comment),
            _Row(ticket=2, symbol="EURUSD", magic=260522, comment=comment),
        ]
    )

    report = reconcile_mt5_demo(fake)

    assert report.bot_positions == 2
    assert report.duplicate_risk
    assert any("duplicate bot exposure on symbol EURUSD" in reason for reason in report.reasons)
    assert any("duplicate bot setup/cycle" in reason for reason in report.reasons)


def test_reconciliation_checks_local_journal_consistency() -> None:
    comment = build_standard_order_comment(asset_class="forex", symbol="GBP/USD", setup="ema50_pullback", cycle_id="cycle-1")
    fake = _FakeMT5(positions=[_Row(ticket=10, symbol="GBPUSD", magic=260522, comment=comment)])

    report = reconcile_mt5_demo(fake, local_orders=[_local_order("EUR/USD")])

    assert report.reconciliation_status == "BLOCKED"
    assert any("not found in local journal" in reason for reason in report.reasons)


def test_reconciliation_reads_pending_orders_history_and_symbols() -> None:
    comment = build_standard_order_comment(asset_class="forex", symbol="EUR/USD", setup="ema50_pullback", cycle_id="cycle-1")
    fake = _FakeMT5(
        orders=[_Row(ticket=3, symbol="EURUSD", magic=260522, comment=comment)],
        history=[_Row(ticket=4, symbol="EURUSD", magic=260522, comment=comment)],
        symbols=[_Symbol("EURUSD"), _Symbol("GBPUSD")],
    )

    report = reconcile_mt5_demo(fake)

    assert report.pending_orders == 1
    assert len(report.history) == 2
    assert report.symbols == ["EURUSD", "GBPUSD"]
    assert fake.closed_positions == []
    assert fake.sent_orders == []


def _local_order(symbol: str) -> ExecutionOrder:
    request = OrderRequest(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=0.01,
        entry_price=1.1,
        stop_loss=1.095,
        take_profit=1.11,
    )
    return ExecutionOrder(
        order_id="local",
        request=request,
        status=OrderStatus.OPEN_TRADE,
        created_at=datetime.now(timezone.utc),
        initial_stop_loss=1.095,
    )


class _Account:
    server = "Deriv-Demo"
    trade_mode = 0


class _Row:
    def __init__(self, *, ticket: int, symbol: str, magic: int, comment: str, volume: float = 0.01) -> None:
        self.ticket = ticket
        self.order = ticket
        self.symbol = symbol
        self.magic = magic
        self.comment = comment
        self.volume = volume
        self.volume_initial = volume


class _Symbol:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self, *, positions=None, orders=None, history=None, symbols=None) -> None:
        self._positions = positions or []
        self._orders = orders or []
        self._history = history or []
        self._symbols = symbols or []
        self.closed_positions = []
        self.sent_orders = []

    def account_info(self):
        return _Account()

    def positions_get(self):
        return self._positions

    def orders_get(self):
        return self._orders

    def history_orders_get(self, _start, _end):
        return self._history

    def history_deals_get(self, _start, _end):
        return self._history

    def symbols_get(self):
        return self._symbols

    def order_send(self, payload):
        self.sent_orders.append(payload)
        return None

    def position_close(self, ticket):
        self.closed_positions.append(ticket)
