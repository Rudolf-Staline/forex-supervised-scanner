# Asset Concentration Report

Script: `python scripts/asset_concentration_report.py`

## Purpose

Provide an informational view of how signals are distributed across symbols, asset classes, and sessions.

> Concentration analysis is informational and does not authorize execution.

This report is read-only and safety-focused:
- no MT5 call,
- no order routing,
- no live trading activation,
- no watchlist mutation,
- no automatic symbol removal.

## Inputs

The script reads local artifacts from `--reports-dir` when present:
- `signal_journal.jsonl`
- `forward_test_paper.csv`
- `risk_exposure_summary.json`
- `multi_asset_signal_report_summary.json`
- `symbol_health_summary.json`

Missing files are tolerated and surfaced in the output.

## CLI

```bash
python scripts/asset_concentration_report.py \
  --reports-dir reports \
  --asset-class all \
  --top-n 10 \
  --export-json \
  --export-csv
```

`--asset-class`: `forex|commodities|indices|all`

## Outputs

- `reports/asset_concentration_summary.json`
- `reports/asset_concentration_report.csv`

JSON summary fields:
- `total_records`
- `concentration_by_asset_class`
- `concentration_by_symbol`
- `concentration_by_session`
- `top_symbols_by_signal_count`
- `top_symbols_by_executable_count`
- `rejected_concentration_by_symbol`
- `approved_concentration_by_symbol`
- `overrepresented_symbols`
- `underrepresented_symbols`
- `concentration_risk_status` (`LOW`, `MODERATE`, `HIGH`, `INSUFFICIENT_DATA`)
- `recommendations`
- `safety_warning`
