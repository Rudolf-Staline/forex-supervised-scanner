"""OHLCV data validation and timeframe helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.core.types import DataQualityDiagnostic, TIMEFRAME_MINUTES, TIMEFRAME_PANDAS_RULE, Timeframe

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
OPTIONAL_COLUMNS = ["spread"]


class DataValidationError(ValueError):
    """Raised when market data cannot be analyzed safely."""


@dataclass(frozen=True)
class DataWindow:
    """Start and end timestamps for a requested bar window."""

    start: datetime
    end: datetime


def validate_ohlcv(df: pd.DataFrame, min_rows: int = 120) -> pd.DataFrame:
    """Return a cleaned OHLCV frame or raise a precise validation error."""

    if df.empty:
        raise DataValidationError("provider returned no candles")
    missing = [column for column in OHLCV_COLUMNS if column not in df.columns]
    if missing:
        raise DataValidationError(f"missing OHLCV columns: {', '.join(missing)}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise DataValidationError("OHLCV data must use a DatetimeIndex")

    cleaned = df.copy()
    cleaned.index = pd.to_datetime(cleaned.index, utc=True)
    cleaned = cleaned.sort_index()
    cleaned = cleaned[~cleaned.index.duplicated(keep="last")]
    numeric_columns = [column for column in OHLCV_COLUMNS + OPTIONAL_COLUMNS if column in cleaned.columns]
    for column in numeric_columns:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    cleaned = cleaned.dropna(subset=OHLCV_COLUMNS)
    cleaned = cleaned[(cleaned["high"] >= cleaned["low"]) & (cleaned["close"] > 0)]
    cleaned = cleaned[(cleaned["open"] > 0) & (cleaned["high"] > 0) & (cleaned["low"] > 0)]
    if "spread" not in cleaned.columns:
        cleaned["spread"] = np.nan

    if len(cleaned) < min_rows:
        raise DataValidationError(f"not enough clean candles: {len(cleaned)} rows, need at least {min_rows}")
    return cleaned


def assess_data_quality(
    df: pd.DataFrame,
    timeframe: Timeframe,
    end: datetime | None = None,
    duplicate_bars: int = 0,
    resampled: bool = False,
) -> DataQualityDiagnostic:
    """Score operational data quality for provider diagnostics."""

    warnings: list[str] = []
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return DataQualityDiagnostic(
            score=0.0,
            missing_bars=0,
            stale_minutes=None,
            spread_available=False,
            resampled=resampled,
            duplicate_bars=duplicate_bars,
            warnings=["data quality cannot be assessed because the frame is empty or not time-indexed"],
        )

    expected_minutes = TIMEFRAME_MINUTES[timeframe]
    index = pd.to_datetime(df.index, utc=True).sort_values()
    deltas = index.to_series().diff().dropna().dt.total_seconds() / 60.0
    missing_bars = int(
        sum(
            max(0, round(delta / expected_minutes) - 1)
            for delta in deltas.tail(180)
            if delta > expected_minutes * 1.5
        )
    )

    last_timestamp = index[-1].to_pydatetime()
    reference_end = end.astimezone(timezone.utc) if end is not None else datetime.now(timezone.utc)
    stale_minutes = max(0.0, (reference_end - last_timestamp).total_seconds() / 60.0)
    stale_limit = expected_minutes * 4.0 if expected_minutes < 1440 else expected_minutes * 2.5
    spread_available = "spread" in df.columns and not df["spread"].dropna().empty

    score = 100.0
    if missing_bars:
        score -= min(35.0, missing_bars * 0.6)
        warnings.append(f"{missing_bars} missing or irregular recent bars")
    if stale_minutes > stale_limit:
        score -= min(30.0, (stale_minutes / max(expected_minutes, 1)) * 3.0)
        warnings.append(f"latest candle is stale by about {stale_minutes:.0f} minutes")
    if not spread_available:
        score -= 10.0
        warnings.append("spread is absent from provider data")
    if resampled:
        score -= 6.0
        warnings.append("candles were resampled from a lower timeframe")
    if duplicate_bars:
        score -= min(15.0, duplicate_bars * 1.5)
        warnings.append(f"{duplicate_bars} duplicate timestamp bars were removed")

    return DataQualityDiagnostic(
        score=round(max(0.0, min(100.0, score)), 2),
        missing_bars=missing_bars,
        stale_minutes=round(stale_minutes, 2),
        spread_available=spread_available,
        resampled=resampled,
        duplicate_bars=duplicate_bars,
        warnings=warnings,
    )


def attach_data_quality(
    df: pd.DataFrame,
    timeframe: Timeframe,
    end: datetime | None = None,
    duplicate_bars: int = 0,
    resampled: bool = False,
) -> pd.DataFrame:
    """Attach a data-quality diagnostic to a cleaned OHLCV frame."""

    df.attrs["data_quality"] = assess_data_quality(
        df=df,
        timeframe=timeframe,
        end=end,
        duplicate_bars=duplicate_bars,
        resampled=resampled,
    )
    return df


def window_for_bars(timeframe: Timeframe, bars: int, end: datetime | None = None) -> DataWindow:
    """Estimate a timestamp window large enough to include the requested FX bars."""

    end_time = end.astimezone(timezone.utc) if end is not None else datetime.now(timezone.utc)
    minutes = TIMEFRAME_MINUTES[timeframe]
    calendar_padding = 2.8 if minutes < 1440 else 1.8
    start_time = end_time - timedelta(minutes=int(minutes * bars * calendar_padding))
    return DataWindow(start=start_time, end=end_time)


def resample_ohlcv(df: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Resample an OHLCV frame to a coarser timeframe."""

    rule = TIMEFRAME_PANDAS_RULE[timeframe]
    aggregation: dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "spread": "mean",
    }
    available = {column: method for column, method in aggregation.items() if column in df.columns}
    resampled = df.resample(rule).agg(available).dropna(subset=["open", "high", "low", "close"])
    return validate_ohlcv(resampled, min_rows=min(120, len(resampled))) if len(resampled) else resampled


def pips_to_price(symbol: str, pips: float) -> float:
    """Convert pips to price units for a Forex symbol."""

    return pips * pip_size(symbol)


def price_to_pips(symbol: str, price_distance: float) -> float:
    """Convert a price distance into pips for a Forex symbol."""

    return price_distance / pip_size(symbol)


def pip_size(symbol: str) -> float:
    """Return the conventional pip size for the pair."""

    normalized = symbol.replace("/", "").upper()
    if normalized.startswith("XAU"):
        return 0.01
    return 0.01 if normalized.endswith("JPY") else 0.0001
