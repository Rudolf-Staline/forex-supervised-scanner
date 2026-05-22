"""Central MT5 symbol resolution for market data, diagnostics, and demo broker."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from app.config.instruments import canonical_symbol, instrument_for_symbol
from app.core.types import Timeframe

LOGGER = logging.getLogger(__name__)

MT5_SYMBOL_OVERRIDES_ENV = "FOREX_SCANNER_MT5_SYMBOL_OVERRIDES"
DEFAULT_RESOLUTION_TIMEFRAMES = [Timeframe.H1, Timeframe.M15, Timeframe.M5]


@dataclass(frozen=True)
class MT5ResolvedSymbol:
    """Resolution result for one logical symbol."""

    logical_symbol: str
    mt5_symbol: str | None
    status: str
    reason: str
    selected: bool = False
    tradable: bool = False
    bars_by_timeframe: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return true when the symbol can be used safely by MT5 data/broker code."""

        return self.status == "OK" and bool(self.mt5_symbol)


class MT5SymbolResolver:
    """Resolve logical symbols to available MT5 symbols with per-cycle caching."""

    def __init__(
        self,
        mt5: object,
        *,
        bars: int = 120,
        timeframes: list[Timeframe] | None = None,
        require_bars: bool = True,
    ) -> None:
        self.mt5 = mt5
        self.bars = bars
        self.timeframes = timeframes or list(DEFAULT_RESOLUTION_TIMEFRAMES)
        self.require_bars = require_bars
        self._cache: dict[tuple[str, bool], MT5ResolvedSymbol] = {}

    def resolve(self, logical_symbol: str, *, require_bars: bool | None = None) -> MT5ResolvedSymbol:
        """Return a selected MT5 symbol or a clean skip/error result."""

        require_bars = self.require_bars if require_bars is None else require_bars
        logical = logical_symbol.strip().upper()
        cache_key = (logical, bool(require_bars))
        if cache_key in self._cache:
            return self._cache[cache_key]

        override = mt5_symbol_override_for(logical)
        candidates = [override] if override else _candidate_symbols(logical)
        available = _available_symbols(self.mt5)
        ordered_matches = _ordered_available_matches(candidates, available) if available else list(dict.fromkeys(candidates))
        if not ordered_matches:
            result = MT5ResolvedSymbol(logical, None, "ERROR", "symbol_unavailable")
            self._cache[cache_key] = result
            _log_resolution(result)
            return result

        last_result: MT5ResolvedSymbol | None = None
        for mt5_symbol in ordered_matches:
            result = self._try_symbol(logical, mt5_symbol, require_bars=require_bars)
            last_result = result
            if result.ok:
                self._cache[cache_key] = result
                _log_resolution(result)
                return result
            if override:
                break

        result = last_result or MT5ResolvedSymbol(logical, None, "ERROR", "symbol_unavailable")
        self._cache[cache_key] = result
        _log_resolution(result)
        return result

    def _try_symbol(self, logical_symbol: str, mt5_symbol: str, *, require_bars: bool) -> MT5ResolvedSymbol:
        selected = _symbol_select(self.mt5, mt5_symbol)
        if not selected:
            return MT5ResolvedSymbol(logical_symbol, mt5_symbol, "ERROR", "symbol_select_failed", selected=False)

        info = _symbol_info(self.mt5, mt5_symbol)
        tradable = _is_tradable(getattr(info, "trade_mode", None))
        if not tradable:
            return MT5ResolvedSymbol(logical_symbol, mt5_symbol, "ERROR", "symbol_not_tradable", selected=True, tradable=False)

        bars_by_timeframe: dict[str, int] = {}
        if require_bars:
            for timeframe in self.timeframes:
                bars = _bars_for_timeframe(self.mt5, mt5_symbol, timeframe, self.bars)
                bars_by_timeframe[timeframe.value] = bars
            empty = [timeframe for timeframe, count in bars_by_timeframe.items() if count <= 0]
            if empty:
                return MT5ResolvedSymbol(
                    logical_symbol,
                    mt5_symbol,
                    "ERROR",
                    f"bars_0_timeframes={','.join(empty)}",
                    selected=True,
                    tradable=True,
                    bars_by_timeframe=bars_by_timeframe,
                )

        return MT5ResolvedSymbol(
            logical_symbol,
            mt5_symbol,
            "OK",
            "healthy",
            selected=True,
            tradable=True,
            bars_by_timeframe=bars_by_timeframe,
        )


