# Config Profile Validator

Le validateur `config_profile_validator.py` vérifie des profils de configuration sûrs **sans modifier l'environnement** et sans appeler MT5.

## Profils supportés

- `paper_safe`
- `cloud_safe`
- `mt5_readonly`
- `mt5_demo_precheck`
- `demo_execution_locked`

## Variables contrôlées

- `EXECUTION_MODE`
- `BROKER_MODE`
- `ALLOW_LIVE_TRADING`
- `MT5_DEMO_ONLY`
- `ENABLE_DEMO_EXECUTION`
- `AUTO_BOT_ENABLED`
- `ALLOW_MULTI_ASSET_DEMO_TRADING`
- `NOTIFICATIONS_ENABLED`
- `MAX_DEMO_ORDER_VOLUME`
- `MAX_DEMO_ORDERS_PER_DAY`

## Règles de sécurité

- `ALLOW_LIVE_TRADING=true` => `DANGEROUS` pour tous les profils.
- `ENABLE_DEMO_EXECUTION=true` => `BLOCKED` sauf profil `mt5_demo_precheck`.
- `BROKER_MODE=paper` attendu pour `paper_safe` et `cloud_safe`.
- `MT5_DEMO_ONLY=true` attendu pour tous les profils MT5.

## Utilisation

```bash
python scripts/config_profile_validator.py --profile paper_safe --show-recommendations
python scripts/config_profile_validator.py --profile mt5_readonly --export-json --export-txt
```

## Exports

- `reports/config_profile_validation.json`
- `reports/config_profile_validation.txt`

Le rapport contient :

- `profile`
- `status`
- `variables_ok`
- `variables_missing`
- `variables_wrong`
- `dangerous_flags`
- `recommendations`
- `safe_command_examples`
