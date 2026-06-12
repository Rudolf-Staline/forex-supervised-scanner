#!/usr/bin/env python
"""Bounded, read-only local MT5 realtime market-data validation."""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.watchlists import get_watchlist, watchlist_names

DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports"
JSON_REPORT_NAME = "local_mt5_realtime_validation.json"
TXT_REPORT_NAME = "local_mt5_realtime_validation.txt"
CSV_REPORT_NAME = "local_mt5_realtime_samples.csv"
SAFETY_BANNER = (
    "SAFETY: local MT5 realtime validation is read-only market-data validation only; "
    "no live trading or broker order submission is authorized."
)

STATUS_READY = "MT5_REALTIME_READY"
STATUS_WARN = "MT5_REALTIME_WARN"
BLOCKED_MT5_UNAVAILABLE = "BLOCKED_MT5_UNAVAILABLE"
BLOCKED_TERMINAL_INIT_FAILED = "BLOCKED_TERMINAL_INIT_FAILED"
BLOCKED_ACCOUNT_INFO_UNAVAILABLE = "BLOCKED_ACCOUNT_INFO_UNAVAILABLE"
BLOCKED_SYMBOL_UNAVAILABLE = "BLOCKED_SYMBOL_UNAVAILABLE"
BLOCKED_STALE_DATA = "BLOCKED_STALE_DATA"
BLOCKED_SPREAD_TOO_WIDE = "BLOCKED_SPREAD_TOO_WIDE"
BLOCKED_POOR_DATA_QUALITY = "BLOCKED_POOR_DATA_QUALITY"

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}

SYMBOL_ALIASES = {
    "EUR/USD": ["EURUSD"],
    "GBP/USD": ["GBPUSD"],
    "USD/CHF": ["USDCHF"],
    "USD/JPY": ["USDJPY"],
    "AUD/USD": ["AUDUSD"],
    "USD/CAD": ["USDCAD"],
    "NZD/USD": ["NZDUSD"],
    "EUR/JPY": ["EURJPY"],
    "GBP/JPY": ["GBPJPY"],
    "EUR/GBP": ["EURGBP"],
    "XAU/USD": ["XAUUSD"],
    "XAG/USD": ["XAGUSD"],
    "WTI/OIL": ["US Oil"],
    "BRENT/OIL": ["UK Brent Oil"],
    "US500": ["US SP 500"],
    "US30": ["Wall Street 30"],
    "NAS100": ["US Tech 100"],
    "GER40": ["Germany 40"],
    "UK100": ["UK 100"],
    "FRA40": ["France 40"],
}


