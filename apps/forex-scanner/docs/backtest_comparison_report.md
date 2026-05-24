# Backtest Comparison Report

Le comparateur de rapports permet de comparer un baseline et un candidate **sans exécuter de nouvelle stratégie**.

## Commande

```bash
python scripts/backtest_comparison_report.py --reports-dir reports --export-json --export-csv
```

Options utiles:
- `--baseline PATH`
- `--candidate PATH`
- `--top-n 10`

## Entrées supportées

- `reports/backtest_multi_asset_summary.json`
- `reports/backtest_multi_asset.csv`
- `reports/forward_test_summary.json`
- `reports/forward_test_paper.csv`
- `reports/paper_performance_summary.json`

## Sorties

- `reports/backtest_comparison_summary.json`
- `reports/backtest_comparison_report.csv`

## Sécurité

- Purement informatif.
- Aucun appel MT5.
- Aucun envoi d'ordre.
- Aucun backtest long.
- Aucun live trading.
- Message affiché:
  `Backtest comparison is not proof of future profitability.`
