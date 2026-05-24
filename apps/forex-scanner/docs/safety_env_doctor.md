# Safety Environment Doctor

` safety_env_doctor.py ` fournit un diagnostic lisible des variables de sécurité, sans modifier l'environnement et sans envoyer d'ordre.

## Commande

```bash
python scripts/safety_env_doctor.py --mode paper --export-json --export-txt
```

Modes supportés :
- `paper`
- `mt5-readonly`
- `mt5-demo-precheck`

Options :
- `--export-json` écrit `reports/safety_env_doctor.json`
- `--export-txt` écrit `reports/safety_env_doctor.txt`

## Statuts globaux

- `SAFE_PAPER`
- `SAFE_READONLY_DEMO`
- `BLOCKED`
- `DANGEROUS`

## Règles de sécurité

1. `ALLOW_LIVE_TRADING=true` => `DANGEROUS`
2. `ENABLE_DEMO_EXECUTION=true` => `BLOCKED` sauf en `mt5-demo-precheck`
3. `BROKER_MODE=paper` + `EXECUTION_MODE=paper` + `ALLOW_LIVE_TRADING=false` => `SAFE_PAPER`
4. `MT5_DEMO_ONLY=false` bloque les modes MT5
5. Aucune variable n'est modifiée
6. Aucun chargement MT5 ni envoi d'ordre

## Variables inspectées

- `EXECUTION_MODE`
- `BROKER_MODE`
- `ALLOW_LIVE_TRADING`
- `MT5_DEMO_ONLY`
- `ENABLE_DEMO_EXECUTION`
- `AUTO_BOT_ENABLED`
- `ALLOW_MULTI_ASSET_DEMO_TRADING`
- `NOTIFICATIONS_ENABLED`
- `MT5_SERVER`
- `MAX_DEMO_ORDER_VOLUME`
- `MAX_DEMO_ORDERS_PER_DAY`
- `FOREX_SCANNER_MAGIC_NUMBER`
