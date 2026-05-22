"""Central MT5 symbol resolver tests."""

from __future__ import annotations

from types import SimpleNamespace

from app.data.mt5_symbol_resolver import MT5_SYMBOL_OVERRIDES_ENV, MT5SymbolResolver


class _Symbol:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMT5:
    TIMEFRAME_H1 = 60
    TIMEFRAME_M15 = 15
    TIMEFRAME_M5 = 5

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.selected: list[str] = []

    def symbols_get(self):
        return [_Symbol(name) for name in self.names]

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.selected.append(symbol)
        return enable and symbol in self.names

    def symbol_info(self, symbol: str):
        if symbol not in self.names:
            return None
        return SimpleNamespace(name=symbol, visible=True, trade_mode=4)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int):
        if symbol not in self.names:
            return []
        return [{"time": 1_700_000_000 + index * 60, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0} for index in range(count)]


def test_deriv_index_symbols_resolve_to_display_names() -> None:
    mt5 = _FakeMT5(["Wall Street 30", "US Tech 100", "Germany 40", "UK 100", "France 40"])
    resolver = MT5SymbolResolver(mt5)

    assert resolver.resolve("US30").mt5_symbol == "Wall Street 30"
    assert resolver.resolve("NAS100").mt5_symbol == "US Tech 100"
    assert resolver.resolve("GER40").mt5_symbol == "Germany 40"
    assert resolver.resolve("UK100").mt5_symbol == "UK 100"
    assert resolver.resolve("FRA40").mt5_symbol == "France 40"


def test_deriv_commodity_and_metals_symbols_resolve_to_available_names() -> None:
    mt5 = _FakeMT5(["US Oil", "UK Brent Oil", "XAUUSD", "XAGUSD"])
    resolver = MT5SymbolResolver(mt5)

    assert resolver.resolve("WTI/OIL").mt5_symbol == "US Oil"
    assert resolver.resolve("BRENT/OIL").mt5_symbol == "UK Brent Oil"
    assert resolver.resolve("XAU/USD").mt5_symbol == "XAUUSD"
    assert resolver.resolve("XAG/USD").mt5_symbol == "XAGUSD"


def test_unknown_symbol_is_skipped_cleanly() -> None:
    result = MT5SymbolResolver(_FakeMT5(["EURUSD"])).resolve("UNKNOWN")

    assert result.ok is False
    assert result.status == "ERROR"
    assert result.reason == "symbol_unavailable"
    assert result.mt5_symbol is None


def test_cli_override_prevents_retrying_old_logical_symbol(monkeypatch) -> None:
    mt5 = _FakeMT5(["WTI", "US Oil"])
    monkeypatch.setenv(MT5_SYMBOL_OVERRIDES_ENV, '{"WTI/OIL": "US Oil"}')

    result = MT5SymbolResolver(mt5).resolve("WTI/OIL")

    assert result.mt5_symbol == "US Oil"
    assert "WTI" not in mt5.selected
