# Local validation runner (safe)

Commande unique de validation locale sûre :

```bash
python scripts/local_validation.py --quick --provider synthetic --watchlist multi_asset_demo --export-report
```

Garanties de sécurité imposées aux sous-commandes :
- `EXECUTION_MODE=paper`
- `BROKER_MODE=paper`
- `ALLOW_LIVE_TRADING=false`
- `MT5_DEMO_ONLY=true`
- `ENABLE_DEMO_EXECUTION=false`
- `AUTO_BOT_ENABLED=false`
- `NOTIFICATIONS_ENABLED=false` (par défaut)

Modes :
- `--quick` : validation rapide cloud-safe
- `--full` : quick + vérifications étendues (si scripts présents)

Options :
- `--skip-tests`
- `--skip-scripts`
- `--export-report`
- `--provider synthetic|mt5`
- `--watchlist multi_asset_demo`

Sorties exportées :
- `reports/local_validation_summary.json`
- `reports/local_validation_summary.txt`

Les scripts optionnels absents sont marqués `skipped`.
Les indisponibilités MT5 en environnement cloud sont traitées en `warn` (non bloquant), sauf si exécution MT5 explicitement demandée via `--provider mt5`.
