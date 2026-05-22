"""Instrument configuration tests for multi-asset demo scanning."""

from __future__ import annotations

from app.config.instruments import AssetClass, filter_symbols_by_asset_class, instrument_for_symbol, resolve_mt5_symbol_from_candidates
from app.config.watchlists import get_watchlist


def test_instrument_asset_classes_and_thresholds_are_conservative() -> None:
    forex = instrument_for_symbol("EUR/USD")
    gold = instrument_for_symbol("XAU/USD")
    index = instrument_for_symbol("NAS100")

    assert forex.asset_class == AssetClass.FOREX
    assert forex.min_score == 75
    assert gold.asset_class == AssetClass.COMMODITIES
    assert gold.scan_only is True
    assert gold.min_score == 80
    assert gold.risk_percent == 0.10
    assert index.asset_class == AssetClass.INDICES
    assert index.scan_only is True
    assert index.min_score == 82
    assert index.max_volume == 0.01


def test_multi_asset_watchlists_exist() -> None:
    assert "XAU/USD" in get_watchlist("commodities_demo")
    assert "NAS100" in get_watchlist("indices_demo")
    assert "XAU/USD" in get_watchlist("multi_asset_demo")
    assert "EUR/USD" in get_watchlist("multi_asset_demo")


def test_asset_class_filtering_keeps_only_requested_symbols() -> None:
    symbols = ["EUR/USD", "XAU/USD", "NAS100"]

    assert filter_symbols_by_asset_class(symbols, "forex") == ["EUR/USD"]
    assert filter_symbols_by_asset_class(symbols, "commodities") == ["XAU/USD"]
    assert filter_symbols_by_asset_class(symbols, "indices") == ["NAS100"]
    assert filter_symbols_by_asset_class(symbols, "all") == symbols


def test_mt5_symbol_resolution_uses_candidates_without_assuming_exact_names() -> None:
    available = ["frxEURUSD", "XAUUSD.d", "WallStreet30", "Volatility 75 Index"]

    assert resolve_mt5_symbol_from_candidates("XAU/USD", available) == "XAUUSD.d"
    assert resolve_mt5_symbol_from_candidates("US30", available) == "WallStreet30"
