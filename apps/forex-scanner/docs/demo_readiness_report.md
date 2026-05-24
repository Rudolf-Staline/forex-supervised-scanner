# Demo Readiness Aggregator (Limited)

Le script `scripts/demo_readiness_report.py` agrège les rapports existants pour une validation **read-only**.

## Contraintes de sécurité

- N'active jamais `ENABLE_DEMO_EXECUTION`.
- N'active jamais le live trading.
- Ne modifie pas `.env`.
- N'appelle pas MT5.
- N'envoie aucun ordre.
- Statut maximal autorisé: `DEMO_PRECHECK_ONLY`.
- Le rapport affiche: `This report does not authorize order execution.`

## Entrées supportées

- `reports/readiness_report.json`
- `reports/safety_env_doctor.json`
- `reports/config_profile_validation.json`
- `reports/mt5_readonly_validation.json`
- `reports/data_health_report.json`
- `reports/report_index.json`
- `reports/local_validation_summary.json`
- `reports/risk_exposure_summary.json`
- `reports/symbol_health_summary.json`

## Commande

```bash
python scripts/demo_readiness_report.py --reports-dir reports --export-json --export-txt --strict
```

## Sorties

- `reports/demo_readiness_summary.json`
- `reports/demo_readiness_report.txt`
