# Signal Quality Report

Script: `scripts/signal_quality_report.py`

## Purpose

Generate an **informational** quality report from existing signal outputs without changing strategy thresholds, config, or execution behavior.

It prints a readable console summary and can export:

- `reports/signal_quality_summary.json`
- `reports/signal_quality_report.csv`

## Inputs

The script consumes available files when present:

- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/forward_test_summary.json`
- `reports/multi_asset_signal_report_summary.json`

Missing files are handled gracefully.

## CLI options

- `--asset-class forex|commodities|indices|all`
- `--symbol SYMBOL`
- `--session SESSION`
- `--watchlist multi_asset_demo`
- `--min-score`
- `--export-csv`
- `--export-json`
- `--top-n 10`

## Near-miss definition

A signal is near-miss if one of these conditions is true:

- score is within 5 points below threshold
- risk/reward is within 0.2 below threshold
- spread/ATR is slightly above threshold (<= 0.05 over)
- status is `watchlist` or `detected` with score >= 70

## Safety rule

The script always includes the warning:

`Do not change thresholds without forward testing.`

No config mutation, no automatic threshold lowering, and no live-trading action.
