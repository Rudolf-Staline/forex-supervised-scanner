# Symbol Health Report

Le script `scripts/symbol_health_report.py` génère un diagnostic **read-only** de la santé des symboles multi-asset.

## Entrées supportées
- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/multi_asset_signal_report_summary.json`
- `reports/readiness_report.json`

Le script tolère l'absence de fichiers.

## CLI
```bash
python scripts/symbol_health_report.py \
  --watchlist multi_asset_demo \
  --asset-class all \
  --export-json \
  --export-csv \
  --top-n 10
```

Options:
- `--watchlist multi_asset_demo`
- `--asset-class forex|commodities|indices|all`
- `--symbol SYMBOL`
- `--export-json`
- `--export-csv`
- `--top-n 10`

## Sorties
- `reports/symbol_health_summary.json`
- `reports/symbol_health_report.csv` (si `--export-csv`)

## Garanties sécurité
- Aucun appel MT5.
- Aucun envoi d'ordre.
- Aucun live trading.
- Aucun changement de mappings symboles, stratégie ou seuils.
- Les symboles dégradés sont signalés, jamais supprimés automatiquement.
