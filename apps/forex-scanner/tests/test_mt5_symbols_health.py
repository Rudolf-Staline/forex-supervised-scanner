"""MT5 symbol health diagnostics tests."""

from __future__ import annotations

from types import SimpleNamespace

from app.core.types import Timeframe
from app.data.mt5_symbols_health import (
    SymbolHealth,
    TimeframeHealth,
    build_recommended_watchlist,
    diagnose_watchlist_symbols,
    summarize_symbol_health,
)


class FakeMT5:
    TIMEFRAME_H1 = 60
    TIMEFRAME_M15 = 15
    TIMEFRAME_M5 = 5

    def __init__(self, empty_symbols: set[str] | None = None, failing_symbols: set[str] | None = None) -> None:
        self.empty_symbols = empty_symbols or set()
        self.failing_symbols = failing_symbols or set()

    def initialize(self, **kwargs) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def symbol_select(self, symbol: str, selected: bool) -> bool:
        return symbol in {"EURUSD", "EURJPY"}

    def symbol_info(self, symbol: str):
        if symbol not in {"EURUSD", "EURJPY"}:
            return None
        return SimpleNamespace(
            visible=True,
            trade_mode=4,
            volume_min=0.01,
            volume_step=0.01,
            trade_stops_level=10,
            trade_freeze_level=0,
            spread=12,
            point=0.00001 if not symbol.endswith("JPY") else 0.001,
        )

    def symbol_info_tick(self, symbol: str):
        if symbol.endswith("JPY"):
            return SimpleNamespace(bid=164.10, ask=164.13)
        return SimpleNamespace(bid=1.0800, ask=1.08012)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int):
        if symbol in self.failing_symbols:
            raise RuntimeError("Terminal: Call failed")
        if symbol in self.empty_symbols:
            return []
        base = 164.0 if symbol.endswith("JPY") else 1.08
        return [
            {
                "time": 1_700_000_000 + index * 60,
                "open": base + index * 0.0001,
                "high": base + index * 0.0001 + 0.001,
                "low": base + index * 0.0001 - 0.001,
                "close": base + index * 0.0001 + 0.0002,
                "tick_volume": 100,
                "spread": 12,
                "real_volume": 0,
            }
            for index in range(count)
        ]

    def last_error(self):
        return (1, "Success")


def test_symbol_health_marks_symbol_with_all_timeframes_as_ok(settings) -> None:
    results = diagnose_watchlist_symbols(["EUR/USD"], settings=settings, mt5=FakeMT5(), bars=200)

    assert results[0].status == "OK"
    assert results[0].healthy is True
    assert all(item.bars == 200 for item in results[0].timeframes)
    assert results[0].spread_atr is not None


def test_symbol_health_marks_bars_zero_as_unhealthy(settings) -> None:
    results = diagnose_watchlist_symbols(["EUR/JPY"], settings=settings, mt5=FakeMT5({"EURJPY"}), bars=200)

    assert results[0].status == "ERROR"
    assert results[0].reason.startswith("bars_0_timeframes=H1,M15,M5")
    assert results[0].healthy is False


def test_symbol_health_summary_recommends_only_healthy_symbols(settings) -> None:
    results = diagnose_watchlist_symbols(["EUR/USD", "EUR/JPY"], settings=settings, mt5=FakeMT5({"EURJPY"}), bars=200)

    summary = summarize_symbol_health(results)

    assert summary["healthy_symbols"] == ["EUR/USD"]
    assert summary["unhealthy_symbols"] == ["EUR/JPY"]
    assert summary["recommended_watchlist_for_demo"] == ["EUR/USD"]


def test_symbol_health_continues_when_one_symbol_call_fails(settings) -> None:
    results = diagnose_watchlist_symbols(["EUR/USD", "EUR/JPY"], settings=settings, mt5=FakeMT5(failing_symbols={"EURJPY"}), bars=200)

    assert [result.symbol for result in results] == ["EUR/USD", "EUR/JPY"]
    assert results[0].status == "OK"
    assert results[1].status == "ERROR"
    assert "Terminal: Call failed" in results[1].reason


def test_build_recommended_watchlist_excludes_unhealthy_and_expensive_symbols() -> None:
    results = [
        _health("EUR/USD", spread_atr=0.20),
        _health("USD/CHF", spread_atr=0.25),
        _health("AUD/USD", spread_atr=0.30),
        _health("EUR/GBP", spread_atr=1.20),
        _health("EUR/JPY", status="ERROR", reason="bars_0_timeframes=H1"),
    ]

    recommendation = build_recommended_watchlist(results)

    assert recommendation["recommended_watchlist_for_demo"] == ["EUR/USD", "USD/CHF", "AUD/USD"]
    assert "EUR/GBP" in recommendation["excluded_symbols"]
    assert recommendation["reason_by_symbol"]["EUR/JPY"] == "bars_0_timeframes=H1"
    assert "spread_atr_above_peer_threshold" in recommendation["reason_by_symbol"]["EUR/GBP"]


def _health(symbol: str, *, spread_atr: float | None = 0.2, status: str = "OK", reason: str = "healthy") -> SymbolHealth:
    return SymbolHealth(
        symbol=symbol,
        mt5_symbol=symbol.replace("/", ""),
        status=status,
        visible=status == "OK",
        selected=status == "OK",
        tradable=status == "OK",
        spread=0.0001,
        atr=None if spread_atr is None else 0.001,
        spread_atr=spread_atr,
        trade_mode=4,
        volume_min=0.01,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        last_error="(1, 'Success')",
        reason=reason,
        timeframes=[TimeframeHealth(timeframe=timeframe, bars=200 if status == "OK" else 0, last_candle="", error="") for timeframe in [Timeframe.H1, Timeframe.M15, Timeframe.M5]],
    )
