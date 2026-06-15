# Real OHLCV data for `CsvHistoricalProvider`

Place **real** historical FX bars here. The provider
(`app/data/providers.py::CsvHistoricalProvider`, selected via
`settings.provider.name = "csv"`) reads them with **no network and no synthetic
fallback** — a missing/invalid file fails loudly rather than being silently
replaced by fake data.

> Paper/demo only. This data is for backtesting/analysis; nothing here places orders.

## File naming

One CSV per `(symbol, timeframe)`:

```
<SYMBOL_WITHOUT_SLASH>_<TIMEFRAME>.csv
```

- Symbol: the slash removed, upper-case — `EUR/USD` → `EURUSD`.
- Timeframe: the `Timeframe` value — `H1`, `M15`, `M5`, `M1`, `H4`, `D1`.

The `day_trading` style needs three timeframes per symbol: **H1, M15, M5**.
Example for an EUR/USD + GBP/USD day-trading run:

```
EURUSD_H1.csv  EURUSD_M15.csv  EURUSD_M5.csv
GBPUSD_H1.csv  GBPUSD_M15.csv  GBPUSD_M5.csv
```

## Required columns (header row, case-insensitive)

| column | required | meaning |
| --- | --- | --- |
| `timestamp` | yes | UTC bar-open time. ISO-8601 (`2026-01-02T08:00:00Z`) or epoch seconds. |
| `open` | yes | open price |
| `high` | yes | high price (must be ≥ `low`) |
| `low` | yes | low price |
| `close` | yes | close price (> 0) |
| `volume` | yes | bar volume (may be 0 if unavailable) |
| `spread` | optional | bid-ask spread in **price units** (e.g. `0.00012` for EUR/USD, `0.012` for JPY pairs). Used as the per-trade round-trip cost. |

Example (first lines):

```csv
timestamp,open,high,low,close,volume,spread
2026-01-02T08:00:00Z,1.10250,1.10310,1.10220,1.10290,1432,0.00011
2026-01-02T09:00:00Z,1.10290,1.10360,1.10270,1.10330,1551,0.00012
```

## Coverage requirements

- **Sorted/clean enough to pass `validate_ohlcv`** (the provider rejects files
  with too few clean rows; default minimum is 220 bars per file).
- For a walk-forward run you need enough history for warm-up **plus** the full
  in-sample + out-of-sample span. As a rule of thumb for `day_trading`, provide
  at least the style `lookback_bars` (420 H1 bars) of warm-up **before** the
  backtest start, on every timeframe.
- If `spread` is omitted, document the per-symbol spread assumption used instead
  (see the report’s “Realistic costs” section); the conservative SL-before-TP
  rule always applies.

## Acquisition (run LOCALLY — cloud network is blocked)

Use `scripts/fetch_real_data.py` (tested; uses the maintained `dukascopy-python`
library, which provides **bid + ask** → a real `spread = ask − bid`). The cloud
session cannot reach external data sources, so **run this on your machine**:

```bash
pip install dukascopy-python
cd apps/forex-scanner

# Default: EUR/USD + GBP/USD, H1/M15/M5, real spread from Dukascopy bid/ask.
python scripts/fetch_real_data.py --source dukascopy \
    --from-date 2019-01-01 --to-date 2026-01-01 --verbose
```

### How much history for ≥ 780 OOS trades?

The pre-registered minimum is **≥ 780 out-of-sample trades** (power analysis,
σ ≈ 1 R, to resolve ±0.10 R). Trade yield is sparse (order of a few OOS
trades per symbol-month at the analysis gate), so target **several pairs over
several years**. A safe starting point:

```bash
python scripts/fetch_real_data.py --source dukascopy \
    --symbols EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD USD/CAD \
    --timeframes H1 M15 M5 \
    --from-date 2016-01-01 --to-date 2026-01-01 --verbose
```

That writes 18 files (6 pairs × H1/M15/M5) spanning ~10 years. The script logs
`rows` and `coverage` per file; ensure each file comfortably exceeds the warm-up
(≥ 420 H1 bars before your backtest start) plus the walk-forward span. If the
final OOS trade count still falls below 780, add pairs/years rather than forcing
a conclusion — an underpowered run stays **NON-CONCLUSIVE**.

### HistData fallback (no spread)

If you instead have raw HistData "Generic ASCII" M1 files, drop them at
`data/raw_histdata/<SYMBOL>_M1.csv` (e.g. `EURUSD_M1.csv`) and aggregate:

```bash
python scripts/fetch_real_data.py --source histdata \
    --symbols EUR/USD --timeframes H1 M15 M5 \
    --from-date 2016-01-01 --to-date 2026-01-01 --histdata-tz Etc/GMT+5 --verbose
```

HistData has no bid/ask, so the `spread` column is left empty and the backtester
falls back to the documented per-symbol pip cost (conservative SL-before-TP
always applies).

Once the files are in place, the cloud session runs the validated harness
(`scripts/walk_forward_report.py --provider csv …` and
`scripts/score_expectancy_calibration.py --provider csv …`) for the first
real-data verdict.

## Why this matters

All edge validation so far ran on **synthetic** data (a random walk), which
cannot contain a real edge. Dropping real bars here is the single change that
makes the first real-data verdict possible. See
[`../../docs/edge_validation_report.md`](../../docs/edge_validation_report.md).
