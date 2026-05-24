# Watchlist Coverage Report

Script read-only pour mesurer la couverture réelle d'une watchlist dans les artefacts de reporting.

## Commande

```bash
python scripts/watchlist_coverage_report.py --watchlist multi_asset_demo --asset-class all --export-json --export-csv
```

## Entrées analysées

- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/multi_asset_signal_report_summary.json`
- `reports/symbol_health_summary.json`

Si la watchlist de configuration n'est pas trouvée, le script utilise un fallback en lecture seule pour `multi_asset_demo`.

## Sorties

- `reports/watchlist_coverage_summary.json`
- `reports/watchlist_coverage_report.csv`

## Champs clés

- `expected_symbols`
- `observed_symbols`
- `missing_symbols`
- `extra_symbols`
- `coverage_percentage`
- `coverage_by_asset_class`
- `observed_by_session`
- `symbols_without_recent_data`
- `symbols_with_repeated_rejections`
- `recommended_manual_checks`
- `coverage_status` (`FULL`, `PARTIAL`, `LOW`, `NO_DATA`)
- `safety_warning`

## Sécurité

Ce script ne modifie pas les watchlists, n'appelle pas MT5, n'envoie aucun ordre, et n'active jamais le live trading.
