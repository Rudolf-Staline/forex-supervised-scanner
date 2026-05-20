"""MT5 demo symbol mapper tests."""

from __future__ import annotations

import pytest

from app.execution.broker import BrokerExecutionError
from app.execution.mt5_demo_broker import MT5SymbolMapper


class _Symbol:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMT5:
    def __init__(self, names: list[str], *, selectable: bool = True) -> None:
        self.names = names
        self.selectable = selectable
        self.selected: list[str] = []

    def symbols_get(self):
        return [_Symbol(name) for name in self.names]

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.selected.append(symbol)
        return self.selectable and enable


def test_symbol_mapper_prefers_exact_match() -> None:
    fake = _FakeMT5(["EURUSD.pro", "EURUSD", "GBPUSD"])

    mapped = MT5SymbolMapper(fake).map_symbol("EUR/USD")

    assert mapped == "EURUSD"
    assert fake.selected == ["EURUSD"]


def test_symbol_mapper_accepts_broker_suffix() -> None:
    fake = _FakeMT5(["EURUSD.pro", "GBPUSD.a"])

    mapped = MT5SymbolMapper(fake).map_symbol("GBP/USD")

    assert mapped == "GBPUSD.a"


def test_symbol_mapper_refuses_unknown_symbol() -> None:
    fake = _FakeMT5(["EURUSD", "GBPUSD"])

    with pytest.raises(BrokerExecutionError, match="unknown MT5 symbol mapping"):
        MT5SymbolMapper(fake).map_symbol("USD/CHF")


def test_symbol_mapper_refuses_unselectable_symbol() -> None:
    fake = _FakeMT5(["EURUSD"], selectable=False)

    with pytest.raises(BrokerExecutionError, match="could not be selected"):
        MT5SymbolMapper(fake).map_symbol("EUR/USD")