@dataclass
class ValidationSample:
    sampled_at: str
    symbol: str
    resolved_symbol: str
    timeframe: str
    symbol_selected: bool
    latest_candle_time: str | None
    latest_candle_age_seconds: float | None
    latest_tick_time: str | None
    spread: float | None
    atr: float | None
    spread_atr_ratio: float | None
    missing_bars: int
    duplicate_bars: int
    latency_ms: float | None
    provider_latency_ms: float | None
    status: str
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    started_at: str
    completed_at: str | None
    duration_minutes: float
    interval_seconds: float
    symbols: list[str]
    timeframes: list[str]
    mt5_import_ok: bool
    terminal_initialized: bool
    account_info_available: bool
    terminal_info_available: bool
    symbol_selected: dict[str, bool]
    latest_candle_time: dict[str, str | None]
    latest_candle_age_seconds: dict[str, float | None]
    latest_tick_time: dict[str, str | None]
    spread: dict[str, float | None]
    atr: dict[str, float | None]
    spread_atr_ratio: dict[str, float | None]
    missing_bars: dict[str, int]
    duplicate_bars: dict[str, int]
    latency_ms: dict[str, float | None]
    provider_latency_ms: dict[str, float | None]
    sample_count: int
    final_status: str
    blocking_reasons: list[str]
    warnings: list[str]
    safety_flags: dict[str, bool | str]
    output_paths: dict[str, str]
    samples: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ValidationConfig:
    symbols: list[str]
    watchlist: str | None
    timeframes: list[str]
    duration_minutes: float
    interval_seconds: float
    max_candle_age_seconds: float
    max_spread_atr_ratio: float
    reports_dir: Path
    export_json: bool
    export_txt: bool
    export_csv: bool
    strict: bool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only local Windows MT5 realtime market-data validation.")
    parser.add_argument("--symbols", nargs="+", default=None, help="Logical symbols such as EUR/USD GBP/USD.")
    parser.add_argument("--watchlist", choices=watchlist_names(), default=None, help="Configured watchlist to validate.")
    parser.add_argument("--timeframes", nargs="+", choices=sorted(TIMEFRAME_SECONDS), default=["M1", "M5"])
    parser.add_argument("--duration-minutes", type=float, default=15.0, help="Bounded polling duration; use 0 for one immediate sample.")
    parser.add_argument("--interval-seconds", type=float, default=30.0, help="Delay between bounded samples; use 0 for no sleep.")
    parser.add_argument("--max-candle-age-seconds", type=float, default=180.0)
    parser.add_argument("--max-spread-atr-ratio", type=float, default=0.25)
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when MT5/local data validation is blocked.")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> ValidationConfig:
    symbols = _symbols_from_args(args.symbols, args.watchlist)
    return ValidationConfig(
        symbols=symbols,
        watchlist=args.watchlist,
        timeframes=list(dict.fromkeys(args.timeframes)),
        duration_minutes=max(0.0, float(args.duration_minutes)),
        interval_seconds=max(0.0, float(args.interval_seconds)),
        max_candle_age_seconds=max(0.0, float(args.max_candle_age_seconds)),
        max_spread_atr_ratio=max(0.0, float(args.max_spread_atr_ratio)),
        reports_dir=Path(args.reports_dir),
        export_json=bool(args.export_json),
        export_txt=bool(args.export_txt),
        export_csv=bool(args.export_csv),
        strict=bool(args.strict),
    )


def run_validation(config: ValidationConfig, mt5: object | None = None) -> ValidationReport:
    started_at = _utc_now_iso()
    mt5_import_ok = mt5 is not None
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    if mt5 is None:
        mt5 = import_mt5()
        mt5_import_ok = mt5 is not None

    if mt5 is None:
        blocking_reasons.append(BLOCKED_MT5_UNAVAILABLE)
        warnings.append("MetaTrader5 Python package is unavailable; CI-safe mock execution may still export reports.")
        return _empty_report(config, started_at=started_at, mt5_import_ok=False, blocking_reasons=blocking_reasons, warnings=warnings)

    terminal_initialized = bool(_safe_call(mt5, "initialize", default=False))
    if not terminal_initialized:
        blocking_reasons.append(BLOCKED_TERMINAL_INIT_FAILED)

    account_info = _safe_call(mt5, "account_info") if terminal_initialized else None
    terminal_info = _safe_call(mt5, "terminal_info") if terminal_initialized else None
    account_info_available = account_info is not None
    terminal_info_available = terminal_info is not None
    if terminal_initialized and not account_info_available:
        blocking_reasons.append(BLOCKED_ACCOUNT_INFO_UNAVAILABLE)
    if terminal_initialized and not terminal_info_available:
        warnings.append("MT5 terminal_info was unavailable.")

    samples: list[ValidationSample] = []
    symbol_selected: dict[str, bool] = {symbol: False for symbol in config.symbols}

    if terminal_initialized and account_info_available:
        deadline = time.monotonic() + (config.duration_minutes * 60.0)
        first = True
        while first or time.monotonic() < deadline:
            first = False
            samples.extend(_collect_samples(mt5, config, symbol_selected))
            if config.duration_minutes <= 0 or config.interval_seconds <= 0 or time.monotonic() >= deadline:
                break
            time.sleep(min(config.interval_seconds, max(0.0, deadline - time.monotonic())))
    elif terminal_initialized:
        warnings.append("Market-data checks were skipped because account_info was unavailable.")

    report = _build_report(
        config,
        started_at=started_at,
        mt5_import_ok=mt5_import_ok,
        terminal_initialized=terminal_initialized,
        account_info_available=account_info_available,
        terminal_info_available=terminal_info_available,
        symbol_selected=symbol_selected,
        samples=samples,
        initial_blocking_reasons=blocking_reasons,
        initial_warnings=warnings,
    )
    export_reports(report, config)
    return report


