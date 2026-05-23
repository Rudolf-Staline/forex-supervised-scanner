"""Strict MT5 demo execution gate tests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pytest

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.models import BrokerAccountState, ExecutionOrder, OrderRequest, OrderStatus
from app.safety.demo_execution_gate import DemoExecutionGateContext, evaluate_demo_execution_gate
from scripts._demo_bot_cli import add_cycle_arguments


def test_demo_execution_gate_allows_clean_forex_mt5_demo_order(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)

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

    assert result.allowed
    assert result.reasons == []
    assert result.details["asset_class"] == "forex"
    assert result.details["position_sizing_status"] == "available"


def test_demo_execution_gate_blocks_paper_broker_before_mt5_submission(settings) -> None:
    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(),
            broker_mode="paper",
            account=_account(),
            symbol_info=_SymbolInfo(),
            symbol_health_ok=True,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert "broker=mt5_demo must be explicitly requested before MT5 demo execution" in result.reasons


def test_demo_execution_gate_blocks_non_demo_account_server(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(),
            broker_mode="mt5_demo",
            account=_account(server="Deriv-Live", is_demo=False),
            symbol_info=_SymbolInfo(),
            symbol_health_ok=True,
            demo_execution_confirmed=True,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert any("account server must contain Demo" in reason for reason in result.reasons)
    assert "account is not marked as demo" in result.reasons


def test_demo_execution_gate_blocks_weak_or_unhealthy_signal(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(status="watchlist", final_score=60.0, spread=0.001, atr=0.002),
            broker_mode="mt5_demo",
            account=_account(),
            symbol_info=_SymbolInfo(),
            symbol_health_ok=False,
            demo_execution_confirmed=True,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert "symbol health check failed" in result.reasons
    assert "status watchlist is not executable by MT5 demo gate" in result.reasons
    assert any("score 60.0 below asset_class threshold 75.0" in reason for reason in result.reasons)
    assert any("spread_atr 0.500 above asset_class threshold 0.220" in reason for reason in result.reasons)


def test_demo_execution_gate_blocks_multi_asset_demo_trading_by_default(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    monkeypatch.setenv("ALLOW_MULTI_ASSET_DEMO_TRADING", "false")

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=_order(symbol="XAU/USD", entry=2300.0, stop=2290.0, target=2325.0, final_score=88.0, spread=0.5, atr=5.0),
            broker_mode="mt5_demo",
            account=_account(balance=10_000.0),
            symbol_info=_CommoditySymbolInfo(),
            symbol_health_ok=True,
            demo_execution_confirmed=True,
            now=_tradable_time(),
        )
    )

    assert not result.allowed
    assert "ALLOW_MULTI_ASSET_DEMO_TRADING is false for asset_class=commodities" in result.reasons


def test_demo_execution_gate_blocks_duplicate_same_symbol_setup(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    existing = _order(order_id="existing-order")
    candidate = _order(order_id="candidate-order")

    result = evaluate_demo_execution_gate(
        DemoExecutionGateContext(
            settings=settings,
            order=candidate,
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
    assert any("open paper position already exists" in reason for reason in result.reasons)
    assert "duplicate open trade for EUR/USD/ema50_pullback" in result.reasons


def test_demo_execution_gate_blocks_invalid_execution_mode(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "broker_live")

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
    assert "EXECUTION_MODE must be paper or demo, got broker_live" in result.reasons


def test_demo_execution_gate_blocks_if_allow_live_trading_true(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

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
    assert "ALLOW_LIVE_TRADING must be false, got true" in result.reasons


def test_cycle_cli_accepts_explain_execution_gate_argument() -> None:
    parser = argparse.ArgumentParser()
    add_cycle_arguments(parser)

    args = parser.parse_args(["--explain-execution-gate"])

    assert args.explain_execution_gate is True


def _set_mt5_demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("BROKER_MODE", "mt5_demo")
    monkeypatch.setenv("AUTO_BOT_ENABLED", "false")
    monkeypatch.setenv("MT5_DEMO_ONLY", "true")
    monkeypatch.setenv("MT5_LOGIN", "123456")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "Deriv-Demo")
    monkeypatch.setenv("ENABLE_DEMO_EXECUTION", "true")
    monkeypatch.setenv("MAX_DEMO_ORDER_VOLUME", "0.01")
    monkeypatch.setenv("MAX_DEMO_ORDERS_PER_DAY", "1")


def _tradable_time() -> datetime:
    return datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)


def _account(*, server: str = "Deriv-Demo", is_demo: bool = True, balance: float = 100_000.0) -> BrokerAccountState:
    return BrokerAccountState(
        broker="mt5",
        mode="mt5_demo",
        connected=True,
        can_trade=True,
        balance=balance,
        equity=balance,
        currency="USD",
        account_id="123456",
        server=server,
        is_demo=is_demo,
    )


def _order(
    *,
    order_id: str = "paper-order",
    symbol: str = "EUR/USD",
    status: str = "approved",
    final_score: float = 86.0,
    entry: float = 1.1,
    stop: float = 1.095,
    target: float = 1.11,
    spread: float = 0.00005,
    atr: float = 0.001,
) -> ExecutionOrder:
    request = OrderRequest(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=1.0,
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        signal_timestamp=_tradable_time(),
        source_status=status,
        final_score=final_score,
        provider="mt5",
        session="london",
        spread_at_signal=spread,
        atr_at_signal=atr,
    )
    return ExecutionOrder(
        order_id=order_id,
        request=request,
        status=OrderStatus.OPEN_TRADE,
        created_at=_tradable_time(),
        signal_timestamp=_tradable_time(),
        initial_stop_loss=stop,
    )


class _SymbolInfo:
    volume_min = 0.01
    volume_step = 0.01
    volume_max = 100.0
    trade_tick_size = 0.00001
    trade_tick_value = 1.0
    trade_contract_size = 100_000.0


class _CommoditySymbolInfo:
    volume_min = 0.01
    volume_step = 0.01
    volume_max = 1.0
    trade_tick_size = 0.01
    trade_tick_value = 1.0
    trade_contract_size = 100.0
