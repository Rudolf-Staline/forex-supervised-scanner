# Locked calendar holdout

This workflow provides the final research checkpoint after temporal OOF validation.
It does not train a deployable model and it cannot change scanner thresholds.

## Why a separate holdout exists

Walk-forward and OOF results are still part of model development. Features, label
weights, calibration policy, and hyperparameters can be influenced by those results.
A locked holdout must therefore be a calendar period whose rules are fixed before its
outcomes are evaluated.

## Two-step workflow

### 1. Freeze the plan

A strict plan must be written before `holdout_start`:

```bash
python scripts/locked_calendar_holdout.py freeze \
  --holdout-start 2026-08-01T00:00:00Z \
  --holdout-end 2026-10-01T00:00:00Z \
  --plan reports/locked_holdout/holdout_plan.json
```

The plan freezes:

- inclusive start and exclusive end timestamps;
- the exact decision-time feature allowlist;
- forbidden lifecycle and label fields;
- observed and shadow sample weights;
- calibration-row count;
- embargo duration;
- minimum observed evidence requirements;
- regularization and calibration settings;
- coverage and evidence-gate policy.

The plan contains no target metrics. It is protected by a SHA-256 hash and is never
overwritten by the CLI.

### 2. Evaluate once

After the calendar window has ended and labels are available:

```bash
python scripts/locked_calendar_holdout.py evaluate \
  --dataset reports/decision_calibration/decision_calibration_with_shadow.jsonl \
  --plan reports/locked_holdout/holdout_plan.json \
  --output-dir reports/locked_holdout
```

The evaluator:

1. verifies the plan hash and feature-contract hash;
2. applies the preregistered calendar selection rule;
3. removes every pre-holdout row whose label was unavailable at the cutoff;
4. applies the preregistered embargo;
5. reserves the most recent eligible pre-holdout rows for Platt calibration;
6. fits the weighted logistic model on older data only;
7. scores the locked period once;
8. computes the promotion screen only on observed holdout outcomes;
9. writes a local single-use receipt.

If the receipt already exists, the CLI refuses a second evaluation by default.
Deleting the receipt can bypass this operational safeguard, so durable review should
also preserve the plan and receipt in an immutable artifact store or release record.

## Evidence policy

Shadow labels may supplement model fitting at their preregistered lower weight. They
are excluded from the final promotion evidence gate. The locked holdout requires
minimum observed row counts and class support.

The holdout is never used to choose:

- features;
- label-source weights;
- thresholds;
- regularization;
- calibration method;
- evidence criteria.

No holdout result authorizes deployment automatically. Forward paper evidence and
manual review remain mandatory.

## Historical dry runs

Use `--historical-dry-run` only to validate the software pipeline against a past
period. The report is forced to `historical_dry_run_no_promotion`, even when metrics
look favorable.

## Outputs

- `holdout_plan.json`;
- `locked_holdout_report.json` / `.txt`;
- `locked_holdout_predictions.jsonl` / `.csv`;
- `locked_holdout_training_audit.json`;
- `locked_holdout_manifest.json`;
- `locked_holdout_evaluation_receipt.json`.

No `.pkl`, `.joblib`, ONNX, or other model binary is written.
