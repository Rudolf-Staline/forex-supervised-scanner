"""MT5 symbol health diagnostics for demo watchlists."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from app.config.instruments import AssetClass, enabled_instrument_symbols, filter_symbols_by_asset_class, instrument_for_symbol, resolve_mt5_symbol_from_candidates, symbols_for_asset_class
from app.config.settings import AppSettings, load_settings
from app.config.watchlists import get_watchlist
from app.core.types import Timeframe
from app.data.mt5_symbol_resolver import MT5SymbolResolver
from app.data.providers import DataProviderError, initialize_mt5_terminal, mt5_last_error, mt5_rates_to_ohlcv, to_mt5_symbol
from app.data.validation import pip_size

HEALTH_TIMEFRAMES = [Timeframe.H1, Timeframe.M15, Timeframe.M5]
DEFAULT_HEALTH_BARS = 200
DEFAULT_SPREAD_ATR_PERCENTILE = 0.75


@dataclass(frozen=True)
class TimeframeHealth:
    """Health result for one MT5 symbol/timeframe pair."""

    timeframe: Timeframe
    bars: int
    last_candle: str
    error: str


@dataclass(frozen=True)
class SymbolHealth:
    """MT5 market-data and tradability diagnostics for one logical symbol."""

    symbol: str
    mt5_symbol: str
    asset_class: str
    status: str
    visible: bool
    selected: bool
    tradable: bool
    spread: float | None
    atr: float | None
    spread_atr: float | None
    trade_mode: object
    volume_min: float | None
    volume_step: float | None
    stops_level: object
    freeze_level: object
    last_error: str
    reason: str
    timeframes: list[TimeframeHealth]

    @property
    def healthy(self) -> bool:
        """Return true when the symbol has usable MT5 candles on all required timeframes."""

        return self.status == "OK"


def load_mt5_module() -> object:
    """Import MetaTrader5 or raise a diagnostic error."""

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise DataProviderError("MetaTrader5 Python package is not installed") from exc
    return mt5


def resolve_symbols(symbols: list[str] | None = None, watchlist: str | None = None) -> list[str]:
    """Resolve explicit symbols first, then a named watchlist."""

    if symbols:
        resolved: list[str] = []
        for raw in symbols:
            resolved.extend(symbol.strip().upper() for symbol in raw.split(",") if symbol.strip())
        return resolved
    if watchlist:
        return get_watchlist(watchlist)
    return get_watchlist("major_forex")


def resolve_symbols_for_asset_class(symbols: list[str] | None, watchlist: str | None, asset_class: str | None) -> list[str]:
    """Resolve symbols/watchlist and filter or default by asset class."""

    if symbols or watchlist:
        return filter_symbols_by_asset_class(resolve_symbols(symbols, watchlist), asset_class)
    if asset_class == "all":
        return enabled_instrument_symbols()
    if asset_class and asset_class != "all":
        return symbols_for_asset_class(AssetClass(asset_class))
    return resolve_symbols(None, None)


def diagnose_watchlist_symbols(
    symbols: Iterable[str],
    *,
    settings: AppSettings | None = None,
    mt5: object | None = None,
    bars: int = DEFAULT_HEALTH_BARS,
) -> list[SymbolHealth]:
    """Diagnose each symbol without placing any order."""

    settings = settings or load_settings()
    mt5_module = mt5 or load_mt5_module()
    initialize_mt5_terminal(mt5_module)
    results: list[SymbolHealth] = []
    try:
        for symbol in symbols:
            try:
                results.append(diagnose_symbol(symbol, settings=settings, mt5=mt5_module, bars=bars))
            except Exception as exc:
                results.append(_failed_symbol_health(symbol, mt5_module, str(exc)))
    finally:
        if mt5 is None:
            shutdown = getattr(mt5_module, "shutdown", None)
            if callable(shutdown):
                shutdown()
    return results


def diagnose_symbol(
    symbol: str,
    *,
    settings: AppSettings,
    mt5: object,
    bars: int = DEFAULT_HEALTH_BARS,
) -> SymbolHealth:
    """Inspect one MT5 symbol across H1, M15, and M5."""

    config = instrument_for_symbol(symbol)
    resolution = MT5SymbolResolver(mt5, bars=bars, require_bars=True).resolve(symbol, require_bars=True)
    mt5_symbol = resolution.mt5_symbol or to_mt5_symbol(symbol)
    selected = bool(mt5.symbol_select(mt5_symbol, True))
    info = mt5.symbol_info(mt5_symbol)
    visible = bool(getattr(info, "visible", False)) if info is not None else False
    trade_mode = getattr(info, "trade_mode", None)
    tradable = _is_tradable(trade_mode)
    volume_min = _float_or_none(getattr(info, "volume_min", None))
    volume_step = _float_or_none(getattr(info, "volume_step", None))
    stops_level = getattr(info, "trade_stops_level", getattr(info, "stops_level", None))
    freeze_level = getattr(info, "trade_freeze_level", getattr(info, "freeze_level", None))
    spread = _current_spread(symbol, mt5_symbol, mt5, info)
    timeframe_results = [_diagnose_timeframe(symbol, mt5_symbol, timeframe, mt5, bars) for timeframe in HEALTH_TIMEFRAMES]
    atr = _estimate_atr(symbol, mt5_symbol, mt5, bars=bars)
    spread_atr = None if spread is None or atr is None or atr <= 0 else round(spread / atr, 4)
    last_error = mt5_last_error(mt5)
    reason = _health_reason(selected, visible, tradable, timeframe_results)
    if not resolution.ok and resolution.reason in {"symbol_unavailable", "symbol_select_failed", "symbol_not_tradable"}:
        reason = resolution.reason
    return SymbolHealth(
        symbol=symbol,
        mt5_symbol=mt5_symbol,
        asset_class=config.asset_class.value,
        status="OK" if reason == "healthy" else "ERROR",
        visible=visible,
        selected=selected,
        tradable=tradable,
        spread=spread,
        atr=atr,
        spread_atr=spread_atr,
        trade_mode=trade_mode,
        volume_min=volume_min,
        volume_step=volume_step,
        stops_level=stops_level,
        freeze_level=freeze_level,
        last_error=last_error,
        reason=reason,
        timeframes=timeframe_results,
    )


def get_tradable_symbols_from_watchlist(watchlist: str | list[str]) -> list[str]:
    """Return only watchlist symbols that look usable in the current MT5 demo terminal."""

    symbols = get_watchlist(watchlist) if isinstance(watchlist, str) else list(watchlist)
    return [result.symbol for result in diagnose_watchlist_symbols(symbols) if result.healthy]


def split_healthy_symbols(symbols: list[str]) -> tuple[list[str], list[SymbolHealth]]:
    """Return healthy symbols and full health diagnostics for CLI filtering."""

    results = diagnose_watchlist_symbols(symbols)
    return [result.symbol for result in results if result.healthy], results


def export_symbol_health_csv(results: list[SymbolHealth], path: Path) -> Path:
    """Export a flattened MT5 symbol health report."""

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for result in results:
        row = _base_row(result)
        for timeframe in result.timeframes:
            row[f"{timeframe.timeframe.value}_bars"] = timeframe.bars
            row[f"{timeframe.timeframe.value}_last_candle"] = timeframe.last_candle
            row[f"{timeframe.timeframe.value}_error"] = timeframe.error
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def summarize_symbol_health(results: list[SymbolHealth]) -> dict[str, object]:
    """Return a compact terminal summary for demo watchlist selection."""

    healthy = [result.symbol for result in results if result.healthy]
    unhealthy = [result.symbol for result in results if not result.healthy]
    ranked = [result for result in results if result.spread_atr is not None]
    ranked.sort(key=lambda item: float(item.spread_atr or 0.0))
    return {
        "healthy_symbols": healthy,
        "unhealthy_symbols": unhealthy,
        "highest_spread_atr_symbols": [item.symbol for item in ranked[-3:]][::-1],
        "lowest_spread_atr_symbols": [item.symbol for item in ranked[:3]],
        "recommended_watchlist_for_demo": healthy,
    }


def build_recommended_watchlist(results: list[SymbolHealth]) -> dict[str, object]:
    """Build a conservative demo watchlist from observed MT5 symbol health."""

    healthy = [item for item in results if item.healthy]
    threshold = _spread_atr_peer_threshold(healthy)
    recommended: list[str] = []
    excluded: list[str] = []
    reason_by_symbol: dict[str, str] = {}
    for item in results:
        reason = _recommendation_reason(item, threshold)
        if reason == "recommended":
            recommended.append(item.symbol)
        else:
            excluded.append(item.symbol)
            reason_by_symbol[item.symbol] = reason
    clean = sorted([item for item in healthy if item.spread_atr is not None], key=lambda item: float(item.spread_atr or 0.0))
    expensive = sorted([item for item in results if item.spread_atr is not None], key=lambda item: float(item.spread_atr or 0.0), reverse=True)
    return {
        "healthy_symbols": [item.symbol for item in healthy],
        "excluded_symbols": excluded,
        "reason_by_symbol": reason_by_symbol,
        "recommended_watchlist_for_demo": recommended,
        "spread_atr_peer_threshold": threshold,
        "top_clean_symbols": [item.symbol for item in clean[:5]],
        "top_expensive_symbols": [item.symbol for item in expensive[:5]],
    }


def _diagnose_timeframe(symbol: str, mt5_symbol: str, timeframe: Timeframe, mt5: object, bars: int) -> TimeframeHealth:
    attr = f"TIMEFRAME_{timeframe.value}"
    tf_constant = getattr(mt5, attr)
    try:
        rates = mt5.copy_rates_from_pos(mt5_symbol, tf_constant, 0, bars)
    except Exception as exc:
        return TimeframeHealth(timeframe=timeframe, bars=0, last_candle="", error=f"{exc}; last_error={mt5_last_error(mt5)}")
    count = 0 if rates is None else len(rates)
    if rates is None or count == 0:
        return TimeframeHealth(timeframe=timeframe, bars=count, last_candle="", error=mt5_last_error(mt5))
    try:
        normalized = mt5_rates_to_ohlcv(symbol, rates, mt5_symbol=mt5_symbol, timeframe=timeframe)
        last_candle = normalized.tail(1).to_json(date_format="iso")
    except Exception as exc:
        return TimeframeHealth(timeframe=timeframe, bars=count, last_candle="", error=str(exc))
    return TimeframeHealth(timeframe=timeframe, bars=count, last_candle=last_candle, error="")


def _estimate_atr(symbol: str, mt5_symbol: str, mt5: object, *, bars: int) -> float | None:
    try:
        rates = mt5.copy_rates_from_pos(mt5_symbol, getattr(mt5, "TIMEFRAME_M15"), 0, max(30, bars))
    except Exception:
        return None
    if rates is None or len(rates) < 15:
        return None
    try:
        df = mt5_rates_to_ohlcv(symbol, rates, mt5_symbol=mt5_symbol, timeframe=Timeframe.M15)
    except Exception:
        return None
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.tail(14).mean()
    return None if pd.isna(atr) else float(atr)


def _current_spread(symbol: str, mt5_symbol: str, mt5: object, info: object | None) -> float | None:
    tick = mt5.symbol_info_tick(mt5_symbol) if hasattr(mt5, "symbol_info_tick") else None
    ask = _float_or_none(getattr(tick, "ask", None))
    bid = _float_or_none(getattr(tick, "bid", None))
    if ask is not None and bid is not None and ask > bid:
        return ask - bid
    raw_spread = _float_or_none(getattr(info, "spread", None))
    point = _float_or_none(getattr(info, "point", None)) or pip_size(symbol)
    if raw_spread is None:
        return None
    return raw_spread * point


def _health_reason(selected: bool, visible: bool, tradable: bool, timeframes: list[TimeframeHealth]) -> str:
    if not selected:
        return "symbol_select_failed"
    if not visible:
        return "symbol_not_visible"
    if not tradable:
        return "symbol_not_tradable"
    empty = [item.timeframe.value for item in timeframes if item.bars <= 0]
    if empty:
        errors = [item.error for item in timeframes if item.error]
        suffix = f" error={errors[0]}" if errors else ""
        return f"bars_0_timeframes={','.join(empty)}{suffix}"
    return "healthy"


def _spread_atr_peer_threshold(healthy: list[SymbolHealth]) -> float | None:
    values = sorted(float(item.spread_atr) for item in healthy if item.spread_atr is not None)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    index = round((len(values) - 1) * DEFAULT_SPREAD_ATR_PERCENTILE)
    return values[index]


def _recommendation_reason(item: SymbolHealth, spread_atr_threshold: float | None) -> str:
    if not item.healthy:
        return item.reason
    if item.spread_atr is None:
        return "missing_spread_atr"
    if spread_atr_threshold is not None and item.spread_atr > spread_atr_threshold:
        return f"spread_atr_above_peer_threshold {item.spread_atr:.4f}>{spread_atr_threshold:.4f}"
    return "recommended"


def _is_tradable(trade_mode: object) -> bool:
    if trade_mode is None:
        return True
    try:
        return int(trade_mode) != 0
    except (TypeError, ValueError):
        return True


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _base_row(result: SymbolHealth) -> dict[str, object]:
    return {
        "symbol": result.symbol,
        "mt5_symbol": result.mt5_symbol,
        "asset_class": result.asset_class,
        "status": result.status,
        "reason": result.reason,
        "visible": result.visible,
        "selected": result.selected,
        "tradable": result.tradable,
        "spread": result.spread,
        "atr": result.atr,
        "spread_atr": result.spread_atr,
        "trade_mode": result.trade_mode,
        "volume_min": result.volume_min,
        "volume_step": result.volume_step,
        "stops_level": result.stops_level,
        "freeze_level": result.freeze_level,
        "last_error": result.last_error,
    }


def _failed_symbol_health(symbol: str, mt5: object, reason: str) -> SymbolHealth:
    config = instrument_for_symbol(symbol)
    mt5_symbol = resolve_mt5_symbol_from_candidates(symbol)
    return SymbolHealth(
        symbol=symbol,
        mt5_symbol=mt5_symbol,
        asset_class=config.asset_class.value,
        status="ERROR",
        visible=False,
        selected=False,
        tradable=False,
        spread=None,
        atr=None,
        spread_atr=None,
        trade_mode=None,
        volume_min=None,
        volume_step=None,
        stops_level=None,
        freeze_level=None,
        last_error=mt5_last_error(mt5),
        reason=reason,
        timeframes=[TimeframeHealth(timeframe=timeframe, bars=0, last_candle="", error=reason) for timeframe in HEALTH_TIMEFRAMES],
    )
