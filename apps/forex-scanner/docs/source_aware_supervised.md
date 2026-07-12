# Source-aware supervised evaluation

## Why a separate benchmark exists

Observed paper outcomes and historical shadow outcomes do not carry the same
strength of evidence.

- **Observed labels** come from persisted paper/backtest lifecycles.
- **Shadow labels** are counterfactual replays of candidates that were not actually
  executed.

The shadow population is useful because it reduces selection bias, but treating it
as equivalent to observed fills can produce a convincing model that only learns
the assumptions of the simulator. The source-aware benchmark therefore separates
learning assistance from promotion evidence.

## Default policy

```text
observed training weight = 1.00
shadow training weight   = 0.35
promotion evidence       = observed OOF rows only
```

The weight is passed to both:

1. the L2-regularized logistic classifier;
2. the independent temporal Platt calibrator.

Scikit-learn combines the configured sample weights with class balancing when
class balancing is enabled.

## Observed anchors in every fold

A candidate temporal fold is retained only when it contains at least:

- 20 observed training rows;
- 5 observed calibration rows;
- 5 observed test rows;
- both observed classes in training;
- both observed classes in calibration.

All ordinary label-availability purges and the time embargo still apply. This
means a large all-shadow dataset cannot create a valid fold.

The minimums are configurable, but reducing them weakens the evidence contract and
is recorded in the experiment manifest.

## Metrics

The report exposes three views:

- overall mixed-source diagnostics;
- observed-only OOF metrics;
- shadow-only OOF metrics.

The following are calculated separately for observed and shadow rows:

- ROC AUC;
- average precision;
- Brier score;
- log loss;
- calibration error;
- accuracy at 0.5;
- realized expectancy.

Coverage and ranking expectancy used by the evidence gate are calculated on
observed OOF predictions only.

## Evidence gate

The promotion screen receives no shadow test rows. It requires:

- the configured minimum number of observed OOF predictions;
- lower observed Brier score than `final_score / 100`;
- higher observed ROC AUC than `final_score / 100`;
- better observed top-quartile expectancy than the full observed sample.

Even when all checks pass:

```text
deployment_authorized=false
promotion_authorized=false
automatic_threshold_update=false
```

The report remains research evidence, not a deployable model.

## Command

```bash
python scripts/train_source_aware_baseline.py \
  --dataset reports/shadow_labels/decision_training_with_shadow.jsonl \
  --observed-label-weight 1.0 \
  --shadow-label-weight 0.35 \
  --minimum-observed-oof-rows 40 \
  --output-dir reports/source_aware_supervised
```

The CLI never writes a model binary. It exports only:

- `source_aware_supervised_report.json` and `.txt`;
- `source_aware_oof_predictions.jsonl` and `.csv`;
- `source_aware_temporal_folds.json`;
- `source_aware_coefficient_stability.csv`;
- `source_aware_experiment_manifest.json`.

## Interpretation

A useful result has three properties:

1. the mixed-source model learns from a broader decision population;
2. performance remains positive when inspected on observed OOF rows alone;
3. the observed-only result improves on the current manual score baseline.

Strong shadow metrics with weak observed metrics indicate simulator agreement, not
validated market edge.

## Remaining limitations

- Observed paper fills remain approximations of broker execution.
- Shadow labels inherit OHLC path ambiguity and transaction-cost assumptions.
- Source weighting is a research hyperparameter and must not be optimized on the
  final test set.
- Row-count folds do not replace a locked calendar holdout.
- Forward paper evidence is still mandatory before any promotion discussion.

No live trading, model deployment, broker submission, or automatic threshold
change is enabled.
