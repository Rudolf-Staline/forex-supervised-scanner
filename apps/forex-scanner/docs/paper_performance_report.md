# Paper Performance Report

Script: `python scripts/paper_performance_report.py`

Ce rapport est **informatif uniquement**: aucun ordre n'est envoyé, aucun appel MT5 n'est effectué.

## Entrées supportées
- `reports/forward_test_paper.csv`
- `reports/forward_test_summary.json`
- `reports/paper_fill_report.csv`
- `reports/paper_fill_summary.json`
- `reports/signal_journal.jsonl`

Le script continue même si certains fichiers sont absents.

## Options CLI
- `--reports-dir reports`
- `--asset-class forex|commodities|indices|all`
- `--symbol SYMBOL`
- `--session SESSION`
- `--export-json`
- `--export-csv`
- `--top-n 10`

## Sorties
- `reports/paper_performance_summary.json`
- `reports/paper_performance_report.csv` (si `--export-csv`)

## Avertissement
`Paper performance is not proof of profitability.`
