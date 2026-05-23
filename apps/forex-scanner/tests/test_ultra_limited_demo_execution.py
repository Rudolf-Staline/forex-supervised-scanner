"""Ultra-limited MT5 demo execution safety tests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pytest

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.broker import BrokerExecutionError
from app.execution.models import BrokerAccountState, ExecutionOrder, OrderRequest, OrderStatus
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.safety.demo_execution_gate import DemoExecutionGateContext, evaluate_demo_execution_gate
from scripts._demo_bot_cli import add_cycle_arguments


def test_gate_blocks_when_enable_demo_execution_is_false(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_env(monkeypatch, enable_demo_execution="false")

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(),
            broker_mode="mt5_demo",
            account=_account(),
            symbol_info=_SymbolInfo(),
            symbol_health_ok=True,
            demo_execution_confirmed=True,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert "ENABLE_DEMO_EXECUTION must be true for ultra-limited MT5 demo execution" in result.reasons


def test_gate_blocks_without_cli_confirmation(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_env(monkeypatch, enable_demo_execution="true")

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(),
            broker_mode="mt5_demo",
            account=_account(),
            symbol_info=_SymbolInfo(),
            symbol_health_ok=True,
            demo_execution_confirmed=False,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert "--demo-execution-confirmed is required before any MT5 demo order" in result.reasons


def test_gate_blocks_volume_above_hard_limit(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_env(monkeypatch, enable_demo_execution="true")
    monkeypatch.setenv("MAX_DEMO_ORDER_VOLUME", "0.005")

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(),
            broker_mode="mt5_demo",
            account=_account(),
            symbol_info=_SymbolInfo(),
            symbol_health_ok=True,
            demo_execution_confirmed=True,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert any("position_size" in reason or "volume" in reason for reason in result.reasons)


def test_gate_blocks_after_daily_demo_order_limit(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_env(monkeypatch, enable_demo_execution="true")
    existing = _order(order_id="broker-order").model_copy(update={"broker_mode": "mt5_demo", "broker_name": "mt5", "broker_order_id": "123"})

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(order_id="candidate", symbol="GBP/USD"),
            broker_mode="mt5_demo",
            existing_orders=[existing],
            account=_account(),
            symbol_info=_SymbolInfo(),
            symbol_health_ok=True,
            demo_execution_confirmed=True,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert "MAX_DEMO_ORDERS_PER_DAY reached: 1/1" in result.reasons


def test_cli_accepts_demo_execution_confirmed_flag() -> None:
    parser = argparse.ArgumentParser()
    add_cycle_arguments(parser)

    args = parser.parse_args(["--demo-execution-confirmed"])

    assert args.demo_execution_confirmed is True


def test_mt5_demo_broker_refuses_place_order_without_confirmation(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_env(monkeypatch, enable_demo_execution="true")
    broker = MT5DemoBroker(settings, mt5_module=_FakeMT5())
    broker.connect()

    with pytest.raises(BrokerExecutionError, match="demo-execution-confirmed"):
        broker.place_order(_request(), gate_passed=True)


def test_mt5_demo_broker_refuses_place_order_without_gate_pass(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_env(monkeypatch, enable_demo_execution="true")
    broker = MT5DemoBroker(settings, mt5_module=_FakeMT5(), demo_execution_confirmed=True)
    broker.connect()

    with pytest.raises(BrokerExecutionError, match="demo_execution_gate must pass"):
        broker.place_order(_request())


def _set_mt5_env(monkeypatch: pytest.MonkeyPatch, *, enable_demo_execution: str) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("BROKER_MODE", "mt5_demo")
    monkeypatch.setenv("AUTO_BOT_ENABLED", "false")
    monkeypatch.setenv("MT5_DEMO_ONLY", "true")
    monkeypatch.setenv("MT5_LOGIN", "123456")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "Deriv-Demo")
    monkeypatch.setenv("ENABLE_DEMO_EXECUTION", enable_demo_execution)
    monkeypatch.setenv("MAX_DEMO_ORDER_VOLUME", "0.01")
    monkeypatch.setenv("MAX_DEMO_ORDERS_PER_DAY", "1")


def _tradable_time() -> datetime:
    return datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)


def _account() -> BrokerAccountState:
    return BrokerAccountState(
        broker="mt5",
        mode="mt5_demo",
        connected=True,
        can_trade=True,
        balance=1_000.0,
        equity=1_000.0,
        currency="USD",
        account_id="123456",
        server="Deriv-Demo",
        is_demo=True,
    )


def _request(symbol: str = "EUR/USD") -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=1.0,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        signal_timestamp=_tradable_time(),
        source_status="approved",
        final_score=86.0,
        provider="mt5",
        session="london",
        spread_at_signal=0.00005,
        atr_at_signal=0.001,
    )


def _order(order_id: str = "paper-order", symbol: str = "EUR/USD") -> ExecutionOrder:
    return ExecutionOrder(
        order_id=order_id,
        request=_request(symbol),
        status=OrderStatus.OPEN_TRADE,
        created_at=_tradable_time(),
        signal_timestamp=_tradable_time(),
        initial_stop_loss=1.0950,
    )


class _SymbolInfo:
    volume_min = 0.01
    volume_step = 0.01
    volume_max = 100.0
    trade_tick_size = 0.00001
    trade_tick_value = 1.0
    trade_contract_size = 100_000.0


class _Account:
    login = 123456
    server = "Deriv-Demo"
    trade_mode = 0
    trade_allowed = True
    balance = 1_000.0
    equity = 1_000.0
    margin_free = 900.0
    currency = "USD"


class _FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0

    def initialize(self, **_kwargs) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def account_info(self):
        return _Account()
