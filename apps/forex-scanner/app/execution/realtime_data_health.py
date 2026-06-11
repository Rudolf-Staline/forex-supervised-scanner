"""Realtime market-data health checks for bounded paper/demo operation.

This module is intentionally diagnostic/read-only.  It may request candles from
configured data providers, but it never submits broker orders, never calls
``order_send``, never mutates ``.env``, and treats synthetic fallback as a
blocking condition for realtime paper mode.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from app.config.settings import PROJECT_ROOT
from app.core.types import TIMEFRAME_MINUTES, Timeframe
from app.data.providers import DataProviderError, MarketDataProvider
from app.data.validation import assess_data_quality

DEFAULT_REALTIME_DATA_HEALTH_JSON = "realtime_data_health.json"
DEFAULT_REALTIME_DATA_HEALTH_TXT = "realtime_data_health.txt"
DEFAULT_REALTIME_REPORTS_DIR = PROJECT_ROOT / "reports"


class RealtimeDataHealthStatus(StrEnum):
    REALTIME_DATA_READY = "REALTIME_DATA_READY"
    REALTIME_DATA_WARN = "REALTIME_DATA_WARN"
    BLOCKED_STALE_DATA = "BLOCKED_STALE_DATA"
    BLOCKED_SYNTHETIC_FALLBACK = "BLOCKED_SYNTHETIC_FALLBACK"
    BLOCKED_PROVIDER_FAILURE = "BLOCKED_PROVIDER_FAILURE"
    BLOCKED_POOR_DATA_QUALITY = "BLOCKED_POOR_DATA_QUALITY"
    BLOCKED_SPREAD_TOO_WIDE = "BLOCKED_SPREAD_TOO_WIDE"


class SymbolDataHealth(BaseModel):
    provider: str
    requested_provider: str
    symbol: str
    timeframe: Timeframe
    latest_candle_timestamp: datetime | None = None
    latest_candle_age_seconds: float | None = None
    data_fresh: bool = False
    spread_available: bool = False
    latest_spread: float | None = None
    atr: float | None = None
    spread_atr_ratio: float | None = None
    missing_bars: int = 0
    duplicate_bars: int = 0
    data_quality_score: float = 0.0
    provider_fallback_status: str = "not_used"
    synthetic_fallback_used: bool = False
    mt5_used: bool = False
    safe_for_realtime_paper: bool = False
    status: RealtimeDataHealthStatus
    warnings: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    rows_checked: int = 0
    error: str | None = None


class RealtimeDataHealthReport(BaseModel):
    started_at: datetime
    completed_at: datetime
    provider: str
    symbols: list[str]
    timeframe: Timeframe
    status: RealtimeDataHealthStatus
    latest_data_age_seconds: float | None = None
    data_health_status: str
    safe_for_realtime_paper: bool
    provider_fallback_status: str
    synthetic_fallback_used: bool
    mt5_used: bool
    checks: list[SymbolDataHealth]
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    output_paths: list[str] = Field(default_factory=list)


class RealtimeDataHealthConfig(BaseModel):
    provider: str = "auto"
    symbols: list[str]
    timeframe: Timeframe = Timeframe.M1
    reports_dir: Path = DEFAULT_REALTIME_REPORTS_DIR
    export_json: bool = False
    export_txt: bool = False
    max_age_seconds: float | None = None
    min_quality_score: float = Field(default=75.0, ge=0.0, le=100.0)
    warn_quality_score: float = Field(default=90.0, ge=0.0, le=100.0)
    max_spread_atr_ratio: float = Field(default=0.25, gt=0.0, le=10.0)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        symbols = [symbol.strip().upper() for symbol in value if symbol.strip()]
        if not symbols:
            raise ValueError("at least one symbol is required")
        return symbols

    @field_validator("timeframe", mode="before")
    @classmethod
    def normalize_timeframe(cls, value: object) -> object:
        return value.value if isinstance(value, Timeframe) else str(value).upper() if isinstance(value, str) else value

    @property
    def effective_max_age_seconds(self) -> float:
        if self.max_age_seconds is not None:
            return self.max_age_seconds
        return float(TIMEFRAME_MINUTES[self.timeframe] * 60 * 4)


class RealtimeDataHealthService:
    def __init__(self, provider: MarketDataProvider, now_fn: Any | None = None) -> None:
        self.provider = provider
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def check(self, config: RealtimeDataHealthConfig) -> RealtimeDataHealthReport:
        started_at = self.now_fn()
        checks = [self._check_symbol(symbol, config, started_at) for symbol in config.symbols]
        completed_at = self.now_fn()
        blocking = [reason for check in checks for reason in check.blocking_reasons]
        warnings = [reason for check in checks for reason in check.warnings]
        status = _aggregate_status(checks)
        latest_ages = [check.latest_candle_age_seconds for check in checks if check.latest_candle_age_seconds is not None]
        report = RealtimeDataHealthReport(
            started_at=started_at,
            completed_at=completed_at,
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            status=status,
            latest_data_age_seconds=max(latest_ages) if latest_ages else None,
            data_health_status=status.value,
            safe_for_realtime_paper=all(check.safe_for_realtime_paper for check in checks),
            provider_fallback_status=_aggregate_fallback(checks),
            synthetic_fallback_used=any(check.synthetic_fallback_used for check in checks),
            mt5_used=any(check.mt5_used for check in checks),
            checks=checks,
            blocking_reasons=blocking,
            warnings=warnings,
        )
        paths: list[Path] = []
        if config.export_json:
            paths.append(config.reports_dir / DEFAULT_REALTIME_DATA_HEALTH_JSON)
        if config.export_txt:
            paths.append(config.reports_dir / DEFAULT_REALTIME_DATA_HEALTH_TXT)
        report = report.model_copy(update={"output_paths": [str(path) for path in paths]})
        if config.export_json:
            export_realtime_data_health_json(report, config.reports_dir)
        if config.export_txt:
            export_realtime_data_health_txt(report, config.reports_dir)
        return report

    def _check_symbol(self, symbol: str, config: RealtimeDataHealthConfig, now: datetime) -> SymbolDataHealth:
        requested = config.provider
        try:
            start = now - timedelta(minutes=TIMEFRAME_MINUTES[config.timeframe] * 260)
            df = self.provider.get_ohlcv(symbol, config.timeframe, start=start, end=now)
        except Exception as exc:
            reason = f"provider failed for {symbol}: {exc}"
            return SymbolDataHealth(
                provider=requested,
                requested_provider=requested,
                symbol=symbol,
                timeframe=config.timeframe,
                provider_fallback_status="provider_failure",
                status=RealtimeDataHealthStatus.BLOCKED_PROVIDER_FAILURE,
                blocking_reasons=[reason],
                error=str(exc),
            )

        actual_provider = str(df.attrs.get("provider") or getattr(self.provider, "name", requested))
        warning = df.attrs.get("warning")
        provider_fallback_status = _fallback_status(requested, actual_provider, warning)
        synthetic_used = actual_provider.lower() == "synthetic" or "synthetic" in provider_fallback_status.lower()
        mt5_used = actual_provider.lower() == "mt5"
        duplicate_bars = int(pd.to_datetime(df.index, utc=True).duplicated().sum()) if isinstance(df.index, pd.DatetimeIndex) else 0
        quality = df.attrs.get("data_quality") or assess_data_quality(df, config.timeframe, end=now, duplicate_bars=duplicate_bars)
        index = pd.to_datetime(df.index, utc=True).sort_values() if isinstance(df.index, pd.DatetimeIndex) else pd.DatetimeIndex([])
        latest_ts = index[-1].to_pydatetime() if len(index) else None
        age_seconds = max(0.0, (now - latest_ts).total_seconds()) if latest_ts is not None else None
        spread_series = pd.to_numeric(df.get("spread", pd.Series(dtype=float)), errors="coerce").dropna()
        spread_available = not spread_series.empty
        latest_spread = float(spread_series.iloc[-1]) if spread_available else None
        atr = _average_true_range(df)
        spread_atr_ratio = (latest_spread / atr) if latest_spread is not None and atr and atr > 0 else None
        missing_bars = int(getattr(quality, "missing_bars", 0) or 0)
        data_quality_score = float(getattr(quality, "score", 0.0) or 0.0)
        warnings = list(getattr(quality, "warnings", []) or [])
        if warning:
            warnings.append(str(warning))

        blocking: list[str] = []
        data_fresh = age_seconds is not None and age_seconds <= config.effective_max_age_seconds
        if synthetic_used:
            blocking.append("synthetic fallback is not accepted for realtime paper mode")
        if not data_fresh:
            blocking.append(f"latest candle is stale or unavailable for {symbol}")
        if data_quality_score < config.min_quality_score:
            blocking.append(f"data quality score {data_quality_score:.1f} is below {config.min_quality_score:.1f}")
        if spread_atr_ratio is not None and spread_atr_ratio > config.max_spread_atr_ratio:
            blocking.append(f"spread/ATR ratio {spread_atr_ratio:.3f} exceeds {config.max_spread_atr_ratio:.3f}")
        if data_quality_score < config.warn_quality_score and not blocking:
            warnings.append(f"data quality score {data_quality_score:.1f} is below warning threshold")

        status = _symbol_status(blocking, warnings, synthetic_used, data_fresh, data_quality_score, spread_atr_ratio, config)
        return SymbolDataHealth(
            provider=actual_provider,
            requested_provider=requested,
            symbol=symbol,
            timeframe=config.timeframe,
            latest_candle_timestamp=latest_ts,
            latest_candle_age_seconds=age_seconds,
            data_fresh=data_fresh,
            spread_available=spread_available,
            latest_spread=latest_spread,
            atr=atr,
            spread_atr_ratio=spread_atr_ratio,
            missing_bars=missing_bars,
            duplicate_bars=duplicate_bars + int(getattr(quality, "duplicate_bars", 0) or 0),
            data_quality_score=data_quality_score,
            provider_fallback_status=provider_fallback_status,
            synthetic_fallback_used=synthetic_used,
            mt5_used=mt5_used,
            safe_for_realtime_paper=not blocking,
            status=status,
            warnings=warnings,
            blocking_reasons=blocking,
            rows_checked=len(df),
        )


def export_realtime_data_health_json(report: RealtimeDataHealthReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_DATA_HEALTH_JSON
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_realtime_data_health_txt(report: RealtimeDataHealthReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_DATA_HEALTH_TXT
    lines = [
        f"realtime_data_health={report.status.value}",
        f"started_at={report.started_at.isoformat()}",
        f"completed_at={report.completed_at.isoformat()}",
        f"provider={report.provider}",
        f"symbols={','.join(report.symbols)}",
        f"timeframe={report.timeframe.value}",
        f"latest_data_age_seconds={report.latest_data_age_seconds}",
        f"safe_for_realtime_paper={str(report.safe_for_realtime_paper).lower()}",
        f"synthetic_fallback_used={str(report.synthetic_fallback_used).lower()}",
        f"mt5_used={str(report.mt5_used).lower()}",
    ]
    for reason in report.blocking_reasons:
        lines.append(f"block={reason}")
    for warning in report.warnings:
        lines.append(f"warning={warning}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _average_true_range(df: pd.DataFrame, period: int = 14) -> float | None:
    if not {"high", "low", "close"}.issubset(df.columns) or len(df) < 2:
        return None
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)
    true_range = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = true_range.dropna().tail(period).mean()
    return None if pd.isna(atr) else float(atr)


def _fallback_status(requested: str, actual: str, warning: object) -> str:
    if requested.lower() == actual.lower() and not warning:
        return "not_used"
    if actual.lower() == "synthetic":
        return "synthetic_fallback_used"
    return f"fallback_to_{actual}"


def _symbol_status(blocking: list[str], warnings: list[str], synthetic: bool, fresh: bool, score: float, ratio: float | None, config: RealtimeDataHealthConfig) -> RealtimeDataHealthStatus:
    if synthetic:
        return RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK
    if not fresh:
        return RealtimeDataHealthStatus.BLOCKED_STALE_DATA
    if ratio is not None and ratio > config.max_spread_atr_ratio:
        return RealtimeDataHealthStatus.BLOCKED_SPREAD_TOO_WIDE
    if score < config.min_quality_score:
        return RealtimeDataHealthStatus.BLOCKED_POOR_DATA_QUALITY
    if blocking:
        return RealtimeDataHealthStatus.BLOCKED_PROVIDER_FAILURE
    if warnings:
        return RealtimeDataHealthStatus.REALTIME_DATA_WARN
    return RealtimeDataHealthStatus.REALTIME_DATA_READY


def _aggregate_status(checks: list[SymbolDataHealth]) -> RealtimeDataHealthStatus:
    priority = [
        RealtimeDataHealthStatus.BLOCKED_PROVIDER_FAILURE,
        RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK,
        RealtimeDataHealthStatus.BLOCKED_STALE_DATA,
        RealtimeDataHealthStatus.BLOCKED_SPREAD_TOO_WIDE,
        RealtimeDataHealthStatus.BLOCKED_POOR_DATA_QUALITY,
        RealtimeDataHealthStatus.REALTIME_DATA_WARN,
        RealtimeDataHealthStatus.REALTIME_DATA_READY,
    ]
    statuses = {check.status for check in checks}
    for status in priority:
        if status in statuses:
            return status
    return RealtimeDataHealthStatus.BLOCKED_PROVIDER_FAILURE


def _aggregate_fallback(checks: list[SymbolDataHealth]) -> str:
    statuses = {check.provider_fallback_status for check in checks}
    if "synthetic_fallback_used" in statuses:
        return "synthetic_fallback_used"
    if len(statuses) == 1:
        return next(iter(statuses))
    return "mixed"