def import_mt5() -> object | None:
    try:
        return importlib.import_module("MetaTrader5")
    except Exception:
        return None


def _symbols_from_args(symbols: list[str] | None, watchlist: str | None) -> list[str]:
    selected: list[str] = []
    if watchlist:
        selected.extend(get_watchlist(watchlist))
    if symbols:
        selected.extend(symbols)
    if not selected:
        selected = ["EUR/USD"]
    return list(dict.fromkeys(selected))


def _empty_report(
    config: ValidationConfig,
    *,
    started_at: str,
    mt5_import_ok: bool,
    blocking_reasons: list[str],
    warnings: list[str],
) -> ValidationReport:
    report = ValidationReport(
        started_at=started_at,
        completed_at=_utc_now_iso(),
        duration_minutes=config.duration_minutes,
        interval_seconds=config.interval_seconds,
        symbols=config.symbols,
        timeframes=config.timeframes,
        mt5_import_ok=mt5_import_ok,
        terminal_initialized=False,
        account_info_available=False,
        terminal_info_available=False,
        symbol_selected={symbol: False for symbol in config.symbols},
        latest_candle_time={},
        latest_candle_age_seconds={},
        latest_tick_time={},
        spread={},
        atr={},
        spread_atr_ratio={},
        missing_bars={},
        duplicate_bars={},
        latency_ms={},
        provider_latency_ms={},
        sample_count=0,
        final_status=blocking_reasons[0] if blocking_reasons else STATUS_WARN,
        blocking_reasons=list(dict.fromkeys(blocking_reasons)),
        warnings=list(dict.fromkeys(warnings)),
        safety_flags=_safety_flags(),
        output_paths={},
        samples=[],
    )
    export_reports(report, config)
    return report


def _collect_samples(mt5: object, config: ValidationConfig, symbol_selected: dict[str, bool]) -> list[ValidationSample]:
    samples: list[ValidationSample] = []
    for logical_symbol in config.symbols:
        resolved_symbol = _resolve_symbol(mt5, logical_symbol)
        selected = False
        if resolved_symbol:
            selected = bool(_safe_call(mt5, "symbol_select", resolved_symbol, True, default=False))
        symbol_selected[logical_symbol] = symbol_selected.get(logical_symbol, False) or selected
        for timeframe in config.timeframes:
            samples.append(_sample_symbol_timeframe(mt5, config, logical_symbol, resolved_symbol or "", timeframe, selected))
    return samples


