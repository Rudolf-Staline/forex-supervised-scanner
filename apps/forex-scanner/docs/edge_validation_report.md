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
  (`SyntheticForexDataProvider`). Inspecting the code, each symbol's price is a
  **per-symbol seeded random walk** (`close = base + cumsum(Gaussian shocks)`)
  plus a tiny constant drift and a seasonal sine. A random walk has, by
  construction, **no exploitable predictive structure**, so the theoretical
  expectation for *any* rules-based strategy on it is **gross expectancy ≈ 0**
  and **net expectancy < 0** (eaten by the spread/cost). Any "edge" measured on
  it is an artifact, not evidence about real FX markets. This fact is central to
  the verdict in Part C.

Consequently, Part B exercises the (now-audited) harness end-to-end on synthetic
data to validate the *methodology and the exported artifacts*, but its numbers
**cannot** establish a real-market edge. This is stated plainly and carried into
the Part C verdict.

### B.1 — Walk-forward run (exact configuration)

| Parameter | Value |
| --- | --- |
| Provider | synthetic (deterministic; **not** real FX) |
| Universe | EUR/USD, GBP/USD, USD/CHF, AUD/USD, USD/JPY |
| Style | day_trading (HTF H1 / entry M15 / trigger M5) |
| Period | 2026-02-15 → 2026-06-01 (~3.5 months) |
| Windows | in-sample 35 d, out-of-sample 21 d, step 14 d → **4 folds** |
| Score grid (tuned in-sample) | 0, 50, 55, 60, 65, 70, 75 |
| `min_in_sample_trades` | 5 |
| Engine score gate | lowered to 35 (all families) to widen the sample and the score range |
| Full-period trades | **282** |
| Aggregated OOS trades | **65** |

Method note: to keep cost down, one full-period backtest was computed and the
walk-forward `segment_runner` sliced its trades by entry date. After the P1.1
causal fix this is equivalent to per-segment runs (each bar uses only past data);
the sole difference is that the `blocked_until` cooldown is continuous across fold
boundaries. Exports: `reports/walk_forward.{json,txt}`.

Lowering the engine gate to 35 deliberately admits low-quality setups, which
biases expectancy **downward**; this is acceptable here because the goal is to
test whether the *score ranks* those setups, not to showcase a curated subset.

### B.2 — Score → expectancy calibration (OOS trades)

Exports: `reports/score_expectancy_calibration.{json,txt}`. Monotonic
(non-decreasing): **no**. Spearman(score, realized R) = **−0.0713**.

| decile | score range | n | expectancy (R) | win % | bootstrap CI |
| --- | --- | --- | --- | --- | --- |
| D1 | 60.0–61.3 | 7 | −0.064 | 42.9 | [−0.203, 0.059] |
| D2 | 61.3–62.3 | 6 | −0.029 | 33.3 | [−0.185, 0.152] |
| D3 | 62.7–63.5 | 7 | −0.124 | 42.9 | [−0.354, 0.097] |
| D4 | 63.5–64.5 | 6 | −0.048 | 50.0 | [−0.237, 0.140] |
| D5 | 64.7–65.0 | 7 | −0.222 | 28.6 | [−0.383, −0.067] |
| D6 | 65.3–66.9 | 6 | −0.167 | 16.7 | [−0.296, −0.018] |
| D7 | 66.9–67.5 | 7 | −0.225 | 28.6 | [−0.365, −0.080] |
| D8 | 67.6–68.6 | 6 | −0.025 | 50.0 | [−0.148, 0.106] |
| D9 | 68.6–69.7 | 7 | −0.272 | 0.0 | [−0.390, −0.154] |
| D10 | 70.1–72.1 | 6 | +0.037 | 66.7 | [−0.118, 0.192] |

The top decile (D10) is the only positive bucket, but its CI includes zero
(n=6) and D9 — the *second*-highest — is the **worst** bucket (0 % win rate).
There is no monotone climb; the curve is essentially flat-to-negative noise.

### B.3 — Out-of-sample synthesis

**Aggregate OOS (after costs):**

| metric | value |
| --- | --- |
| trades | 65 |
| expectancy / trade | **−0.1191 R** |
| expectancy bootstrap CI (95 %) | **[−0.1742, −0.0639]** |
| profit factor | 0.30 |
| win rate | 35.4 % |
| max drawdown | 7.93 R |
| Sharpe / trade | −0.51 |

**By symbol** (only 2 of 5 produced any OOS trades — severe concentration):

| symbol | n | expectancy | CI | win % | PF |
| --- | --- | --- | --- | --- | --- |
| USD/CHF | 34 | −0.071 | [−0.153, 0.014] | 44.1 | 0.54 |
| GBP/USD | 31 | −0.172 | [−0.239, −0.104] | 25.8 | 0.09 |
| EUR/USD, AUD/USD, USD/JPY | 0 | — | — | — | — |

**By session:** london −0.225 (n=19), off_hours −0.222 (n=5), new_york −0.062
(n=12), asia −0.050 (n=20), ny_overlap −0.069 (n=9). **By regime:** trending_up
−0.169 (n=28), trending_down −0.103 (n=29); the only positive cells are
weak_trend_down +0.079 (n=4) — too small to mean anything.

**Per fold** (threshold tuned in-sample, reported OOS):

| fold | tuned min_score | IS exp (n) | OOS exp (n) |
| --- | --- | --- | --- |
| 0 | 70 | +0.123 (11) | +0.096 (1) |
| 1 | 70 | +0.163 (7) | −0.064 (2) |
| 2 | 65 | −0.117 (17) | −0.146 (17) |
| 3 | 60 | −0.119 (52) | −0.116 (45) |

Folds 0–1 found a positive in-sample threshold but it generalized to almost no
OOS trades (1 and 2). Folds 2–3 could not find *any* positive-expectancy
threshold even **in-sample**, and the OOS result tracked the (negative) in-sample
result. The optimizer behaved correctly; there was simply nothing to optimize.



<!-- PART_C_VERDICT -->
