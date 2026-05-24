# Failure Diagnostics Report

Le script `scripts/failure_diagnostics_report.py` produit un diagnostic rapide des échecs à partir des artefacts déjà présents dans `reports/`.

## Entrées lues

- `reports/local_validation_summary.json`
- `reports/post_merge_audit.json`
- `reports/report_index.json`
- `reports/repository_maintenance_audit.json`
- `reports/readiness_report.json`
- `reports/*.txt`

## Utilisation

```bash
python scripts/failure_diagnostics_report.py --export-json --export-txt --show-suggestions
```

Options disponibles :

- `--reports-dir reports`
- `--export-json`
- `--export-txt`
- `--show-suggestions`

## Sorties

- `reports/failure_diagnostics_summary.json`
- `reports/failure_diagnostics_report.txt`

## Garanties de sécurité

- Lecture seule des rapports d'entrée.
- Aucun relancement de stratégie.
- Aucun test long relancé automatiquement.
- Aucun appel MT5.
- Aucun envoi d'ordre.
- Aucune autorisation de live trading.
