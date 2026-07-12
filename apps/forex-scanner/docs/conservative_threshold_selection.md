# Conservative threshold selection

The previous walk-forward selector maximized raw in-sample mean expectancy and
allowed a threshold to qualify with only five trades. That policy is useful as a
simple diagnostic, but it can select a small lucky cluster and turn noise into a
high-score trading rule.

The research CLI now uses a conservative policy by default.

## Candidate calculation

For every score threshold that retains at least `min_trades` in-sample trades:

1. calculate raw mean net expectancy `mean_R`;
2. calculate sample standard deviation and standard error;
3. shrink the mean toward a zero-edge prior:

   `shrunk_mean = n / (n + prior_trades) * mean_R`

4. calculate a one-sided lower confidence objective:

   `LCB = shrunk_mean - confidence_z * standard_error`

5. select the threshold with the highest LCB, preferring the higher threshold on
   exact ties.

If the best LCB is not above the configured minimum objective, the fold abstains.
The implementation represents abstention with a score threshold of `101`, above
the scanner's 0–100 score range, so no in-sample or OOS trade passes.

## Default controls

- minimum retained in-sample trades: `50`;
- shrinkage prior: `50` equivalent trades;
- one-sided confidence multiplier: `1.645`;
- minimum acceptable LCB: `0.0 R`;
- abstention: enabled.

These are intentionally conservative research defaults, not claims that the
resulting strategy is profitable.

## Artifacts

Every run writes:

- `threshold_selection.json`: full per-fold diagnostics for every eligible score
  threshold;
- `threshold_selection.txt`: compact audit of selected and abstained folds.

The diagnostics include sample size, raw expectancy, standard deviation,
standard error, shrinkage weight, shrunk expectancy, lower confidence bound, and
the final ranking objective.

## Default command

```bash
python scripts/walk_forward_report.py \
  --provider csv \
  --symbols AUD/USD EUR/USD GBP/USD USD/CAD USD/CHF USD/JPY \
  --style day_trading \
  --from-date 2019-02-15 \
  --to-date 2023-12-21 \
  --in-sample-days 45 \
  --out-of-sample-days 15 \
  --step-days 15 \
  --min-in-sample-trades 50 \
  --threshold-objective conservative_lcb \
  --shrinkage-trades 50 \
  --confidence-z 1.645 \
  --minimum-threshold-objective-r 0
```

## Legacy reproduction

To reproduce the old raw-mean selection policy as closely as possible:

```bash
python scripts/walk_forward_report.py \
  --threshold-objective mean_expectancy \
  --min-in-sample-trades 5 \
  --shrinkage-trades 0 \
  --confidence-z 0 \
  --no-threshold-abstention
```

This mode is retained for comparison only. It should not be used as the primary
promotion criterion because it does not penalize small or uncertain samples.

## Scope

The selector changes only the in-sample score threshold. It does not tune setup
rules, portfolio constraints, stop placement, targets, or execution assumptions.
Threshold selection remains upstream of OOS de-duplication and portfolio
allocation.
