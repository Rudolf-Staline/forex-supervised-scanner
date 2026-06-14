# Edge validation report

Status: **integrity audited (Part A: OK)**; evidence produced on the only data
available in this environment (Part B); honest interpretation (Part C).

> Paper/demo only. No live trading, no `order_send`, no broker execution. This
> document does not constitute a performance guarantee or trading advice.

---

## Part A — Integrity audit (do the fixes actually hold?)

The audit proves, code in hand, that the P1/P2 fixes are real and correct. Every
check below was executed in this session.

### A.1 — Swing look-ahead leak is closed

- The P1.1 regression test (`tests/test_indicators.py::
  test_causal_swings_have_no_look_ahead_leak`) passes.
- **Independent verification** (not relying on the committed test): on synthetic
  EUR/USD H1 data (2161 bars), I compared `swing_high`/`swing_low` on the slice
  `[:T]` of indicators computed over the full history against indicators
  recomputed **only** on data `<= T`, at three cut-offs (middle `idx 1080`,
  near-tail `idx 2155`, exact tail `idx 2160`):

  | T (idx) | swing_high NaN-pos mismatches | max value diff | swing_low |
  | --- | --- | --- | --- |
  | 1080/2160 | 0 | 0.0 | 0 / 0.0 |
  | 2155/2160 | 0 | 0.0 | 0 / 0.0 |
  | 2160/2160 | 0 | 0.0 | 0 / 0.0 |

- **Negative control** (proving the test is not vacuous): across ~50 cut-offs,
  the *causal* detector produced **0** slice-vs-recompute mismatches, while the
  *legacy centred* detector (`causal=False`) produced **34** — i.e. the old
  behaviour really did leak future bars, and the fix really removes it.

**A.1 verdict: leak closed.**

### A.2 — Walk-forward is watertight (no OOS leakage into tuning)

By code inspection of `app/backtest/walk_forward.py`:

1. **No parameter is tuned on the out-of-sample fold.** `select_min_score()` and
   `evaluate_fold()` only ever receive `in_sample_trades` for threshold
   selection; the OOS trades are scored *after*, with the already-chosen
   threshold. There is no path by which OOS data reaches the optimiser.
2. **No train/test overlap.** `generate_windows()` makes
   `out_of_sample_start == in_sample_end`. The Backtester treats date ranges as
   inclusive, which would share one boundary bar; `run_walk_forward()` now ends
   the in-sample segment **1 µs before** the boundary, so the two date ranges are
   disjoint *by construction* (regression test:
   `test_run_walk_forward_segments_are_disjoint_at_boundary`).
3. **No global normalization.** Metrics are computed per fold via
   `calculate_metrics()` on raw `net_r`; no scaler/standardizer is fit over the
   whole dataset, so there is no full-sample statistic leaking into folds.

**A.2 verdict: watertight.**

### A.3 — Entry activation excludes unfilled signals

- The P1.2 tests (`tests/test_backtest_activation.py`, 7 tests) pass.
- **Direct proof**: a LONG whose entry (1.1000) is never traded through (price
  gaps above and never returns) makes `_simulate_trade(...)` return `None`, so it
  is excluded from P&L — no assumed fill. Confirmed live in this session.

**A.3 verdict: unfilled signals are excluded, not silently filled.**

### A.4 — Metrics match a hand-computed reference

Reference net-R set `[2.0, -1.0, 3.0, -1.0, -1.0]`:

| metric | computed | hand-computed | ok |
| --- | --- | --- | --- |
| expectancy | 0.4000 | 0.4000 | ✅ |
| sharpe_per_trade | 0.2052 | 0.2052 | ✅ |
| sharpe_annualized (×√252) | 3.2574 | 3.2574 | ✅ |
| profit_factor | 1.6667 | 1.6667 | ✅ |
| max_drawdown_r | 2.0000 | 2.0000 | ✅ |
| win_rate | 40.00 | 40.00 | ✅ |
| median_r | -1.0000 | -1.0000 | ✅ |

`sharpe_like` (deprecated) = `sharpe_per_trade × √N` = 0.4588, confirming why it
inflates with sample size. The expectancy bootstrap CI on this tiny n=5 sample is
`[-1.0, 2.0]` — correctly **wide and including zero**, exactly what an honest CI
should do with five noisy trades.

**A.4 verdict: metrics are correct.**

### Verdict A — INTEGRITY OK

All four audits pass. The numbers downstream can be trusted *as computations*.
Whether they constitute an **edge** is a separate question, addressed below — and
gated by a hard environmental constraint described next.

---

## Part B — Evidence (with a decisive environmental caveat)

### B.0 — Data availability (read this first)

A genuine edge proof requires **real market data**. In this execution
environment that is **not available**:

- The Yahoo Finance hosts are blocked by the network egress allowlist:
  `HTTP 403: Host not in allowlist: query1/query2.finance.yahoo.com`. A real
  backtest over EUR/USD, GBP/USD, USD/JPY, AUD/USD returned **0 trades**, every
  symbol skipped with `DataProviderError: Yahoo Finance returned no data`.
- There is no MetaTrader5 terminal here (Windows-only), so the real-spread demo
  path is unavailable too.
- The **only** working provider is the **synthetic generator**
  (`SyntheticForexDataProvider`), whose prices are a deterministic
  `trend + sine + noise` formula. Any "edge" measured on it is an **artifact of
  that formula**, not evidence about real FX markets.

Consequently, Part B exercises the (now-audited) harness end-to-end on synthetic
data to validate the *methodology and the exported artifacts*, but its numbers
**cannot** establish a real-market edge. This is stated plainly and carried into
the Part C verdict.

<!-- PART_B_NUMBERS -->

<!-- PART_C_VERDICT -->
