# Report Index

`report_index.py` construit un index central des rapports dans `reports/`.

## Commande

```bash
python scripts/report_index.py --show-missing --show-stale --export-json --export-txt
```

## Options

- `--reports-dir reports`
- `--export-json`
- `--export-txt`
- `--show-missing`
- `--show-stale`
- `--max-age-hours 48`

## Sorties

- `reports/report_index.json`
- `reports/report_index.txt`

## Garanties sécurité

- Aucun ordre envoyé.
- Aucun appel MT5.
- Aucune exécution de stratégie.
- Aucun changement de configuration.
