"""Market data provider abstraction and V1 provider implementations."""

from __future__ import annotations

import hashlib
import os
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import numpy as np
import pandas as pd

from app.config.settings import AppSettings, ProviderSettings
from app.config.instruments import canonical_symbol, resolve_mt5_symbol_from_candidates
from app.core.types import TIMEFRAME_MINUTES, TIMEFRAME_PANDAS_RULE, Timeframe
from app.data.validation import attach_data_quality, pip_size, validate_ohlcv, window_for_bars

LOGGER = logging.getLogger(__name__)

DemoScenario = Literal["trend_up_pullback", "breakout_candidate", "range_reversion"]
MT5_LOGIN_ENV = "MT5_LOGIN"
MT5_PASSWORD_ENV = "MT5_PASSWORD"
MT5_SERVER_ENV = "MT5_SERVER"
MT5_PATH_ENV = "MT5_PATH"
DEBUG_MARKET_DATA_ENV = "FOREX_SCANNER_DEBUG_MARKET_DATA"


class DataProviderError(RuntimeError):
    """Raised when a market data provider cannot serve the requested data."""


class MarketDataProvider(ABC):
    """Provider interface for historical Forex candles."""

    name: str

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """Return candles indexed by UTC timestamp with OHLCV and optional spread columns."""