def mt5_symbol_override_for(logical_symbol: str) -> str | None:
    """Return a CLI-provided resolved MT5 symbol, if present."""

    raw = os.getenv(MT5_SYMBOL_OVERRIDES_ENV, "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get(logical_symbol.strip().upper())
    return str(value).strip() if value else None


def set_mt5_symbol_overrides(mapping: dict[str, str]) -> None:
    """Publish per-cycle resolved symbols for provider reuse."""

    clean = {key.strip().upper(): value for key, value in mapping.items() if key.strip() and value.strip()}
    if clean:
        os.environ[MT5_SYMBOL_OVERRIDES_ENV] = json.dumps(clean, sort_keys=True)
    else:
        os.environ.pop(MT5_SYMBOL_OVERRIDES_ENV, None)


def _candidate_symbols(logical_symbol: str) -> list[str]:
    config = instrument_for_symbol(logical_symbol)
    candidates = list(config.mt5_symbol_candidates or [])
    fallback = canonical_symbol(logical_symbol)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _available_symbols(mt5: object) -> list[str]:
    symbols_get = getattr(mt5, "symbols_get", None)
    if not callable(symbols_get):
        return []
    try:
        rows = symbols_get() or []
    except Exception:
        return []
    return [str(getattr(row, "name", row)) for row in rows if str(getattr(row, "name", row)).strip()]


def _ordered_available_matches(candidates: list[str], available: list[str]) -> list[str]:
    canonical_available = {canonical_symbol(symbol): symbol for symbol in available}
    matches: list[str] = []
    for candidate in candidates:
        canonical_candidate = canonical_symbol(candidate)
        if not canonical_candidate:
            continue
        exact = canonical_available.get(canonical_candidate)
        if exact:
            matches.append(exact)
            continue
        fuzzy = [
            symbol
            for symbol in available
            if canonical_symbol(symbol).startswith(canonical_candidate)
            or canonical_candidate in canonical_symbol(symbol)
            or canonical_symbol(symbol) in canonical_candidate
        ]
        matches.extend(sorted(fuzzy, key=lambda value: (len(value), value)))
    return list(dict.fromkeys(matches))


def _symbol_select(mt5: object, mt5_symbol: str) -> bool:
    symbol_select = getattr(mt5, "symbol_select", None)
    if not callable(symbol_select):
        return True
    try:
        return bool(symbol_select(mt5_symbol, True))
    except Exception:
        return False


def _symbol_info(mt5: object, mt5_symbol: str) -> object | None:
    symbol_info = getattr(mt5, "symbol_info", None)
    if not callable(symbol_info):
        return None
    try:
        return symbol_info(mt5_symbol)
    except Exception:
        return None


def _bars_for_timeframe(mt5: object, mt5_symbol: str, timeframe: Timeframe, bars: int) -> int:
    attr = f"TIMEFRAME_{timeframe.value}"
    tf_constant = getattr(mt5, attr, None)
    if tf_constant is None:
        return 0
    copy_rates = getattr(mt5, "copy_rates_from_pos", None)
    if not callable(copy_rates):
        return 0
    try:
        rates = copy_rates(mt5_symbol, tf_constant, 0, bars)
    except Exception:
        return 0
    return 0 if rates is None else len(rates)


def _is_tradable(trade_mode: object) -> bool:
    if trade_mode is None:
        return True
    try:
        return int(trade_mode) != 0
    except (TypeError, ValueError):
        return True


def _log_resolution(result: MT5ResolvedSymbol) -> None:
    if result.ok:
        LOGGER.info(
            "symbol_resolved",
            extra={"logical": result.logical_symbol, "mt5_symbol": result.mt5_symbol, "bars_by_timeframe": result.bars_by_timeframe},
        )
    else:
        LOGGER.warning(
            "symbol_skipped",
            extra={"logical": result.logical_symbol, "mt5_symbol": result.mt5_symbol or "", "reason": result.reason},
        )
