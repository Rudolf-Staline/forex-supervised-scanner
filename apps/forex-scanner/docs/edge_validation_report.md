# Edge validation report

Status: **integrity audited (Part A: OK)**; evidence on the only data available
(Part B); decomposition, power, verdict (Part C); **decisive gross-signal test and
action verdict (Part D).**

> Paper/demo only. No live trading, no `order_send`, no broker execution. This
> document does not constitute a performance guarantee or trading advice.

---

## DÉCISION — **NO-GO sur l'approche actuelle**

**Question décisive : existe-t-il un signal BRUT exploitable ?** → **Non.**

Test de signal sur le walk-forward existant (run canonique, le run à −0.1191 R,
n = 65 trades OOS) :

| | expectancy | IC bootstrap 95 % | significatif ? |
| --- | --- | --- | --- |
| **Brut** (hors coûts) | **+0.0285 R** | **[−0.0274, +0.0851]** | **non — IC inclut zéro** |
| Net (après coûts) | −0.1191 R | [−0.1742, −0.0639] | oui (négatif) |
| Coût moyen / trade | 0.1476 R | [0.1379, 0.1575] | — |

**L'expectancy brute n'est pas positive avec un IC excluant zéro** → cas
« brut ≈ 0 » → **pas de signal exploitable dans les features/données actuelles**.

> Pourquoi on ne retient PAS le run étendu (réduit) où le brut valait +0.1396
> [+0.0022, +0.2866] : ce run était sous-puissant (65 OOS au lieu des ≥300
> pré-enregistrés), a dévié de la pré-enregistration, **contredit** le run
> canonique (instabilité de signe), et sur une marche aléatoire tout « brut »
> positif ne capte que la **dérive artificielle du générateur**. Choisir ce
> chiffre favorable serait du p-hacking — explicitement interdit ici.