class AutoFallbackProvider(MarketDataProvider):
    """Try MT5 first, Yahoo second, and synthetic only when explicitly allowed."""

    name = "auto"

    def __init__(
        self,
        primary: MarketDataProvider,
        secondary: MarketDataProvider,
        fallback: MarketDataProvider | None,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.fallback = fallback

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        errors: list[str] = []
        try:
            df = self.primary.get_ohlcv(symbol, timeframe, start, end)
            df.attrs["provider"] = self.primary.name
            return df
        except Exception as exc:
            errors.append(f"{self.primary.name}: {exc}")
            LOGGER.warning(
                "primary provider failed; trying secondary provider",
                extra={
                    "symbol": symbol,
                    "timeframe": timeframe.value,
                    "provider": self.primary.name,
                    "error": str(exc),
                },
            )

        try:
            df = self.secondary.get_ohlcv(symbol, timeframe, start, end)
            df.attrs["provider"] = self.secondary.name
            df.attrs["warning"] = f"{self.primary.name} data was unavailable; using Yahoo fallback."
            return df
        except Exception as exc:
            errors.append(f"{self.secondary.name}: {exc}")
            if self.fallback is None:
                raise DataProviderError("all real-data providers failed and synthetic fallback is disabled: " + " | ".join(errors)) from exc
            df = self.fallback.get_ohlcv(symbol, timeframe, start, end)
            df.attrs["provider"] = self.fallback.name
            df.attrs["warning"] = (
                "MT5 and Yahoo data were unavailable; using deterministic development candles. "
                "Do not treat fallback candles as broker-quality market data."
            )
            return df


class YahooFinanceProvider(MarketDataProvider):
    """Yahoo Finance FX historical data via yfinance."""

    name = "yahoo"

    _INTERVALS: dict[Timeframe, str] = {
        Timeframe.M1: "1m",
        Timeframe.M5: "5m",
        Timeframe.M15: "15m",
        Timeframe.H1: "60m",
        Timeframe.H4: "60m",
        Timeframe.D1: "1d",
    }

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise DataProviderError("yfinance is not installed") from exc

        request_end = end or datetime.now(timezone.utc)
        request_start = start or window_for_bars(timeframe, self.settings.max_bars, request_end).start
        ticker = _to_yahoo_symbol(symbol)
        interval = self._INTERVALS[timeframe]

        try:
            raw = yf.download(
                tickers=ticker,
                start=request_start,
                end=request_end + timedelta(minutes=TIMEFRAME_MINUTES[timeframe]),
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            raise DataProviderError(f"Yahoo Finance download failed for {symbol} {timeframe.value}: {exc}") from exc

        if raw.empty:
            raise DataProviderError(f"Yahoo Finance returned no data for {symbol} {timeframe.value}")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [str(column[0]).lower() for column in raw.columns]
        else:
            raw.columns = [str(column).lower() for column in raw.columns]

        volume = raw["volume"] if "volume" in raw.columns else pd.Series(0.0, index=raw.index)
        duplicate_bars = int(pd.to_datetime(raw.index, utc=True).duplicated().sum())
        df = pd.DataFrame(
            {
                "open": raw["open"],
                "high": raw["high"],
                "low": raw["low"],
                "close": raw["close"],
                "volume": volume.fillna(0.0),
                "spread": np.nan,
            },
            index=pd.to_datetime(raw.index, utc=True),
        )
        resampled = False
        if timeframe == Timeframe.H4:
            df = _resample_hourly_to_h4(df)
            resampled = True
        cleaned = validate_ohlcv(df, min_rows=120)
        cleaned = attach_data_quality(cleaned, timeframe=timeframe, end=request_end, duplicate_bars=duplicate_bars, resampled=resampled)
        cleaned.attrs["provider"] = self.name
        return cleaned


class SyntheticForexDataProvider(MarketDataProvider):
    """Deterministic development provider with realistic demo market scenarios."""

    name = "synthetic"

    _BASE_PRICE: dict[str, float] = {
        "EUR/USD": 1.0850,
        "GBP/USD": 1.2650,
        "USD/JPY": 151.20,
        "USD/CHF": 0.9050,
        "AUD/USD": 0.6550,
        "USD/CAD": 1.3580,
        "NZD/USD": 0.6030,
        "EUR/JPY": 164.30,
        "GBP/JPY": 191.50,
        "EUR/GBP": 0.8580,
        "EUR/CHF": 0.9820,
        "GBP/CHF": 1.1450,
        "AUD/JPY": 99.10,
        "CAD/JPY": 111.40,
        "CHF/JPY": 167.30,
        "EUR/CAD": 1.4720,
        "GBP/CAD": 1.7160,
        "AUD/CAD": 0.8900,
        "NZD/JPY": 91.20,
        "XAU/USD": 2350.0,
    }
    _DEMO_SCENARIOS: dict[str, DemoScenario] = {
        "EUR/USD": "trend_up_pullback",
        "GBP/USD": "breakout_candidate",
        "USD/CHF": "range_reversion",
    }

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        request_end = end or datetime.now(timezone.utc)
        request_start = start or window_for_bars(timeframe, self.settings.max_bars, request_end).start
        rule = TIMEFRAME_PANDAS_RULE[timeframe]
        index = pd.date_range(request_start, request_end, freq=rule, tz=timezone.utc)
        if len(index) < self.settings.max_bars:
            index = pd.date_range(end=request_end, periods=self.settings.max_bars, freq=rule, tz=timezone.utc)

        seed = _stable_seed(self.settings.synthetic_seed, symbol, timeframe.value)
        rng = np.random.default_rng(seed)
        base = self._BASE_PRICE.get(symbol.upper(), 1.0)
        pip = pip_size(symbol)
        minutes = TIMEFRAME_MINUTES[timeframe]
        volatility = pip * np.sqrt(max(minutes, 1)) * 2.6
        drift_direction = 1 if seed % 3 == 0 else -1 if seed % 3 == 1 else 0
        drift = drift_direction * pip * 0.035 * np.sqrt(max(minutes, 1))

        seasonal = np.sin(np.linspace(0.0, 5.5 * np.pi, len(index))) * volatility * 0.65
        shocks = rng.normal(loc=drift, scale=volatility, size=len(index))
        close = base + np.cumsum(shocks) + seasonal
        close = np.maximum(close, pip * 50.0)
        scenario = self._DEMO_SCENARIOS.get(symbol.upper())
        if scenario is not None:
            close = _demo_scenario_close(index=index, base=base, pip=pip, timeframe=timeframe, rng=rng, scenario=scenario)

        open_ = np.concatenate(([close[0]], close[:-1]))
        wick_location = pip * (1.8 * _timeframe_scenario_scale(timeframe)) if scenario is not None else volatility * 0.65
        wick = np.abs(rng.normal(loc=wick_location, scale=wick_location * 0.35, size=len(index)))
        high = np.maximum(open_, close) + wick
        low = np.minimum(open_, close) - wick
        low = np.maximum(low, pip * 10.0)
        volume = rng.integers(80, 900, size=len(index)).astype(float)
        spread_pips = np.clip(rng.normal(loc=1.2 if pip == 0.0001 else 1.6, scale=0.25, size=len(index)), 0.2, 4.0)

        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "spread": spread_pips * pip,
            },
            index=index,
        )
        cleaned = validate_ohlcv(df, min_rows=120)
        cleaned = attach_data_quality(cleaned, timeframe=timeframe, end=request_end)
        cleaned.attrs["provider"] = self.name
        cleaned.attrs["warning"] = (
            "Using deterministic development candles with demo scenarios for trend, breakout, and range conditions. "
            "Configure Yahoo or MT5 for market data before making trading decisions."
        )
        if scenario is not None:
            cleaned.attrs["scenario"] = scenario
        return cleaned


