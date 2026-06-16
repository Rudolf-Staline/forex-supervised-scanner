# Real-data validation runbook

> Paper/demo only. No live trading, no `order_send`, no broker execution. This
> document is a reproducibility runbook, not a performance guarantee.

Heavy compute (fetch + walk-forward) runs **locally / in your Codespace** where
the network is open. The cloud session's allowlist blocks the data fetch, and the
raw CSVs are too large for git — so you run this, then push **only the reports**.

All commands run from `apps/forex-scanner/`. Use **tmux** for the long steps
(fetch + walk-forward) so they survive disconnects:

```bash
tmux new -s edge        # later: tmux attach -t edge  (detach with Ctrl-b d)
cd apps/forex-scanner
pip install dukascopy-python
```

The windows, score grid and `min_in_sample_trades` below are the **pre-registered**
values from `docs/edge_validation_report.md` — do not change them.

---

## Step 1 — Fetch real bars (Dukascopy bid/ask → real spread)

Freeze the universe and period here (example: 6 majors, 8 years). Trade yield is
sparse, so aim wide enough to clear the pre-registered **≥ 878 effective OOS
trades** (ideally ≥ 3 500). Add pairs/years rather than narrowing.

```bash
python scripts/fetch_real_data.py --source dukascopy \
    --symbols EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD USD/CAD \
    --timeframes H1 M15 M5 \
    --from-date 2016-01-01 --to-date 2024-01-01 --verbose
```

Writes `data/real/<SYMBOL>_<TF>.csv` (18 files). Check the logged `rows` and
`coverage` per file. (HistData fallback, no spread: `--source histdata` with raw
M1 in `data/raw_histdata/` — see `data/real/README.md`.)

---

## Step 2 — Walk-forward → deduplicated OOS registry (one engine pass)

```bash
python scripts/walk_forward_report.py --provider csv \
    --symbols EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD USD/CAD \
    --from-date 2016-01-01 --to-date 2024-01-01 \
    --in-sample-days 45 --out-of-sample-days 21 --step-days 14 \
    --score-grid 0,50,55,60,65,70,75 --min-in-sample-trades 8 \
    --output-dir reports/real --jobs $(nproc)
```

Produces `reports/real/walk_forward.{json,txt}` and the deduplicated registry
`reports/real/oos_trade_registry.csv` (`pair,timestamp,score,gross_r,net_r,exit_reason`).

### `--jobs N` — CPU parallelisation

| value | behaviour |
| --- | --- |
| `--jobs 1` | **Exact sequential path** (`run_walk_forward`). Identical to the historical baseline. |
| `--jobs N` (N>1) | `N` worker processes (`run_walk_forward_parallel`, `ProcessPoolExecutor`). |
| *(omitted)* | Default = all CPU cores (`os.cpu_count()`). |

**Parallel unit:** one *fold* (`WalkForwardWindow`). Folds are mutually independent
— each worker runs its own in-sample + out-of-sample backtests and tunes its
threshold on its own in-sample fold only. No cross-fold state. Folds are sorted
into canonical order (`fold_index`) after collection; aggregation, dedup, equity
curve and bootstrap IC are independent of worker finish order.

**Why processes, not threads:** CPU-bound work under the GIL; each worker builds
its own provider + `Backtester` from the picklable `AppSettings` → no large
DataFrames cross the process boundary.

**Equivalence guarantee:** `tests/test_walk_forward_parallel.py` asserts strict
value-by-value equality between `--jobs 1` and `--jobs >1`: OOS trade register
record-by-record, per-fold and aggregate metrics (including bootstrap IC), equity
curve. Verified with a real-engine spot check: JSON output byte-for-byte identical.

**Measured speedup** (EUR/USD, 2 folds, 4-core machine):

| jobs | wall time | speedup |
| --- | --- | --- |
| 1 | 163.9 s | 1.00× |
| 2 | 84.4 s | **1.94×** |

---

## Step 3 — Decomposition + calibration from the registry (no re-backtest)

```bash
python scripts/edge_decomposition.py \
    --registry reports/real/oos_trade_registry.csv \
    --output-dir reports/real --bootstrap-resamples 5000
```

Produces `reports/real/edge_decomposition.{json,txt}`: gross/net expectancy,
cost/trade, **grouped** bootstrap CIs (cluster-by-pair + temporal block — the
most conservative governs), effective sample size, per-pair metrics, first/second-
half robustness, and the registry-based score→expectancy calibration (monotonicity,
Spearman with CI, per-pair Spearman). This step is what the pre-registered decision
rule is applied to.

Optional full-period calibration artifact (re-backtests, not registry-based):

```bash
python scripts/score_expectancy_calibration.py --provider csv \
    --symbols EUR/USD GBP/USD USD/JPY USD/CHF AUD/USD USD/CAD \
    --from-date 2016-01-01 --to-date 2024-01-01 --output-dir reports/real
```

---

## Step 4 — Push ONLY the reports (never the CSVs)

`data/real/*.csv` and `reports/*` are gitignored. Push **only** the small report
artifacts with `-f`; do **not** push the multi-hundred-MB bar CSVs.

```bash
git add -f reports/real/walk_forward.json reports/real/walk_forward.txt \
           reports/real/oos_trade_registry.csv \
           reports/real/edge_decomposition.json reports/real/edge_decomposition.txt \
           reports/real/score_expectancy_calibration.json reports/real/score_expectancy_calibration.txt
git commit -m "data: real multi-pair OOS reports for edge validation"
git push origin feat/walk-forward-parallel-registry
# DO NOT: git add data/real/*.csv   (too large; gitignored on purpose)
```

Then tell me it's pushed. I will apply the **pre-registered decision rule**
(`docs/edge_validation_report.md`) unchanged and write the verdict (GO
CONDITIONNEL / NO-GO / NON-CONCLUANT) from the grouped CIs, per-pair, halves, and
calibration — no criterion moved a posteriori.

---

## Pre-registered configuration (do not change)

The methodology is pre-registered in `docs/edge_validation_report.md`. The
walk-forward windows (`45 / 21 / 14` days), the score grid, `min_in_sample_trades`,
the deduplication, and the bootstrap confidence-interval method are fixed. The
bootstrap RNG already uses a fixed seed (`np.random.default_rng(_BOOTSTRAP_SEED)`);
the synthetic provider seeds per `(symbol, timeframe)`. **No figure of the run
changes** — parallelisation changes only scheduling.
