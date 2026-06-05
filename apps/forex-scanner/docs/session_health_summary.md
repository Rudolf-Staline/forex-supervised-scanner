# Session Health Summary

`session_health_summary.py` builds a read-only health summary for observed market sessions from local paper/report artifacts.

## Command

```bash
python scripts/session_health_summary.py --export-json --export-csv
```

## Inputs

- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/backtest_multi_asset_summary.json`
- `reports/multi_asset_signal_report_summary.json`

Missing files are treated as empty inputs.

## Outputs

- `reports/session_health_summary.json`
- `reports/session_health_summary.csv`

## Safety guarantees

- Read-only analysis only.
- No broker calls.
- No MT5 calls.
- No `order_send` usage.
- No `.env` or environment mutation.
- Blocked/off-hours sessions remain blocked rather than permissive.