def _sample_symbol_timeframe(
    mt5: object,
    config: ValidationConfig,
    logical_symbol: str,
    resolved_symbol: str,
    timeframe: str,
    selected: bool,
) -> ValidationSample:
    sampled_at = _utc_now_iso()
    blocking: list[str] = []
    warnings: list[str] = []
    if not resolved_symbol or not selected:
        blocking.append(BLOCKED_SYMBOL_UNAVAILABLE)

    latency_start = time.perf_counter()
    bars = [] if not selected else _copy_rates(mt5, resolved_symbol, timeframe, count=64)
    tick = None if not selected else _safe_call(mt5, "symbol_info_tick", resolved_symbol)
    latency_ms = round((time.perf_counter() - latency_start) * 1000.0, 3)

    latest_candle_time = None
    latest_candle_age_seconds = None
    atr = None
    missing_bars = 0
    duplicate_bars = 0
    if selected and not bars:
        blocking.append(BLOCKED_POOR_DATA_QUALITY)
        warnings.append("No candles returned for selected symbol/timeframe.")
    elif bars:
        latest_time = _bar_value(bars[-1], "time")
        latest_candle_time = _timestamp_to_iso(latest_time)
        latest_candle_age_seconds = _age_seconds(latest_time)
        atr = _calculate_atr(bars)
        missing_bars = _count_missing_bars(bars, TIMEFRAME_SECONDS[timeframe])
        duplicate_bars = _count_duplicate_bars(bars)
        if latest_candle_age_seconds is not None and latest_candle_age_seconds > config.max_candle_age_seconds:
            blocking.append(BLOCKED_STALE_DATA)
        if missing_bars or duplicate_bars:
            blocking.append(BLOCKED_POOR_DATA_QUALITY)

    latest_tick_time = _tick_time_iso(tick)
    spread = _calculate_spread(tick)
    ratio = _safe_ratio(spread, atr)
    if ratio is not None and ratio > config.max_spread_atr_ratio:
        blocking.append(BLOCKED_SPREAD_TOO_WIDE)
    elif spread is None:
        warnings.append("Latest tick or bid/ask spread was unavailable.")
    elif atr is None or atr <= 0:
        warnings.append("ATR was unavailable, so spread/ATR could not be evaluated.")

    status = STATUS_READY
    if blocking:
        status = blocking[0]
    elif warnings:
        status = STATUS_WARN

    return ValidationSample(
        sampled_at=sampled_at,
        symbol=logical_symbol,
        resolved_symbol=resolved_symbol,
        timeframe=timeframe,
        symbol_selected=selected,
        latest_candle_time=latest_candle_time,
        latest_candle_age_seconds=_round_or_none(latest_candle_age_seconds),
        latest_tick_time=latest_tick_time,
        spread=_round_or_none(spread),
        atr=_round_or_none(atr),
        spread_atr_ratio=_round_or_none(ratio),
        missing_bars=missing_bars,
        duplicate_bars=duplicate_bars,
        latency_ms=latency_ms,
        provider_latency_ms=latency_ms,
        status=status,
        blocking_reasons=list(dict.fromkeys(blocking)),
        warnings=list(dict.fromkeys(warnings)),
    )


def _copy_rates(mt5: object, symbol: str, timeframe: str, *, count: int) -> list[Any]:
    mt5_timeframe = getattr(mt5, f"TIMEFRAME_{timeframe}", None)
    if mt5_timeframe is None:
        return []
    values = _safe_call(mt5, "copy_rates_from_pos", symbol, mt5_timeframe, 0, count, default=[])
    if values is None:
        return []
    try:
        return list(values)
    except TypeError:
        return []


def _resolve_symbol(mt5: object, logical_symbol: str) -> str | None:
    candidates = [*SYMBOL_ALIASES.get(logical_symbol, []), logical_symbol.replace("/", ""), logical_symbol]
    for candidate in list(dict.fromkeys(candidates)):
        if _safe_call(mt5, "symbol_info", candidate) is not None:
            return candidate
    return None


