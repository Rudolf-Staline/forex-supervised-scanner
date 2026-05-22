"""MT5 demo-only safety tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config.safety import DemoSafetyError, ensure_demo_safe_mode, ensure_mt5_demo_safe_mode
from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.broker import BrokerExecutionError
from app.execution.models import OrderRequest
from app.execution.mt5_demo_broker import MT5DemoBroker


def test_regular_demo_safe_mode_still_rejects_mt5_demo_broker_mode(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROKER_MODE", "mt5_demo")
    monkeypatch.setenv("MT5_DEMO_ONLY", "true")

    with pytest.raises(DemoSafetyError, match="BROKER_MODE must be paper"):
        ensure_demo_safe_mode(settings, context="strict paper")


def test_mt5_demo_safe_mode_accepts_explicit_demo_env(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)

    ensure_mt5_demo_safe_mode(settings, context="mt5 demo")


def test_mt5_demo_safe_mode_requires_demo_only_true(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    monkeypatch.setenv("MT5_DEMO_ONLY", "false")

    with pytest.raises(DemoSafetyError, match="MT5_DEMO_ONLY must be true"):
        ensure_mt5_demo_safe_mode(settings, context="mt5 demo")


def test_mt5_demo_safe_mode_requires_live_trading_disabled(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    with pytest.raises(DemoSafetyError, match="ALLOW_LIVE_TRADING must be false"):
        ensure_mt5_demo_safe_mode(settings, context="mt5 demo")


def test_mt5_demo_safe_mode_requires_deriv_demo_server(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    monkeypatch.setenv("MT5_SERVER", "FTMO-Demo")

    with pytest.raises(DemoSafetyError, match="MT5_SERVER must be Deriv-Demo"):
        ensure_mt5_demo_safe_mode(settings, context="mt5 demo")


def test_mt5_demo_broker_refuses_non_demo_account(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    fake = _FakeMT5(account=_Account(trade_mode=1, server="Deriv-Demo"))

    broker = MT5DemoBroker(settings, mt5_module=fake)

    with pytest.raises(BrokerExecutionError, match="not a demo account"):
        broker.connect()


def test_mt5_demo_broker_places_tiny_demo_order_without_storing_password(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    monkeypatch.setenv("MT5_PASSWORD", "super-secret-password")
    monkeypatch.setenv("RISK_PER_TRADE_PERCENT", "0.25")
    monkeypatch.setenv("MAX_VOLUME_PER_TRADE", "0.05")
    monkeypatch.setenv("POSITION_SIZING_MODE", "auto")
    fake = _FakeMT5(account=_Account())
    broker = MT5DemoBroker(settings, mt5_module=fake)
    broker.connect()

    order = broker.place_order(_request())

    assert order.broker_mode == "mt5_demo"
    assert order.execution_assumptions["live_money"] is False
    assert order.execution_assumptions["demo_only"] is True
    assert order.execution_assumptions["risk_percent"] == 0.25
    assert order.execution_assumptions["final_volume"] == 0.05
    assert order.request.quantity_units == 0.05
    assert order.broker_order_id == "123456"
    assert order.broker_acknowledgement["filling_mode"] == "FOK"
    assert "super-secret-password" not in str(order.broker_submission)
    assert "super-secret-password" not in str(order.execution_assumptions)
    assert fake.last_order_payload["volume"] == 0.05
    assert fake.last_order_payload["sl"] == 1.095
    assert fake.last_order_payload["tp"] == 1.11
    assert [payload["type_filling"] for payload in fake.order_payloads] == [fake.ORDER_FILLING_IOC, fake.ORDER_FILLING_FOK]


def _set_mt5_demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("BROKER_MODE", "mt5_demo")
    monkeypatch.setenv("AUTO_BOT_ENABLED", "false")
    monkeypatch.setenv("MT5_DEMO_ONLY", "true")
    monkeypatch.setenv("MT5_LOGIN", "123456")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "Deriv-Demo")


def _request() -> OrderRequest:
    return OrderRequest(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=1.0,
        entry_price=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        tp1=1.105,
        tp2=1.11,
        tp3=1.115,
        signal_timestamp=datetime.now(timezone.utc),
        source_status="approved",
        entry_rationale="fixture",
        regime_context="trending_up",
        final_score=86.0,
        provider="synthetic",
    )


class _Account:
    def __init__(self, *, trade_mode: int = 0, server: str = "Deriv-Demo") -> None:
        self.login = 123456
        self.server = server
        self.trade_mode = trade_mode
        self.trade_allowed = True
        self.balance = 100_000.0
        self.equity = 100_000.0
        self.margin_free = 99_000.0
        self.currency = "USD"


class _Symbol:
    name = "EURUSD"


class _Tick:
    ask = 1.1002
    bid = 1.1000


class _Result:
    def __init__(self, retcode: int, comment: str) -> None:
        self.retcode = retcode
        self.order = 123456
        self.comment = comment


class _SymbolInfo:
    filling_mode = 0
    trade_execution = 0
    volume_min = 0.01
    volume_step = 0.01
    volume_max = 100.0
    trade_tick_size = 0.00001
    trade_tick_value = 1.0
    trade_contract_size = 100_000.0


class _FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TRADE_ACTION_PENDING = 5
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 2
    ORDER_FILLING_RETURN = 3
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE = 10009

    def __init__(self, *, account: _Account) -> None:
        self.account = account
        self.last_initialize_kwargs = {}
        self.last_order_payload = {}
        self.order_payloads = []
        self.shutdown_called = False

    def initialize(self, **kwargs) -> bool:
        self.last_initialize_kwargs = kwargs
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def account_info(self) -> _Account:
        return self.account

    def symbols_get(self):
        return [_Symbol()]

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return symbol == "EURUSD" and enable

    def symbol_info_tick(self, symbol: str):
        return _Tick() if symbol == "EURUSD" else None

    def symbol_info(self, symbol: str):
        return _SymbolInfo() if symbol == "EURUSD" else None

    def order_send(self, payload):
        self.last_order_payload = payload
        self.order_payloads.append(payload)
        if payload["type_filling"] == self.ORDER_FILLING_IOC:
            return _Result(10030, "Unsupported filling mode")
        return _Result(10009, "Request executed")