**Levier le plus susceptible de changer le résultat (descriptif, non implémenté) :
la QUALITÉ / DISPONIBILITÉ DES DONNÉES — pas de nouvelles features ni d'autres
instruments.** Justification : la seule donnée accessible ici est une marche
aléatoire synthétique (yfinance bloqué par l'allowlist, pas de MT5). Par
construction elle ne contient **aucune** structure prédictible ; donc de
nouvelles familles de features ou d'autres classes d'instruments **testées sur
cette même donnée** rendraient encore un brut ≈ 0. Tant qu'on ne dispose pas de
**données de marché réelles** (bars demo/broker avec spreads réalistes — MT5
local, ou un fournisseur historique mis en allowlist derrière l'interface
`MarketDataProvider` déjà pluggable), aucune hypothèse de signal n'est testable.
C'est le préalable n°1 ; ce n'est qu'ensuite que la question des features
devient décidable.

**Verdict d'action : NO-GO** sur la stratégie rules-based en l'état + le pipeline
de données actuel. Cela ne « réfute » pas l'hypothèse sur les marchés réels (on
ne l'a jamais testée sur une donnée pouvant contenir un edge) : cela signifie
qu'**aucun GO n'est justifié** — ne pas déployer ni passer en forward paper comme
si un edge était démontré. Le détail (intégrité, brut/net, puissance, instabilité)
est en Parties A–C ci-dessous.

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

---

## Part C — Why is it negative? Decomposition, power, and verdict

This section answers the deeper question raised by the −0.12 R OOS result. The
sub-parts map to the brief's A/B/C/D.

> Anti-p-hacking discipline followed: no subset was selected to flip the sign,
> no threshold was retuned on OOS data, and the extended config (C.3) was frozen
> in a script **before** it was run, with the minimum sample pre-registered.

### C.1 — Decomposing the −0.12 R: gross vs net (brief Part A)

Computed on the same 65 OOS trades, with bootstrap CIs:

| quantity | value | 95 % bootstrap CI |
| --- | --- | --- |
| **gross** expectancy (before costs) | **+0.0285 R** | **[−0.0274, +0.0851]** |
| **net** expectancy (after costs) | **−0.1191 R** | [−0.1742, −0.0639] |
| average cost / trade | **0.1476 R** | [0.1379, 0.1575] |

Exit reasons: **all 65 trades are `time_exit`** — none reached SL or TP inside the
holding window.

**Interpretation.** The gross CI **includes zero**: there is no statistically
detectable gross edge. The net loss is almost exactly the transaction cost
(−0.1191 ≈ 0.0285 − 0.1476). This is the brief's **first case: “gross ≈ 0 and net
< 0 ⇒ a cost / selectivity problem”** — the signal is **not anti-predictive**
(gross is not significantly negative), it simply carries **no gross edge**, and a
~0.15 R/trade spread cost turns flat into negative.

This is *exactly* what theory predicts on a **random walk** (the synthetic
generator): a driftless price series cannot be forecast, so any rules engine
nets ≈ 0 gross and loses the spread. The “all time-exit” pattern is the
mechanism: prices wander and rarely travel far enough to hit SL/TP within
`max_hold`, so realized P&L is tiny noise minus a fixed spread.

### C.2 — Where the loss concentrates (brief Part B, descriptive only)

> ⚠️ With 65 trades split many ways, **none** of these cells is large enough to
> support a selection decision. This is descriptive mechanism-finding only; it is
> **forbidden** (and would be p-hacking) to pick a “good” cell as a strategy.

**By fold** — the loss is broad, not a single outlier; the two well-populated
folds both lose, and gross is ≈ 0 in every fold:

| fold | n | gross | net | net CI |
| --- | --- | --- | --- | --- |
| 0 | 1 | +0.206 | +0.096 | (degenerate) |
| 1 | 2 | +0.041 | −0.064 | [−0.224, +0.096] |
| 2 | 17 | −0.019 | −0.146 | [−0.256, −0.038] |
| 3 | 45 | +0.042 | −0.116 | [−0.184, −0.051] |

**By symbol** (only 2 of 5 produced OOS trades), **session**, **regime**: in
nearly every cell **gross ≈ 0** (range −0.099 … +0.094) while **net is dragged
down by the uniform ~0.15 R cost**. The few “less bad” cells (e.g. asia/NY gross
≈ +0.09; weak_trend_down n=4) are small-sample noise with CIs spanning zero. The
uniformity of gross≈0 across partitions is the tell: this is a **structureless
series taxed by costs**, not a localized anti-signal.

### C.3 — Statistical power and the pre-registered extended re-run (brief Part C)

**C.3.1 Power analysis.** With observed net σ = 0.235 R, the trades needed to
detect an expectancy of magnitude Δ at α=0.05 (two-sided), power 0.80
(`n = (z_{α/2}+z_β)²σ²/Δ²`):

| Δ (R) | trades needed | 65 enough? |
| --- | --- | --- |
| 0.20 | 11 | yes |
| 0.15 | 20 | yes |
| 0.10 | 44 | yes |
| 0.05 | 173 | **no** |

So 65 trades **was** enough to declare the −0.12 R loss real (|Δ|>0.10), but
**not** enough to distinguish a small gross edge (±0.05 R) from zero. Caveat: this
σ (0.235) is unusually small precisely because every trade is a time-exit with
tiny P&L; on real data with SL/TP hits, σ ≈ 1 R, and detecting ±0.05 R would need
**~3,000+ trades**. Power requirements on real markets are far larger than here.

**C.3.2 Extended re-run.** Pre-registered config (frozen in `/tmp/edge_c.py`
before running; outputs under `reports/extended/`): 12 symbols (EUR/USD, GBP/USD,
USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/JPY, GBP/JPY, EUR/GBP, EUR/CHF,
AUD/JPY), day_trading, 2025-11-01→2026-06-01 (7 months), windows 45/21/14, gate
35, **pre-registered minimum ≥ 300 OOS trades**.

**Executed run (with a transparency note on deviation).** Because of sandbox
compute limits and a mid-run container restart, the run actually executed was a
**reduced** variant: 12 symbols (as above), **2026-02-01 → 2026-06-01 (4 months)**,
windows **35/21/14**, 5 folds. This deviates from the original pre-registration
(7 months, 45/21/14) and — critically — **did not reach the pre-registered ≥ 300
OOS minimum**: it produced 382 full-period trades but only **65 OOS trades** (4 of
the 12 symbols generated *zero* trades). The extended attempt is therefore **also
underpowered** and is reported as such, not as confirmation.

| metric | canonical (5 sym, 3.5 mo) | extended (12 sym, 4 mo) |
| --- | --- | --- |
| full-period trades | 282 | 382 |
| OOS trades | 65 | 65 (target ≥300 **not met**) |
| **gross** expectancy | +0.0285 [−0.027, +0.085] | **+0.1396 [+0.002, +0.287]** |
| **net** expectancy | **−0.1191 [−0.174, −0.064]** | **+0.0604 [−0.080, +0.212]** |
| avg cost / trade | 0.148 R | 0.079 R |
| net σ | 0.235 | 0.612 |
| Spearman(score, R) | −0.071 | +0.267 |
| profit factor | 0.30 | 1.29 |

**This table is the key result.** Every headline number **changed sign or swung
wildly** between two arbitrary synthetic configurations: net expectancy went from
significantly **negative** to **positive-but-CI-includes-zero**; gross from ≈0 to
marginally positive; the score↔outcome Spearman from −0.07 to +0.27. Per-cell
figures are pure noise — e.g. GBP/JPY net **+1.21 on n=2**, EUR/JPY +0.44 on n=6,
breakout_candidate +0.47 on n=10, while GBP/USD is −0.28 on n=17. None of this is
stable, and **neither run reached adequate statistical power**.

The instability is the signal: on a structureless (random-walk-plus-artificial-
drift) series, outcomes are dominated by *which seeds/symbols/dates* are included,
not by any repeatable edge.


### C.4 — Honest decision (brief Part D)

**Verdict: (iii) DATA-LIMITED — non-conclusive. The rules-based edge is neither
demonstrated nor refuted, because no data capable of settling it was available.**

Reasoning, strictly from the numbers above:

1. **The only data is uninformative by construction.** The synthetic feed is a
   random walk plus an *artificial* per-symbol drift. On it, theory says gross ≈ 0
   and net < 0 (cost). Any positive "edge" merely captures the generator's
   drift — an artifact, not real predictability. So no synthetic result, positive
   or negative, can validate a real edge.
2. **The results are not robust.** Across two arbitrary configurations every
   headline metric flipped or swung (net −0.119 → +0.060; gross +0.029 → +0.140;
   Spearman −0.07 → +0.27), with per-cell values dominated by 2–10-trade noise.
   This is the fingerprint of *no stable signal*.
3. **Power was never reached.** The pre-registered ≥ 300 OOS minimum was not met
   (65 in both runs); 4/12 symbols produced zero trades. At the artificially low
   synthetic σ, ±0.05 R needs ~173 trades; at a realistic σ ≈ 1 R it needs
   **~3,100** (±0.05 R) or **~780** (±0.10 R). We are 1–2 orders of magnitude short.
4. **Real FX data is blocked** (Yahoo allowlist 403; no MT5). The real test never ran.

Why not the other verdicts:

- **Not (i) “no edge — abandon.”** We cannot declare the hypothesis unsupported
  without ever testing it on data that *could* contain an edge. Claiming (i) here
  would be a category error.
- **Not (ii) “cost-limited” as a conclusion.** The canonical decomposition is
  *consistent* with cost-limitation (gross ≈ 0, net = gross − cost ≈ −0.15 R, all
  time-exits), but the extended run’s gross was not robustly positive, so we
  cannot assert a real gross edge destroyed by costs. (ii) is a **hypothesis to
  test on real data**, not a finding.

**What would settle it (pre-conditions for any real verdict):**

1. **Real data**: demo/broker bars with realistic per-symbol spreads — local MT5
   on Windows, or add an allowlisted historical FX vendor behind the existing
   pluggable `MarketDataProvider` (no engine change needed).
2. **Adequate, pre-registered sample**: size N from the power analysis (≈780
   trades to resolve ±0.10 R, ≈3,100 for ±0.05 R at σ≈1 R), fixed before looking.
3. **Run unchanged through the now-audited harness** (Part A guarantees no
   look-ahead, no OOS leakage, honest costs/metrics).
4. **One falsifiable selectivity hypothesis to test there (not applied now, to
   avoid p-hacking):** since cost-in-R = spread / planned-risk and the canonical
   run’s loss was ~0.15 R of pure cost on all-time-exit trades, require
   `planned_risk_distance ≥ k · spread` (e.g. reject when spread exceeds ~8 % of
   planned risk). Validate strictly OOS; keep or discard by its OOS expectancy CI.

**Bottom line.** The *machinery* is now trustworthy (Part A) and behaves exactly
as theory demands on a structureless series (Part C.1). But in this environment we
**cannot** make an edge claim about real FX markets in either direction. The
honest scientific output of this work is a **validated, leak-free evaluation
harness and a clear, pre-registered protocol** — not a profitable system, and not
a false declaration of edge.

---

## Summary of code changes, tests, and safety

- **Code changed this phase:** the only production change is the walk-forward
  train/test boundary hardening (`run_walk_forward` ends the in-sample segment 1 µs
  before the boundary so train/test ranges are disjoint by construction) plus its
  regression test — committed in `9cf81aa`. All other artifacts are **documentation
  and throwaway analysis scripts** (`/tmp`, not committed). No engine, scoring,
  risk, safety, or provider logic was altered for the experiments (the score-gate
  change used to widen the analysis sample was an in-memory `settings` override in a
  scratch script, never persisted).
- **Tests:** `python -m pytest` — see the run recorded alongside this commit;
  the audited modules (`test_walk_forward`, `test_backtest_activation`,
  `test_backtest_metrics`, `test_score_expectancy_calibration`, `test_indicators`)
  are green.
- **Safety guardrails: none weakened.** Still paper/demo only —
  `EXECUTION_MODE=paper`, `ALLOW_LIVE_TRADING=false`, autonomous policy,
  `ensure_demo_bot_safe_mode`, and the readiness gate are untouched; no `order_send`
  or broker-live path was added; scan/backtest parity is preserved.