def _build_report(
    config: ValidationConfig,
    *,
    started_at: str,
    mt5_import_ok: bool,
    terminal_initialized: bool,
    account_info_available: bool,
    terminal_info_available: bool,
    symbol_selected: dict[str, bool],
    samples: list[ValidationSample],
    initial_blocking_reasons: list[str],
    initial_warnings: list[str],
) -> ValidationReport:
    sample_dicts = [asdict(sample) for sample in samples]
    blocking_reasons = list(initial_blocking_reasons)
    warnings = list(initial_warnings)
    for sample in samples:
        blocking_reasons.extend(sample.blocking_reasons)
        warnings.extend(sample.warnings)
    if terminal_initialized and account_info_available and not samples:
        blocking_reasons.append(BLOCKED_POOR_DATA_QUALITY)
    for symbol, selected in symbol_selected.items():
        if not selected and terminal_initialized and account_info_available:
            blocking_reasons.append(f"{BLOCKED_SYMBOL_UNAVAILABLE}:{symbol}")

    final_status = _final_status(blocking_reasons, warnings)
    latest_by_key = _latest_by_symbol_timeframe(samples)
    report = ValidationReport(
        started_at=started_at,
        completed_at=_utc_now_iso(),
        duration_minutes=config.duration_minutes,
        interval_seconds=config.interval_seconds,
        symbols=config.symbols,
        timeframes=config.timeframes,
        mt5_import_ok=mt5_import_ok,
        terminal_initialized=terminal_initialized,
        account_info_available=account_info_available,
        terminal_info_available=terminal_info_available,
        symbol_selected=symbol_selected,
        latest_candle_time={key: sample.latest_candle_time for key, sample in latest_by_key.items()},
        latest_candle_age_seconds={key: sample.latest_candle_age_seconds for key, sample in latest_by_key.items()},
        latest_tick_time={key: sample.latest_tick_time for key, sample in latest_by_key.items()},
        spread={key: sample.spread for key, sample in latest_by_key.items()},
        atr={key: sample.atr for key, sample in latest_by_key.items()},
        spread_atr_ratio={key: sample.spread_atr_ratio for key, sample in latest_by_key.items()},
        missing_bars={key: sample.missing_bars for key, sample in latest_by_key.items()},
        duplicate_bars={key: sample.duplicate_bars for key, sample in latest_by_key.items()},
        latency_ms={key: sample.latency_ms for key, sample in latest_by_key.items()},
        provider_latency_ms={key: sample.provider_latency_ms for key, sample in latest_by_key.items()},
        sample_count=len(samples),
        final_status=final_status,
        blocking_reasons=list(dict.fromkeys(blocking_reasons)),
        warnings=list(dict.fromkeys(warnings)),
        safety_flags=_safety_flags(),
        output_paths={},
        samples=sample_dicts,
    )
    return report


def _final_status(blocking_reasons: list[str], warnings: list[str]) -> str:
    unique = list(dict.fromkeys(blocking_reasons))
    if not unique:
        return STATUS_WARN if warnings else STATUS_READY
    priority = [
        BLOCKED_MT5_UNAVAILABLE,
        BLOCKED_TERMINAL_INIT_FAILED,
        BLOCKED_ACCOUNT_INFO_UNAVAILABLE,
        BLOCKED_SYMBOL_UNAVAILABLE,
        BLOCKED_STALE_DATA,
        BLOCKED_SPREAD_TOO_WIDE,
        BLOCKED_POOR_DATA_QUALITY,
    ]
    for status in priority:
        if any(reason == status or reason.startswith(f"{status}:") for reason in unique):
            return status
    return unique[0]


def export_reports(report: ValidationReport, config: ValidationConfig) -> None:
    config.reports_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    if config.export_json:
        path = config.reports_dir / JSON_REPORT_NAME
        paths["json"] = str(path)
    if config.export_txt:
        path = config.reports_dir / TXT_REPORT_NAME
        paths["txt"] = str(path)
    if config.export_csv:
        path = config.reports_dir / CSV_REPORT_NAME
        paths["csv"] = str(path)
    report.output_paths = paths
    if "json" in paths:
        Path(paths["json"]).write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    if "txt" in paths:
        Path(paths["txt"]).write_text(_render_txt(report), encoding="utf-8")
    if "csv" in paths:
        _write_csv(Path(paths["csv"]), report.samples)


