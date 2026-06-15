"""Local real-FX data acquisition for CsvHistoricalProvider.

RUN THIS LOCALLY — the cloud session's network allowlist blocks external data
sources. It downloads real Dukascopy bars (bid + ask -> real spread) or
aggregates locally-provided HistData M1 files, and writes
``data/real/<SYMBOL><TF>.csv`` in the exact schema CsvHistoricalProvider expects.

Sources:
  * ``--source dukascopy`` (default): uses the maintained ``dukascopy-python``
    library (verified installable: PyPI ``dukascopy-python`` 4.x). Fetches BID and
    ASK OHLC separately and sets ``spread = ask_close - bid_close`` (price units).
    Bar OHLC are the **bid** prices (tradeable bid); cost is the spread on top,
    consistent with the backtester's "buy at ask, sell at bid" round-trip model.
  * ``--source histdata``: aggregates raw HistData "Generic ASCII" M1 files placed
    under ``data/raw_histdata/`` into M5/M15/H1. No spread is available there, so
    the ``spread`` column is left empty and the backtester falls back to the
    documented per-symbol pip cost.

Paper/demo only. This utility only reads market data; it never trades.
It fails loudly and never fabricates data.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.types import Timeframe
from app.data.validation import resample_ohlcv

LOGGER = logging.getLogger("fetch_real_data")

# Internal timeframe -> Dukascopy interval constant name (resolved lazily so the
# library import only happens on the network path).
_TF_TO_DUKA_INTERVAL = {
    "M1": "INTERVAL_MIN_1",
    "M5": "INTERVAL_MIN_5",
    "M15": "INTERVAL_MIN_15",
    "H1": "INTERVAL_HOUR_1",
    "H4": "INTERVAL_HOUR_4",
    "D1": "INTERVAL_DAY_1",
}
OUTPUT_COLUMNS = ["open", "high", "low", "close", "volume", "spread"]


@dataclass(frozen=True)
class WriteResult:
    path: Path
    rows: int
    start: pd.Timestamp | None
    end: pd.Timestamp | None


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested, no network)
# --------------------------------------------------------------------------- #
def combine_bid_ask(bid: pd.DataFrame, ask: pd.DataFrame) -> pd.DataFrame:
    """Combine bid/ask OHLC frames into bid-OHLC + real per-bar spread.

    Bar prices are the **bid** OHLCV; ``spread = ask_close - bid_close`` in price
    units, clipped at 0. Frames are aligned on their shared timestamps.
    """

    if bid.empty or ask.empty:
        raise ValueError("bid/ask frames must be non-empty")
    index = bid.index.intersection(ask.index)
    if len(index) == 0:
        raise ValueError("bid and ask frames share no timestamps")
    bid = bid.loc[index].sort_index()
    ask = ask.loc[index].sort_index()
    out = pd.DataFrame(
        {
            "open": bid["open"].to_numpy(),
            "high": bid["high"].to_numpy(),
            "low": bid["low"].to_numpy(),
            "close": bid["close"].to_numpy(),
            "volume": bid["volume"].to_numpy(),
            "spread": (ask["close"].to_numpy() - bid["close"].to_numpy()).clip(min=0.0),
        },
        index=index,
    )
    return out


def aggregate_m1(m1: pd.DataFrame, timeframe: Timeframe) -> pd.DataFrame:
    """Aggregate an M1 OHLCV(+spread) frame to a coarser timeframe."""

    if timeframe == Timeframe.M1:
        return m1.copy()
    return resample_ohlcv(m1, timeframe)


def parse_histdata_m1(path: Path, source_tz: str = "Etc/GMT+5") -> pd.DataFrame:
    """Parse a HistData 'Generic ASCII' M1 file into a UTC-indexed OHLCV frame.

    Expected format (semicolon-separated, no header):
    ``YYYYMMDD HHMMSS;OPEN;HIGH;LOW;CLOSE;VOLUME``. HistData generic ASCII M1
    timestamps are in EST without DST (``Etc/GMT+5``); override with ``source_tz``
    if your files differ. Volume is often 0 for FX and is kept as-is.
    """

    if not path.is_file():
        raise FileNotFoundError(f"HistData file not found: {path}")
    raw = pd.read_csv(path, sep=";", header=None, names=["dt", "open", "high", "low", "close", "volume"])
    if raw.empty:
        raise ValueError(f"HistData file is empty: {path}")
    local = pd.to_datetime(raw["dt"], format="%Y%m%d %H%M%S", errors="coerce")
    if local.isna().all():
        raise ValueError(f"HistData timestamps unparseable in {path} (expected 'YYYYMMDD HHMMSS')")
    index = pd.DatetimeIndex(local).tz_localize(source_tz).tz_convert("UTC")
    frame = pd.DataFrame(
        {
            "open": raw["open"].to_numpy(),
            "high": raw["high"].to_numpy(),
            "low": raw["low"].to_numpy(),
            "close": raw["close"].to_numpy(),
            "volume": raw["volume"].to_numpy(),
        },
        index=index,
    ).sort_index()
    return frame[~frame.index.isna()]


def write_output_csv(frame: pd.DataFrame, symbol: str, timeframe: Timeframe, out_dir: Path) -> WriteResult:
    """Write a frame to ``data/real/<SYMBOL><TF>.csv`` in the provider schema."""

    if frame.empty:
        raise ValueError(f"refusing to write empty frame for {symbol} {timeframe.value}")
    out_dir.mkdir(parents=True, exist_ok=True)
    normalized = symbol.replace("/", "").upper()
    path = out_dir / f"{normalized}_{timeframe.value}.csv"
    export = frame.copy()
    for column in OUTPUT_COLUMNS:
        if column not in export.columns:
            export[column] = pd.NA if column == "spread" else 0.0
    export = export[OUTPUT_COLUMNS]
    export.insert(0, "timestamp", pd.DatetimeIndex(export.index).strftime("%Y-%m-%dT%H:%M:%SZ"))
    export.to_csv(path, index=False)
    idx = pd.DatetimeIndex(frame.index)
    return WriteResult(path=path, rows=len(frame), start=idx.min(), end=idx.max())


# --------------------------------------------------------------------------- #
# Dukascopy network path (NOT unit-tested; requires external access)
# --------------------------------------------------------------------------- #
def fetch_dukascopy_bidask(symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
    """Fetch real Dukascopy bid+ask bars and return bid-OHLC + real spread."""

    try:
        import dukascopy_python  # noqa: PLC0415 - lazy: only needed for the network path
    except ImportError as exc:
        raise SystemExit(
            "dukascopy-python is not installed. Run locally: pip install dukascopy-python"
        ) from exc

    interval_name = _TF_TO_DUKA_INTERVAL.get(timeframe.value)
    if interval_name is None:
        raise SystemExit(f"unsupported timeframe for Dukascopy: {timeframe.value}")
    interval = getattr(dukascopy_python, interval_name)
    instrument = symbol.upper()  # dukascopy instrument strings are slashed, e.g. 'EUR/USD'

    LOGGER.info("fetching %s %s bid/ask from Dukascopy %s -> %s", symbol, timeframe.value, start, end)
    bid = dukascopy_python.fetch(instrument, interval, dukascopy_python.OFFER_SIDE_BID, start, end)
    ask = dukascopy_python.fetch(instrument, interval, dukascopy_python.OFFER_SIDE_ASK, start, end)
    if bid is None or bid.empty or ask is None or ask.empty:
        raise SystemExit(f"Dukascopy returned no data for {symbol} {timeframe.value} in range")
    return combine_bid_ask(bid, ask)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Local real-FX data acquisition. Reporting/data only; no orders.")
    parser.add_argument("--source", choices=["dukascopy", "histdata"], default="dukascopy")
    parser.add_argument("--symbols", nargs="+", default=["EUR/USD", "GBP/USD"])
    parser.add_argument("--timeframes", nargs="+", default=["H1", "M15", "M5"])
    parser.add_argument("--from-date", required=True, help="UTC start, e.g. 2018-01-01")
    parser.add_argument("--to-date", required=True, help="UTC end, e.g. 2026-01-01")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "data" / "real"))
    parser.add_argument("--histdata-dir", default=str(PROJECT_ROOT / "data" / "raw_histdata"))
    parser.add_argument("--histdata-tz", default="Etc/GMT+5", help="Timezone of raw HistData M1 timestamps.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    start = _parse_date(args.from_date)
    end = _parse_date(args.to_date)
    out_dir = Path(args.out_dir)
    timeframes = [Timeframe(tf) for tf in args.timeframes]

    results: list[WriteResult] = []
    for symbol in args.symbols:
        if args.source == "dukascopy":
            for timeframe in timeframes:
                frame = fetch_dukascopy_bidask(symbol, timeframe, start, end)
                results.append(write_output_csv(frame, symbol, timeframe, out_dir))
        else:
            results.append(_run_histdata(symbol, timeframes, start, end, out_dir, Path(args.histdata_dir), args.histdata_tz))

    for result in results:
        print(f"wrote {result.path.name}: rows={result.rows} coverage={result.start} -> {result.end}")
    if not results:
        raise SystemExit("no files written")


def _run_histdata(
    symbol: str,
    timeframes: list[Timeframe],
    start: datetime,
    end: datetime,
    out_dir: Path,
    histdata_dir: Path,
    source_tz: str,
) -> WriteResult:
    normalized = symbol.replace("/", "").upper()
    m1_path = histdata_dir / f"{normalized}_M1.csv"
    if not m1_path.is_file():
        raise SystemExit(f"HistData M1 file not found: {m1_path} (place raw HistData generic-ASCII M1 there)")
    m1 = parse_histdata_m1(m1_path, source_tz=source_tz)
    m1 = m1[(m1.index >= _as_utc(start)) & (m1.index <= _as_utc(end))]
    last: WriteResult | None = None
    for timeframe in timeframes:
        aggregated = aggregate_m1(m1, timeframe)
        last = write_output_csv(aggregated, symbol, timeframe, out_dir)
        print(f"wrote {last.path.name}: rows={last.rows} coverage={last.start} -> {last.end}")
    if last is None:
        raise SystemExit("no timeframes requested")
    return last


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


if __name__ == "__main__":
    main()
