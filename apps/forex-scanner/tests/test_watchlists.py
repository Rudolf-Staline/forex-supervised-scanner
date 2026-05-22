"""Watchlist CLI helper tests."""

from __future__ import annotations

from app.config.watchlists import get_watchlist
from app.data.providers import to_mt5_symbol
from scripts._demo_bot_cli import normalize_symbols


def test_major_forex_watchlist_contains_demo_majors() -> None:
    assert get_watchlist("major_forex") == [
        "EUR/USD",
        "GBP/USD",
        "USD/CHF",
        "USD/JPY",
        "AUD/USD",
        "USD/CAD",
        "NZD/USD",
    ]


def test_all_forex_demo_watchlist_contains_crosses() -> None:
    symbols = get_watchlist("all_forex_demo")

    assert "EUR/JPY" in symbols
    assert "GBP/JPY" in symbols
    assert "EUR/GBP" in symbols


def test_deriv_demo_recommended_watchlist_exists() -> None:
    symbols = get_watchlist("deriv_demo_recommended")

    assert symbols
    assert all("/" in symbol for symbol in symbols)


def test_symbols_override_watchlist() -> None:
    assert normalize_symbols(["EUR/USD", "GBP/USD"], "all_forex_demo") == ["EUR/USD", "GBP/USD"]


def test_watchlist_used_when_symbols_absent() -> None:
    assert normalize_symbols(None, "jpy_pairs") == ["USD/JPY", "EUR/JPY", "GBP/JPY"]


def test_demo_watchlist_symbols_map_to_mt5_names() -> None:
    expected = {
        "EUR/USD": "EURUSD",
        "GBP/USD": "GBPUSD",
        "USD/CHF": "USDCHF",
        "USD/JPY": "USDJPY",
        "AUD/USD": "AUDUSD",
        "USD/CAD": "USDCAD",
        "NZD/USD": "NZDUSD",
        "EUR/JPY": "EURJPY",
        "GBP/JPY": "GBPJPY",
        "EUR/GBP": "EURGBP",
    }

    assert {symbol: to_mt5_symbol(symbol) for symbol in get_watchlist("all_forex_demo")} == expected