class MetaTrader5Provider(MarketDataProvider):
    """Optional local MetaTrader 5 provider when the terminal and Python package are installed."""

    name = "mt5"

    _TIMEFRAMES: dict[Timeframe, str] = {
        Timeframe.M1: "TIMEFRAME_M1",
        Timeframe.M5: "TIMEFRAME_M5",
        Timeframe.M15: "TIMEFRAME_M15",
        Timeframe.H1: "TIMEFRAME_H1",
        Timeframe.H4: "TIMEFRAME_H4",
        Timeframe.D1: "TIMEFRAME_D1",
    }

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            _log_mt5_market_data_status(
                symbol=symbol,
                mt5_symbol=to_mt5_symbol(symbol),
                timeframe=timeframe,
                bars=0,
                status="error",
                last_error="MetaTrader5 Python package is not installed",
            )
            raise DataProviderError("MetaTrader5 Python package is not installed") from exc

        mapped_symbol = _resolve_mt5_market_symbol(mt5, symbol)
        tf_constant = getattr(mt5, self._TIMEFRAMES[timeframe])
        request_end = end or datetime.now(timezone.utc)
        try:
            initialize_mt5_terminal(mt5)
            selected = bool(mt5.symbol_select(mapped_symbol, True))
            last_error = mt5_last_error(mt5)
            _log_mt5_debug(
                "MT5 market data symbol selection",
                {
                    "symbol": symbol,
                    "mt5_symbol": mapped_symbol,
                    "timeframe": timeframe.value,
                    "selected": selected,
                    "last_error": last_error,
                },
            )
            if not selected:
                _log_mt5_market_data_status(
                    symbol=symbol,
                    mt5_symbol=mapped_symbol,
                    timeframe=timeframe,
                    bars=0,
                    status="error",
                    last_error=last_error,
                )
                raise DataProviderError(
                    f"MT5 symbol_select failed for {symbol} -> {mapped_symbol} {timeframe.value}: last_error={last_error}"
                )

            rates = mt5.copy_rates_from_pos(mapped_symbol, tf_constant, 0, self.settings.max_bars)
            bars = 0 if rates is None else len(rates)
            last_error = mt5_last_error(mt5)
            if rates is None or bars == 0:
                _log_mt5_market_data_status(
                    symbol=symbol,
                    mt5_symbol=mapped_symbol,
                    timeframe=timeframe,
                    bars=bars,
                    status="error",
                    last_error=last_error,
                )
                raise DataProviderError(
                    f"MT5 returned no candles for {symbol} -> {mapped_symbol} {timeframe.value}: "
                    f"bars={bars} last_error={last_error}"
                )

            df = mt5_rates_to_ohlcv(symbol, rates, mt5_symbol=mapped_symbol, timeframe=timeframe)
            duplicate_bars = int(df.index.duplicated().sum())
            cleaned = validate_ohlcv(df, min_rows=120)
            cleaned = attach_data_quality(cleaned, timeframe=timeframe, end=request_end, duplicate_bars=duplicate_bars)
            cleaned.attrs["provider"] = self.name
            cleaned.attrs["mt5_symbol"] = mapped_symbol
            _log_mt5_market_data_status(
                symbol=symbol,
                mt5_symbol=mapped_symbol,
                timeframe=timeframe,
                bars=len(cleaned),
                status="ok",
                last_error=last_error,
            )
            return cleaned
        except DataProviderError:
            raise
        except Exception as exc:
            raise DataProviderError(
                f"MT5 provider failed for {symbol} -> {mapped_symbol} {timeframe.value}: "
                f"{exc}; last_error={mt5_last_error(mt5)}"
            ) from exc
        finally:
            shutdown = getattr(mt5, "shutdown", None)
            if callable(shutdown):
                shutdown()


