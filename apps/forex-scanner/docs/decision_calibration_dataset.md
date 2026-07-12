# Decision calibration dataset

This dataset is the bridge between the current rules engine and a future supervised
meta-filter. The rules engine remains responsible for generating candidate setups.
The dataset records what the system knew at decision time and, separately, what
happened later in paper execution.

It is reporting and research infrastructure only. It does not train a model, change
thresholds, submit orders, or enable live trading.

## Sources

The builder combines local evidence without mutating it:

1. `scan_results` — full scanner opportunity snapshots, including accepted and
   rejected raw candidates;
2. `reports/signal_journal.jsonl` — demo-bot cycle IDs, acceptance decisions, and
   order IDs;
3. `rejected_signals` — structured bot-level rejection reasons;
4. `paper_orders` — activation state and realized paper outcomes.

`scan_results` is the preferred feature source because it contains the complete
score decomposition, market context, gate diagnostics, levels, spread, ATR, and
data-quality snapshot.

## Leakage boundary

Fields are divided explicitly into two groups.

### Features

Features are values available at the scanner decision timestamp, such as:

- symbol, style, setup family and subtype;
- direction, session, and market regimes;
- technical, execution, context, empirical, pattern, and final scores;
- risk/reward and executable levels;
- spread, ATR, spread/ATR, and data quality;
- activation and invalidation quality;
- gate breakdown, failed gates, missing conditions, and threshold metadata.

A SHA-256 `feature_snapshot_hash` is stored for every row. Labels are excluded from
that hash.

### Labels

Labels come only from later lifecycle evidence:

- whether the paper order activated;
- terminal order status;
- realized R and realized P&L;
- positive/non-negative R targets;
- MAE, MFE, bars to activation, and time in trade;
- close reason and normalized outcome status.

Do not move lifecycle fields into the feature set.

## Matching policy

Paper outcomes are attached conservatively in this order:

1. exact `source_opportunity_id` to decision ID;
2. exact order ID recorded in the signal journal;
3. exact demo-bot `cycle_id` plus normalized symbol;
4. unique nearest compatible decision inside `--match-window-seconds`.

Compatibility checks symbol, setup family, setup subtype, and direction when those
fields exist. Temporal fallback is accepted only when the nearest candidate is
unique. Equal-distance matches are not guessed: they are written to
`ambiguous_decision_matches.jsonl` and remain unlabeled.

Orders with no compatible decision are written to `unmatched_paper_orders.jsonl`.

## Rejected decisions are not losses

A rejected decision usually has no counterfactual market outcome. The builder keeps
its features and rejection diagnostics but does **not** assign `realized_r = -1` or
`target_positive_r = false`.

Rejected rows are useful for:

- understanding scanner and bot selectivity;
- diagnosing which gates dominate;
- checking class and score-band coverage;
- identifying candidates that need future shadow labeling.

They are not valid return labels unless a separate causal shadow-execution process
resolves the same candidate.

## Outputs

Running the builder writes:

- `decision_calibration_dataset.jsonl` — all decisions and available labels;
- `decision_training_dataset.jsonl` — trainable candidates with a known return
  label;
- `decision_calibration_dataset.csv` — flat inspection table;
- `decision_calibration_summary.json` and `.txt` — coverage and label counts;
- `decision_calibration_manifest.json` — source hashes, schema, feature/label
  columns, and build configuration;
- `unmatched_paper_orders.jsonl` — orders that could not be linked;
- `ambiguous_decision_matches.jsonl` — deliberately unresolved matches;
- `malformed_calibration_sources.jsonl` — invalid source records that were skipped
  or recovered through scalar columns.

## Command

```bash
cd apps/forex-scanner
python scripts/build_decision_calibration_dataset.py
```

Explicit example:

```bash
python scripts/build_decision_calibration_dataset.py \
  --database data/forex_scanner.db \
  --signal-journal reports/signal_journal.jsonl \
  --output-dir reports/decision_calibration \
  --match-window-seconds 300
```

The configured database path is used when `--database` is omitted. A missing signal
journal is allowed; direct database evidence is still exported.

## Model-development guidance

- Split chronologically. Random row splits are forbidden because nearby decisions
  share market state and may overlap in time.
- Use purge/embargo when label horizons overlap the validation boundary.
- Begin with separate models or at least interactions for trend continuation,
  breakout, and mean reversion.
- Compare a regularized logistic baseline with a small tree-based model before any
  neural architecture.
- Calibrate probabilities only from out-of-fold predictions.
- Preserve an untouched holdout period and record every experiment against the
  manifest hashes.
- Evaluate both candidate-level ranking and portfolio-constrained performance.

## Current limitations

- Rejected signals are not counterfactually simulated.
- Temporal fallback can only use persisted timestamps and fields; ambiguous cases
  intentionally remain unmatched.
- Signal-journal rows that cannot be enriched may contain fewer features and are
  excluded from the training subset unless they are sufficiently identified and
  labeled.
- This dataset reflects paper execution assumptions, not guaranteed broker fills.
- No model is promoted or deployed by this builder.
