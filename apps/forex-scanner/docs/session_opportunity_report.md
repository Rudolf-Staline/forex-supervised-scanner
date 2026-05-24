# Session Opportunity Report

## Purpose

`session_opportunity_report.py` provides an informational analysis of where stronger signal opportunities were observed by market session.  
This report is observational only and does **not** change scanner behavior.

**Mandatory safety statement:**

> Session analysis is informational and does not authorize execution.

## Inputs

The script will attempt to load the following files if they exist:

- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/backtest_multi_asset_summary.json`
- `reports/multi_asset_signal_report_summary.json`

If any file is missing, processing continues without crashing.

## CLI

```bash
python scripts/session_opportunity_report.py --asset-class all --export-json --export-csv
```

Options:

- `--asset-class forex|commodities|indices|all`
- `--symbol SYMBOL`
- `--export-json`
- `--export-csv`
- `--top-n 10`

## Outputs

- `reports/session_opportunity_summary.json`
- `reports/session_opportunity_report.csv` (when `--export-csv` is set)

The JSON summary contains:

- `total_records`
- `sessions_detected`
- `signals_by_session`
- `approved_by_session`
- `rejected_by_session`
- `average_score_by_session`
- `average_risk_reward_by_session`
- `average_spread_atr_by_session`
- `best_sessions_by_asset_class`
- `weakest_sessions_by_asset_class`
- `off_hours_count`
- `session_quality_status` (`HEALTHY`, `WARN`, `DEGRADED`, `BLOCKED`)
- `recommended_observation_windows`
- `safety_warning`

## Safety constraints

- Informational report only.
- Does not update session windows.
- Does not alter strategy or thresholds.
- Never authorizes automatic execution.
- Never sends orders.
