# Real-data validation runbook — walk-forward

> Paper/demo only. No live trading, no `order_send`, no broker execution. This
> document is a reproducibility runbook, not a performance guarantee.

This runbook covers running the pre-registered walk-forward / out-of-sample
analysis on real data (`--provider csv`), including the CPU parallelisation flag
`--jobs`.

## Pre-registered configuration (do not change)

The methodology is pre-registered in `docs/edge_validation_report.md`. The
walk-forward windows (`45 / 21 / 14` days), the score grid, `min_in_sample_trades`,
the deduplication, and the bootstrap confidence-interval method are fixed. **No
figure of the run changes** — parallelisation only changes scheduling.

## Running the walk-forward

```
python scripts/walk_forward_report.py --provider csv --symbols EUR/USD \
    --from-date 2023-01-01 --to-date 2024-05-31 \
    --in-sample-days 45 --out-of-sample-days 21 --step-days 14 \
    --score-grid 0,50,55,60,65,70,75 --min-in-sample-trades 8 \
    --output-dir reports/real --jobs $(nproc)
```

For the full 6-pair scenario, pass all symbols (each pair runs inside the same
folds; folds are the parallel unit):

```
python scripts/walk_forward_report.py --provider csv \
    --symbols EUR/USD GBP/USD USD/CHF USD/JPY AUD/USD USD/CAD \
    --from-date 2019-01-01 --to-date 2024-01-01 \
    --in-sample-days 45 --out-of-sample-days 21 --step-days 14 \
    --score-grid 0,50,55,60,65,70,75 --min-in-sample-trades 8 \
    --output-dir reports/real --jobs $(nproc)
```

## `--jobs N` — CPU parallelisation

| value | behaviour |
| --- | --- |
| `--jobs 1` | **Exact sequential path** (`run_walk_forward`). Identical to the historical baseline. |
| `--jobs N` (N>1) | `N` worker processes (`run_walk_forward_parallel`, `ProcessPoolExecutor`). |
| *(omitted)* | Default = all CPU cores (`os.cpu_count()`). |

**Parallel unit:** one *fold* (one `WalkForwardWindow`). Folds are mutually
independent — each runs its own in-sample + out-of-sample backtests and tunes its
threshold using **only** its own in-sample fold. No cross-fold state.

**What stays centralised (on the parent process):** the reassembly. Folds are
sorted into canonical order (`fold_index`) before aggregation; the aggregate
trade pool is then sorted by `exit_time` exactly as the sequential path does. This
makes the deduplication, equity curve, aggregate metrics and bootstrap IC
**independent of which worker finishes first**.

**Why processes, not threads:** the work is CPU-bound (pandas/indicator loops
under the GIL); threads would not help. Each worker builds its **own** provider
and `Backtester` from the (picklable) `AppSettings` and reads its own data
locally, so no large DataFrames are pickled across the process boundary.

**Determinism / RNG:** the only RNG is the expectancy bootstrap in
`app/backtest/metrics.py`, which already uses a fixed seed
(`np.random.default_rng(_BOOTSTRAP_SEED)`); the synthetic provider seeds per
`(symbol, timeframe)`. Reproducibility is therefore independent of `--jobs`.

## Equivalence guarantee (proven)

The sequential and parallel paths share the same per-fold body
(`_evaluate_single_fold`) and the same aggregation code, so output is identical by
construction. This is enforced by an automated test:

```
pytest tests/test_walk_forward_parallel.py
```

`test_parallel_walk_forward_equals_sequential` asserts strict, value-by-value
equality between `--jobs 1` and `--jobs >1`: per-fold OOS trade register
(record-by-record), selected min-score, in/out-of-sample trade counts and
expectancy, per-fold OOS metrics, aggregate metrics (including the bootstrap
confidence interval) and the OOS equity curve.

A real-engine spot check (synthetic provider, 2 folds, EUR/USD) confirmed the
JSON report is **byte-for-byte identical** between `--jobs 1` and `--jobs 2`.

## Measured speedup

Real `Backtester` + synthetic provider, EUR/USD, `IS=20 / OOS=10 / step=10`,
40-day range (2 folds), on a 4-core machine:

| jobs | wall time | speedup | report JSON |
| --- | --- | --- | --- |
| 1 | 163.9 s | 1.00× | baseline |
| 2 | 84.4 s | **1.94×** | identical to `--jobs 1` |

Speedup scales with the number of folds up to the number of cores; the 6-pair ×
5-year run (dozens of folds) benefits proportionally to `min(folds, cores)`.
