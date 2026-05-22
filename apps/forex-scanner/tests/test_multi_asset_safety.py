"""Safety tests for scan-only commodities and indices."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.broker import BrokerExecutionError
from app.execution.models import OrderRequest
from app.execution.mt5_demo_broker import MT5DemoBroker
from app.risk.position_sizing import calculate_position_size
from scripts._demo_bot_cli import normalize_symbols


def test_cli_asset_class_filter_for_multi_asset_watchlist() -> None:
    assert normalize_symbols(None, "multi_asset_demo", "commodities") == ["XAU/USD", "XAG/USD", "WTI/OIL", "BRENT/OIL"]
    assert "NAS100" in normalize_symbols(None, "multi_asset_demo", "indices")


def test_mt5_demo_broker_blocks_non_forex_by_default(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mt5_demo_env(monkeypatch)
    fake = _FakeMT5()
    broker = MT5DemoBroker(settings, mt5_module=fake)
    broker.connect()

    with pytest.raises(BrokerExecutionError, match="ALLOW_MULTI_ASSET_DEMO_TRADING is false"):
        broker.place_order(_request("XAU/USD"))

    assert fake.order_payloads == []


def test_non_forex_position_sizing_requires_tick_value_and_tick_size() -> None:
    with pytest.raises(ValueError, match="position_sizing_unavailable"):
        calculate_position_size(
            balance=10_000,
            risk_percent=0.10,
            entry_price=2300.0,
            stop_loss=2290.0,
            symbol_info=_SymbolInfoMissingTickValue(),
            max_volume=0.02,
            require_tick_value=True,
        )


def _set_mt5_demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "false")
    monkeypatch.setenv("BROKER_MODE", "mt5_demo")
    monkeypatch.setenv("AUTO_BOT_ENABLED", "false")
    monkeypatch.setenv("MT5_DEMO_ONLY", "true")
    monkeypatch.setenv("MT5_LOGIN", "123456")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "Deriv-Demo")
    monkeypatch.setenv("ALLOW_MULTI_ASSET_DEMO_TRADING", "false")


def _request(symbol: str) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=1.0,
        entry_price=2300.0,
        stop_loss=2290.0,
        take_profit=2320.0,
        signal_timestamp=datetime.now(timezone.utc),
        source_status="approved",
        final_score=90.0,
        provider="mt5",
    )


class _Account:
    login = 123456
    server = "Deriv-Demo"
    trade_mode = 0
    trade_allowed = True
    balance = 10_000.0
    equity = 10_000.0
    margin_free = 9_000.0
    currency = "USD"


class _Symbol:
    name = "XAUUSD.d"


class _FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self) -> None:
        self.order_payloads = []

    def initialize(self, **kwargs) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def account_info(self):
        return _Account()

    def symbols_get(self):
        return [_Symbol()]


class _SymbolInfoMissingTickValue:
    volume_min = 0.01
    volume_step = 0.01
    volume_max = 1.0
    point = 0.01
    trade_contract_size = 100.0
