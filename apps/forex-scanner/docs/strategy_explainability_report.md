# Strategy Explainability Report

`strategy_explainability_report.py` produit un rapport d'explicabilité basé sur les fichiers de reporting existants.

## Entrées
- `reports/signal_journal.jsonl`
- `reports/multi_asset_signal_report_summary.json`
- `reports/forward_test_paper.csv`

Le script continue même si un ou plusieurs fichiers sont absents.

## CLI
- `--asset-class forex|commodities|indices|all`
- `--symbol SYMBOL`
- `--status approved|premium|watchlist|detected|rejected|all`
- `--top-n 10`
- `--export-json`
- `--export-txt`

## Sorties
- `reports/strategy_explainability_summary.json`
- `reports/strategy_explainability_report.txt`

## Sécurité
Le rapport est descriptif uniquement et n'altère jamais la stratégie ou les seuils.

> This report explains decisions; it does not authorize execution.
