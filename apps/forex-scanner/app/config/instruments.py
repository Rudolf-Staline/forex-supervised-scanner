"""Multi-asset instrument configuration for local demo scanning."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.types import Timeframe


class AssetClass(str, Enum):
    """Supported local-demo asset classes."""

    FOREX = "forex"
    COMMODITIES = "commodities"
    INDICES = "indices"


@dataclass(frozen=True)
class InstrumentConfig:
    """Conservative instrument metadata for scanners and MT5 diagnostics."""

    logical_symbol: str
    mt5_symbol_candidates: list[str]
    asset_class: AssetClass
    enabled: bool = True
    default_timeframes: list[Timeframe] = field(default_factory=lambda: [Timeframe.H1, Timeframe.M15, Timeframe.M5])
    min_score: float = 75.0
    min_risk_reward: float = 1.5
    max_spread_atr: float = 0.22
    risk_percent: float = 0.25
    max_volume: float = 0.05
    allowed_sessions: list[str] = field(default_factory=lambda: ["london", "new_york", "new_york_overlap"])
    scan_only: bool = False
    notes: str = ""


FOREX_DEFAULT = {
    "min_score": 75.0,
    "min_risk_reward": 1.5,
    "max_spread_atr": 0.22,
    "risk_percent": 0.25,
    "max_volume": 0.05,
}
COMMODITIES_DEFAULT = {
    "min_score": 80.0,
    "min_risk_reward": 1.8,
    "max_spread_atr": 0.35,
    "risk_percent": 0.10,
    "max_volume": 0.02,
}
INDICES_DEFAULT = {
    "min_score": 82.0,
    "min_risk_reward": 2.0,
    "max_spread_atr": 0.30,
    "risk_percent": 0.10,
    "max_volume": 0.01,
}


def _forex(symbol: str) -> InstrumentConfig:
    mt5_symbol = symbol.replace("/", "")
    return InstrumentConfig(
        logical_symbol=symbol,
        mt5_symbol_candidates=[mt5_symbol],
        asset_class=AssetClass.FOREX,
        notes="Forex demo pair.",
        **FOREX_DEFAULT,
    )


INSTRUMENTS: dict[str, InstrumentConfig] = {
    symbol: _forex(symbol)
    for symbol in [
        "EUR/USD",
        "GBP/USD",
        "USD/CHF",
        "USD/JPY",
        "AUD/USD",
        "USD/CAD",
        "NZD/USD",
        "EUR/JPY",
        "GBP/JPY",
        "EUR/GBP",
    ]
}

INSTRUMENTS.update(
    {
        "XAU/USD": InstrumentConfig(
            logical_symbol="XAU/USD",
            mt5_symbol_candidates=["XAUUSD", "XAUUSD.", "Gold", "GOLD"],
            asset_class=AssetClass.COMMODITIES,
            scan_only=True,
            notes="Gold logical symbol; Deriv MT5 name must be discovered.",
            **COMMODITIES_DEFAULT,
        ),
        "XAG/USD": InstrumentConfig(
            logical_symbol="XAG/USD",
            mt5_symbol_candidates=["XAGUSD", "Silver", "SILVER"],
            asset_class=AssetClass.COMMODITIES,
            scan_only=True,
            notes="Silver logical symbol; Deriv MT5 name must be discovered.",
            **COMMODITIES_DEFAULT,
        ),
        "WTI/OIL": InstrumentConfig(
            logical_symbol="WTI/OIL",
            mt5_symbol_candidates=["US Oil", "USOil", "USOIL", "WTI", "WTIUSD", "Oil_WTI"],
            asset_class=AssetClass.COMMODITIES,
            scan_only=True,
            notes="WTI/oil logical symbol; broker symbol varies.",
            **COMMODITIES_DEFAULT,
        ),
        "BRENT/OIL": InstrumentConfig(
            logical_symbol="BRENT/OIL",
            mt5_symbol_candidates=["UK Brent Oil", "UKBrentOil", "UKOIL", "BRENT", "BRENTUSD", "Oil_Brent"],
            asset_class=AssetClass.COMMODITIES,
            scan_only=True,
            notes="Brent/oil logical symbol; broker symbol varies.",
            **COMMODITIES_DEFAULT,
        ),
        "US500": InstrumentConfig(
            logical_symbol="US500",
            mt5_symbol_candidates=["US500", "SPX500", "SP500", "US 500"],
            asset_class=AssetClass.INDICES,
            scan_only=True,
            notes="US500 logical index; broker symbol varies.",
            **INDICES_DEFAULT,
        ),
        "US30": InstrumentConfig(
            logical_symbol="US30",
            mt5_symbol_candidates=["Wall Street 30", "WallStreet30", "US30", "DJ30", "DOW"],
            asset_class=AssetClass.INDICES,
            scan_only=True,
            notes="US30 logical index; broker symbol varies.",
            **INDICES_DEFAULT,
        ),
        "NAS100": InstrumentConfig(
            logical_symbol="NAS100",
            mt5_symbol_candidates=["US Tech 100", "USTech100", "NAS100", "NASDAQ", "USTEC", "US100"],
            asset_class=AssetClass.INDICES,
            scan_only=True,
            notes="NASDAQ logical index; broker symbol varies.",
            **INDICES_DEFAULT,
        ),
        "GER40": InstrumentConfig(
            logical_symbol="GER40",
            mt5_symbol_candidates=["Germany 40", "Germany40", "GER40", "DAX", "DE40"],
            asset_class=AssetClass.INDICES,
            scan_only=True,
            notes="GER40 logical index; broker symbol varies.",
            **INDICES_DEFAULT,
        ),
        "UK100": InstrumentConfig(
            logical_symbol="UK100",
            mt5_symbol_candidates=["UK 100", "UK100", "FTSE", "FTSE100"],
            asset_class=AssetClass.INDICES,
            scan_only=True,
            notes="UK100 logical index; broker symbol varies.",
            **INDICES_DEFAULT,
        ),
        "FRA40": InstrumentConfig(
            logical_symbol="FRA40",
            mt5_symbol_candidates=["France 40", "France40", "FRA40", "CAC40"],
            asset_class=AssetClass.INDICES,
            scan_only=True,
            notes="FRA40 logical index; broker symbol varies.",
            **INDICES_DEFAULT,
        ),
    }
)


def instrument_for_symbol(symbol: str) -> InstrumentConfig:
    """Return configured instrument metadata, defaulting unknown symbols to conservative Forex rules."""

    normalized = symbol.strip().upper()
    return INSTRUMENTS.get(normalized) or _forex(normalized)


def symbols_for_asset_class(asset_class: AssetClass | str) -> list[str]:
    """Return enabled symbols for one asset class."""

    wanted = AssetClass(asset_class)
    return [symbol for symbol, config in INSTRUMENTS.items() if config.enabled and config.asset_class == wanted]


def enabled_instrument_symbols() -> list[str]:
    """Return every enabled logical instrument symbol."""

    return [symbol for symbol, config in INSTRUMENTS.items() if config.enabled]


def filter_symbols_by_asset_class(symbols: list[str], asset_class: AssetClass | str | None) -> list[str]:
    """Filter a symbol list by asset class; asset_class='all' keeps all symbols."""

    if asset_class is None or str(asset_class) == "all":
        return symbols
    wanted = AssetClass(asset_class)
    return [symbol for symbol in symbols if instrument_for_symbol(symbol).asset_class == wanted]


def resolve_mt5_symbol_from_candidates(logical_symbol: str, available_symbols: list[str] | None = None) -> str:
    """Resolve a logical symbol to a likely MT5 symbol using configured candidates."""

    config = instrument_for_symbol(logical_symbol)
    candidates = config.mt5_symbol_candidates or [logical_symbol.replace("/", "")]
    if not available_symbols:
        return candidates[0]
    canonical_available = {canonical_symbol(symbol): symbol for symbol in available_symbols}
    for candidate in candidates:
        canonical_candidate = canonical_symbol(candidate)
        if canonical_candidate in canonical_available:
            return canonical_available[canonical_candidate]
    for candidate in candidates:
        canonical_candidate = canonical_symbol(candidate)
        matches = [symbol for symbol in available_symbols if canonical_symbol(symbol).startswith(canonical_candidate)]
        if matches:
            return sorted(matches, key=lambda value: (len(value), value))[0]
    return candidates[0]


def canonical_symbol(symbol: str) -> str:
    """Normalize broker symbols for fuzzy matching."""

    return "".join(char for char in symbol.upper() if char.isalnum())
