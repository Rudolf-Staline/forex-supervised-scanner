# Shadow labeling for rejected decisions

## Purpose

The decision dataset is naturally selected: paper outcomes mostly exist for
candidates that passed scanner and bot gates. Rejected candidates usually have no
return label, so a supervised model trained only on observed orders can learn the
old policy rather than the economic value of the underlying setup.

Shadow labeling reduces this blind spot by replaying an unexecuted candidate
against later historical candles. It is research-only counterfactual evidence.
No order is created, no broker is contacted, and no runtime threshold is changed.

## Eligible population

The default scope is `rejected_unlabeled`:

- the row is a trainable setup candidate;
- no observed return label already exists;
- the scanner rejected it;
- direction is long or short;
- stored entry, stop, and target form a valid executable plan.

Use `--scope all_unlabeled` only when missing outcomes from accepted candidates
must also be reconstructed.

Rows with missing or inconsistent levels remain unlabeled and appear in
`shadow_label_failures.jsonl`.

## Historical replay

For each symbol and trigger timeframe, the labeler loads one shared candle frame.
Each candidate then uses:

- its stored decision timestamp;
- stored entry, stop, TP1, TP2, TP3, and final target;
- the style's configured transaction cost;
- the style's maximum hold measured in entry-timeframe bars and converted to
  trigger-timeframe bars;
- `app.backtest.execution.simulate_execution` for bid/ask-aware fills;
- the same conservative stop-first rule as the main backtester.

A terminal event on the activation candle, or a candle containing both stop and
target, is marked `intrabar_ambiguous=true`.

## Non-activation

When the entry is never reached during the complete holding window, the default
counterfactual return is `0R`:

- `shadow_status=not_activated`;
- `activated=false`;
- `realized_r=0`;
- `target_positive_r=false`;
- `target_non_negative_r=true`;
- `label_timestamp` is the end of the complete observation window.

This represents a candidate that consumed no capital because it never filled. The
value can be changed with `--not-activated-return-r`, but changing it creates a
different label contract and is recorded in the manifest.

## Outputs

The default command writes only separate artifacts:

- `shadow_decision_labels.jsonl`;
- `shadow_label_failures.jsonl`;
- `shadow_label_summary.json` and `.txt`;
- `shadow_label_manifest.json`.

The original decision dataset is never modified.

Passing `--write-augmented-dataset` additionally writes new copies:

- `decision_calibration_with_shadow.jsonl`;
- `decision_training_with_shadow.jsonl`;
- `shadow_merge_summary.json`.

Observed paper/backtest labels always take precedence during the merge.

## Recommended command

```bash
python scripts/build_shadow_labels.py \
  --dataset reports/decision_calibration/decision_calibration_dataset.jsonl \
  --provider csv \
  --csv-data-dir data/real \
  --scope rejected_unlabeled \
  --output-dir reports/shadow_labels \
  --write-augmented-dataset
```

Synthetic candles are blocked unless `--allow-synthetic` is supplied explicitly.
Synthetic labels are suitable only for tests, never strategy evidence.

## Training guidance

Shadow rows must remain identifiable through:

- `label_source=shadow_historical`;
- `shadow_counterfactual=true`;
- `shadow_label_id`;
- `shadow_execution_model`;
- `shadow_intrabar_ambiguous`.

Model reports should separate observed and shadow performance. A later training
stage may down-weight counterfactual rows, but this labeler does not choose a
weight and does not deploy a model.

## Limitations

- OHLC data cannot reveal the exact intrabar path.
- The simulation does not recreate portfolio-capacity rejections or margin use.
- Stored decision levels can reflect the rule engine's historical configuration.
- Corporate-quality tick reconstruction, swaps, commissions beyond configured
  spread costs, and partial-fill mechanics are outside this version.
- Counterfactual labels are weaker evidence than actually observed paper orders.

The system remains paper/research only. No live-trading authorization is added.
