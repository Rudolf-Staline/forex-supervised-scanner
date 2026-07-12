# Supervised meta-filter baseline

This component is the first genuine supervised benchmark in the repository. It is
not a replacement for the rules engine. The existing detector continues to generate
candidate setups; the supervised model asks a narrower question:

> Among candidates similar to those that received a known paper outcome, which ones
> are more likely to finish above 0R?

The component is research-only. It does not serialize a model, change a threshold,
rank live opportunities, submit an order, or authorize deployment.

## Model

The initial model is deliberately simple:

- L2-regularized logistic regression;
- median imputation and standardization for numeric features;
- most-frequent imputation and one-hot encoding for categorical features;
- balanced class weights by default;
- Platt scaling fitted only on an independent chronological calibration block.

A linear probabilistic baseline is preferable to a large tree ensemble at this
stage because its behavior is easier to audit, its coefficients are inspectable,
and it exposes whether the existing feature set carries any stable signal before
more flexible models are attempted.

## Feature contract

The model uses an explicit allowlist of decision-time fields.

Numeric features include:

- technical, execution, context, empirical, and final scores;
- pattern, activation, and invalidation quality;
- risk/reward and minimum required risk/reward;
- spread/ATR and data quality;
- static and adaptive threshold values.

Categorical features include:

- normalized symbol and trading style;
- setup family and subtype;
- direction, scanner status, session, and provider;
- higher, entry, and trigger regimes;
- score band.

Lifecycle fields are forbidden as features, including realized R, outcome, order
status, activation, MAE, MFE, time in trade, and all target columns.

## Temporal validation

Every fold follows the same chronology:

1. expanding historical training set;
2. a more recent independent calibration block;
3. an embargo;
4. a strictly future test block.

A pre-test row is allowed into training or calibration only if its label was already
knowable before the test began. When an explicit `label_timestamp` is unavailable,
the evaluator estimates availability from:

```text
decision_timestamp + time_in_trade_minutes
```

Rows lacking both receive a configurable conservative delay. This is safer than
assuming the result was known immediately.

Test blocks are disjoint, so each decision receives at most one out-of-fold
prediction.

## Probability calibration

The base logistic model is fitted on the training block. Its probabilities on the
later calibration block are transformed to logits, then a second one-dimensional
logistic regression learns the Platt mapping. The test block is never used to fit
either stage.

When a calibration block contains only one class, probabilities remain uncalibrated
and the fold records `identity_single_class_calibration` rather than fitting an
invalid calibrator.

## Comparison with the current score

Every OOF prediction is evaluated against the existing manual score baseline:

```text
score_baseline_probability = final_score / 100
```

The report compares:

- ROC AUC;
- average precision;
- Brier score;
- log loss;
- accuracy at 0.5;
- expected calibration error;
- probability/target Spearman correlation;
- realized expectancy at the top 10%, 25%, 50%, and 100% coverage.

The evidence screen passes only if the supervised model simultaneously improves
Brier score, ROC AUC, and top-quartile realized expectancy relative to the full OOF
sample. Passing that screen still does **not** authorize deployment.

## Command

First build the decision dataset:

```bash
cd apps/forex-scanner
python scripts/build_decision_calibration_dataset.py
```

Then install the optional ML dependency and run the benchmark:

```bash
python -m pip install -e '.[dev,ml]'
python scripts/train_supervised_baseline.py
```

A smaller smoke-test configuration can be run with explicit row counts:

```bash
python scripts/train_supervised_baseline.py \
  --min-train-rows 80 \
  --calibration-rows 20 \
  --test-rows 20 \
  --step-rows 20 \
  --minimum-class-count 5 \
  --minimum-oof-rows 40
```

## Outputs

- `supervised_baseline_report.json` and `.txt`;
- `supervised_oof_predictions.jsonl` and `.csv`;
- `supervised_temporal_folds.json`;
- `supervised_coefficient_stability.csv`;
- `supervised_experiment_manifest.json`.

No `.pkl`, `.joblib`, ONNX, or other model binary is written.

The manifest records the dataset and dataset-manifest hashes, scikit-learn version,
feature allowlists, configuration, fold boundaries, purge cutoff, and the explicit
fact that deployment is unauthorized.

## Known limitations

1. Return labels currently come mainly from candidates that reached paper execution.
   This creates selection bias: the model has limited evidence about rejected
   candidates.
2. Rejected decisions are correctly retained in the complete calibration dataset,
   but are not used as negative return labels.
3. Paper outcomes reflect simulator assumptions, not guaranteed broker fills.
4. Row-count folds are useful for repeatable development but do not replace a final
   preregistered calendar holdout.
5. Coefficients can be unstable when categories are rare or the labeled sample is
   small.
6. A strong OOF result can still be backtest overfitting if many experiments are
   tried and only the best is reported.

## Required next gates

Before any model can influence paper decisions:

- obtain substantially more shadow/paper labels across all setup families;
- lock an untouched calendar holdout;
- compare performance in both temporal halves and across pairs;
- verify probability calibration out of fold;
- route predictions through the portfolio allocator;
- preregister the promotion criteria and experiment count;
- complete forward paper validation without changing the model.