def _render_txt(report: ValidationReport) -> str:
    data = asdict(report)
    lines = ["Local MT5 Realtime Validation (read-only market data)"]
    for key, value in data.items():
        if key == "samples":
            lines.append(f"samples={len(value)}")
            continue
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, samples: list[dict[str, Any]]) -> None:
    fieldnames = [field.name for field in ValidationSample.__dataclass_fields__.values()]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            writer.writerow(sample)


def _safe_call(obj: object, name: str, *args: Any, default: Any = None) -> Any:
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        value = fn(*args)
    except Exception:
        return default
    return default if value is None else value


def _bar_value(bar: Any, key: str) -> Any:
    if isinstance(bar, dict):
        return bar.get(key)
    try:
        return bar[key]
    except Exception:
        return getattr(bar, key, None)


def _timestamp_to_iso(value: Any) -> str | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _age_seconds(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return max(0.0, datetime.now(timezone.utc).timestamp() - timestamp)


def _tick_time_iso(tick: Any) -> str | None:
    if tick is None:
        return None
    for attr in ("time_msc", "time"):
        value = getattr(tick, attr, None)
        if value is not None:
            return _timestamp_to_iso(value)
    if isinstance(tick, dict):
        return _timestamp_to_iso(tick.get("time_msc") or tick.get("time"))
    return None


def _calculate_spread(tick: Any) -> float | None:
    if tick is None:
        return None
    bid = _object_value(tick, "bid")
    ask = _object_value(tick, "ask")
    try:
        return abs(float(ask) - float(bid))
    except (TypeError, ValueError):
        return None


def _object_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _calculate_atr(bars: list[Any], period: int = 14) -> float | None:
    if len(bars) < 2:
        return None
    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars[-(period + 1) :]:
        try:
            high = float(_bar_value(bar, "high"))
            low = float(_bar_value(bar, "low"))
            close = float(_bar_value(bar, "close"))
        except (TypeError, ValueError):
            continue
        if previous_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        if math.isfinite(tr) and tr >= 0:
            true_ranges.append(tr)
        previous_close = close
    if not true_ranges:
        return None
    return sum(true_ranges) / len(true_ranges)


def _count_missing_bars(bars: list[Any], timeframe_seconds: int) -> int:
    times = _bar_times(bars)
    missing = 0
    for left, right in zip(times, times[1:]):
        gap = right - left
        if gap > timeframe_seconds * 1.5:
            missing += max(1, round(gap / timeframe_seconds) - 1)
    return missing


def _count_duplicate_bars(bars: list[Any]) -> int:
    times = _bar_times(bars)
    return len(times) - len(set(times))


def _bar_times(bars: list[Any]) -> list[int]:
    values: list[int] = []
    for bar in bars:
        try:
            values.append(int(float(_bar_value(bar, "time"))))
        except (TypeError, ValueError):
            continue
    return values


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _latest_by_symbol_timeframe(samples: list[ValidationSample]) -> dict[str, ValidationSample]:
    latest: dict[str, ValidationSample] = {}
    for sample in samples:
        latest[f"{sample.symbol}:{sample.timeframe}"] = sample
    return latest


def _safety_flags() -> dict[str, bool | str]:
    return {
        "read_only_market_data_only": True,
        "live_trading_authorized": False,
        "broker_live_execution_enabled": False,
        "order_send_called": False,
        "broker_order_submission": False,
        "env_mutation": False,
        "daemon": False,
        "infinite_loop": False,
        "bounded_duration_only": True,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = config_from_args(args)
    print(SAFETY_BANNER)
    report = run_validation(config)
    print(f"final_status={report.final_status}")
    print(f"sample_count={report.sample_count}")
    for reason in report.blocking_reasons:
        print(f"block={reason}")
    for warning in report.warnings:
        print(f"warning={warning}")
    for path in report.output_paths.values():
        print(f"report={path}")
    if config.strict and report.final_status not in {STATUS_READY, STATUS_WARN}:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