def build_provider(settings: AppSettings) -> MarketDataProvider:
    """Create the configured data provider."""

    synthetic_allowed = settings.provider.environment != "production" or settings.provider.allow_synthetic_in_production
    synthetic = SyntheticForexDataProvider(settings.provider) if synthetic_allowed else None
    if settings.provider.name == "synthetic":
        if synthetic is None:
            raise DataProviderError("synthetic provider is disabled in production")
        return synthetic
    if settings.provider.name == "yahoo":
        return YahooFinanceProvider(settings.provider)
    if settings.provider.name == "mt5":
        return MetaTrader5Provider(settings.provider)
    fallback = synthetic if settings.provider.fallback_to_synthetic else None
    return AutoFallbackProvider(
        primary=MetaTrader5Provider(settings.provider),
        secondary=YahooFinanceProvider(settings.provider),
        fallback=fallback,
    )


def _to_yahoo_symbol(symbol: str) -> str:
    normalized = symbol.replace("/", "").upper()
    return f"{normalized}=X"


def to_mt5_symbol(symbol: str) -> str:
    """Map internal Forex symbols such as EUR/USD to broker symbols such as EURUSD."""

    return "".join(char for char in symbol.upper() if char.isalnum())


def _resolve_mt5_market_symbol(mt5: object, symbol: str) -> str:
    symbols_get = getattr(mt5, "symbols_get", None)
    if not callable(symbols_get):
        return resolve_mt5_symbol_from_candidates(symbol)
    try:
        available = [str(getattr(row, "name", row)) for row in (symbols_get() or []) if str(getattr(row, "name", row)).strip()]
    except Exception:
        available = []
    resolved = resolve_mt5_symbol_from_candidates(symbol, available)
    if resolved:
        return resolved
    return canonical_symbol(symbol)


def mt5_rates_to_ohlcv(
    symbol: str,
    rates: object,
    *,
    mt5_symbol: str | None = None,
    timeframe: Timeframe | None = None,
) -> pd.DataFrame:
    """Normalize MT5 copy_rates rows into the OHLCV frame expected by validate_ohlcv."""

    raw = pd.DataFrame(rates)
    if debug_market_data_enabled():
        _log_mt5_raw_rates(symbol, mt5_symbol or to_mt5_symbol(symbol), timeframe, raw)
    required = ["time", "open", "high", "low", "close"]
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise DataProviderError(f"MT5 rates are missing required columns: {', '.join(missing)}")
    timestamp = pd.to_datetime(raw["time"].to_numpy(), unit="s", utc=True)
    volume = raw["tick_volume"].to_numpy(dtype=float) if "tick_volume" in raw.columns else _optional_raw_column(raw, "real_volume", 0.0)
    spread = raw["spread"].to_numpy(dtype=float) * pip_size(symbol) if "spread" in raw.columns else np.full(len(raw), np.nan)
    normalized = pd.DataFrame(
        {
            "open": raw["open"].to_numpy(dtype=float),
            "high": raw["high"].to_numpy(dtype=float),
            "low": raw["low"].to_numpy(dtype=float),
            "close": raw["close"].to_numpy(dtype=float),
            "volume": volume,
            "spread": spread,
        },
        index=timestamp,
    )
    normalized.index.name = "timestamp"
    if debug_market_data_enabled():
        _log_mt5_debug(
            "MT5 normalized OHLCV",
            {
                "symbol": symbol,
                "mt5_symbol": mt5_symbol or to_mt5_symbol(symbol),
                "timeframe": timeframe.value if timeframe else "",
                "rows": len(normalized),
                "columns": ",".join(str(column) for column in normalized.columns),
                "nan_counts": str(normalized.isna().sum().to_dict()),
                "last_candle": normalized.tail(1).to_json(date_format="iso"),
            },
        )
    return normalized


def _optional_raw_column(raw: pd.DataFrame, column: str, default: float) -> np.ndarray:
    if column in raw.columns:
        return raw[column].to_numpy(dtype=float)
    return np.full(len(raw), default, dtype=float)


def _log_mt5_raw_rates(symbol: str, mt5_symbol: str, timeframe: Timeframe | None, raw: pd.DataFrame) -> None:
    _log_mt5_debug(
        "MT5 raw rates diagnostics",
        {
            "symbol": symbol,
            "mt5_symbol": mt5_symbol,
            "timeframe": timeframe.value if timeframe else "",
            "rows": len(raw),
            "columns": ",".join(str(column) for column in raw.columns),
            "head": raw.head(3).to_json(date_format="iso"),
            "tail": raw.tail(3).to_json(date_format="iso"),
            "dtypes": str({str(key): str(value) for key, value in raw.dtypes.to_dict().items()}),
            "nan_counts": str(raw.isna().sum().to_dict()),
        },
    )


def debug_market_data_enabled() -> bool:
    """Return true when heavy market-data diagnostics are explicitly requested."""

    return os.getenv(DEBUG_MARKET_DATA_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _log_mt5_market_data_status(
    *,
    symbol: str,
    mt5_symbol: str,
    timeframe: Timeframe,
    bars: int,
    status: Literal["ok", "error"],
    last_error: str,
) -> None:
    message = "MT5 market data OK" if status == "ok" else "MT5 market data error"
    level = LOGGER.info if status == "ok" else LOGGER.warning
    level(
        message,
        extra={
            "symbol": symbol,
            "mt5_symbol": mt5_symbol,
            "timeframe": timeframe.value,
            "bars": bars,
            "status": status,
            "last_error": last_error,
        },
    )


def _log_mt5_debug(message: str, extra: dict[str, object]) -> None:
    if debug_market_data_enabled():
        LOGGER.info(message, extra=extra)


def initialize_mt5_terminal(mt5: object, *, timeout_seconds: float = 10.0) -> None:
    """Initialize MT5 using local demo credentials when available."""

    initialize = getattr(mt5, "initialize")
    kwargs: dict[str, Any] = {"timeout": int(timeout_seconds * 1000)}
    login = os.getenv(MT5_LOGIN_ENV)
    password = os.getenv(MT5_PASSWORD_ENV)
    server = os.getenv(MT5_SERVER_ENV)
    path = os.getenv(MT5_PATH_ENV)
    if login and password and server:
        try:
            kwargs["login"] = int(str(login))
        except ValueError as exc:
            raise DataProviderError("MT5_LOGIN must be an integer account id") from exc
        kwargs["password"] = str(password)
        kwargs["server"] = str(server)
    if path:
        kwargs["path"] = path
    try:
        ok = bool(initialize(**kwargs))
    except TypeError:
        kwargs.pop("timeout", None)
        ok = bool(initialize(**kwargs))
    if not ok:
        raise DataProviderError(f"MetaTrader 5 initialize failed: last_error={mt5_last_error(mt5)}")


def mt5_last_error(mt5: object) -> str:
    """Return MT5 last_error as a safe string for logs and diagnostics."""

    last_error = getattr(mt5, "last_error", None)
    if not callable(last_error):
        return "unavailable"
    return str(last_error())


def _stable_seed(base_seed: int, symbol: str, timeframe: str) -> int:
    digest = hashlib.sha256(f"{base_seed}:{symbol}:{timeframe}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % (2**32 - 1)


def _demo_scenario_close(
    index: pd.DatetimeIndex,
    base: float,
    pip: float,
    timeframe: Timeframe,
    rng: np.random.Generator,
    scenario: DemoScenario,
) -> np.ndarray:
    """Create deterministic demo closes that resemble common technical contexts."""

    count = len(index)
    scale = _timeframe_scenario_scale(timeframe)
    if scenario == "trend_up_pullback":
        return _trend_pullback_close(count, base, pip, scale, timeframe, rng)
    if scenario == "breakout_candidate":
        return _breakout_candidate_close(count, base, pip, scale, timeframe, rng)
    return _range_reversion_close(count, base, pip, scale, timeframe, rng)


def _timeframe_scenario_scale(timeframe: Timeframe) -> float:
    minutes = TIMEFRAME_MINUTES[timeframe]
    return float(np.clip(np.sqrt(minutes / 15.0), 0.7, 3.0))


def _trend_pullback_close(
    count: int,
    base: float,
    pip: float,
    scale: float,
    timeframe: Timeframe,
    rng: np.random.Generator,
) -> np.ndarray:
    x_axis = np.linspace(0.0, 1.0, count)
    trend = pip * (100.0 * scale) * x_axis
    wave = pip * (5.5 * scale) * np.sin(np.linspace(0.0, 7.0 * np.pi, count))
    noise = rng.normal(0.0, pip * 0.8 * scale, count)
    close = base + trend + wave + noise
    if TIMEFRAME_MINUTES[timeframe] >= 60:
        segment = min(52, max(20, count // 10))
        final_target = base + pip * 118.0 * scale
        close[-segment:] = np.linspace(close[-segment], final_target, segment) + rng.normal(0.0, pip * 0.35 * scale, segment)
        return np.maximum(close, pip * 50.0)

    segment = min(48, max(18, count // 8))
    pullback_end = count - max(8, segment // 4)
    pullback_start = count - segment
    anchor = close[pullback_start - 1]
    pullback = np.linspace(0.0, pip * 18.0 * scale, pullback_end - pullback_start)
    recovery = np.linspace(-pip * 18.0 * scale, pip * 16.0 * scale, count - pullback_end)
    close[pullback_start:pullback_end] = anchor - pullback + rng.normal(0.0, pip * 0.5 * scale, pullback_end - pullback_start)
    close[pullback_end:] = anchor + recovery + rng.normal(0.0, pip * 0.45 * scale, count - pullback_end)
    return np.maximum(close, pip * 50.0)


def _breakout_candidate_close(
    count: int,
    base: float,
    pip: float,
    scale: float,
    timeframe: Timeframe,
    rng: np.random.Generator,
) -> np.ndarray:
    width = pip * 12.0 * scale
    center = base
    oscillation = width * np.sin(np.linspace(0.0, 10.0 * np.pi, count))
    noise = rng.normal(0.0, pip * 0.55 * scale, count)
    close = center + oscillation + noise
    if TIMEFRAME_MINUTES[timeframe] >= 60:
        segment = min(56, max(22, count // 9))
        resistance = center + width
        compression = resistance - pip * (2.2 + 0.55 * np.sin(np.linspace(0.0, 4.0 * np.pi, segment))) * scale
        close[-segment:] = compression + rng.normal(0.0, pip * 0.25 * scale, segment)
        return np.maximum(close, pip * 50.0)

    segment = min(36, max(14, count // 10))
    start = count - segment
    resistance = center + width
    compression = resistance - pip * (4.0 + 1.5 * np.sin(np.linspace(0.0, 3.0 * np.pi, segment))) * scale
    close[start:] = compression + rng.normal(0.0, pip * 0.35 * scale, segment)
    close[-2] = resistance - pip * 2.0 * scale
    close[-1] = resistance + pip * 14.0 * scale
    return np.maximum(close, pip * 50.0)


def _range_reversion_close(
    count: int,
    base: float,
    pip: float,
    scale: float,
    timeframe: Timeframe,
    rng: np.random.Generator,
) -> np.ndarray:
    half_width = pip * 26.0 * scale
    center = base
    close = center + half_width * np.sin(np.linspace(0.0, 9.0 * np.pi, count)) + rng.normal(0.0, pip * 0.55 * scale, count)
    if TIMEFRAME_MINUTES[timeframe] >= 60:
        return np.maximum(close, pip * 50.0)

    segment = min(42, max(18, count // 9))
    start = count - segment
    support = center - half_width
    approach_len = max(8, int(segment * 0.68))
    bounce_len = segment - approach_len
    approach = np.linspace(center - half_width * 0.20, support - pip * 9.0 * scale, approach_len)
    bounce = np.linspace(support - pip * 9.0 * scale, support - pip * 4.5 * scale, bounce_len)
    close[start : start + approach_len] = approach + rng.normal(0.0, pip * 0.35 * scale, approach_len)
    close[start + approach_len :] = bounce + rng.normal(0.0, pip * 0.3 * scale, bounce_len)
    return np.maximum(close, pip * 50.0)


def _resample_hourly_to_h4(df: pd.DataFrame) -> pd.DataFrame:
    resampled = df.resample("4h").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "spread": "mean",
        }
    )
    return resampled.dropna(subset=["open", "high", "low", "close"])
