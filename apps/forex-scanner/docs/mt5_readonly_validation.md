# MT5 Read-Only Validation (Demo)

Script: `scripts/mt5_readonly_validation.py`

## But

Valider localement une connexion MT5 **démo uniquement** sans envoi d'ordre.

- Ne jamais appeler `order_send`
- Ne jamais fermer de position
- Ne jamais activer le live trading

## Commande

```bash
python scripts/mt5_readonly_validation.py \
  --watchlist multi_asset_demo \
  --symbols EUR/USD XAU/USD US500 \
  --show-next-windows \
  --export-report
```

## Vérifications effectuées

- import du module `MetaTrader5`
- `initialize()`
- `account_info()`
- contrôle `demo_only` via `trade_mode` / serveur contenant `Demo`
- affichage balance/equity/margin/free_margin quand disponibles
- résolution symbole logique -> symbole MT5
- `symbol_info` et `symbol_info_tick` pour chaque symbole
- fenêtres de session tradables (option `--show-next-windows`)
- réconciliation via `reconcile_mt5_demo` (lecture seule)

## Rapports exportés

- `reports/mt5_readonly_validation.json`
- `reports/mt5_readonly_validation.txt`

Champs principaux:

- `mt5_available`
- `initialized`
- `account_server`
- `demo_only`
- `symbols_checked`
- `symbols_ok`
- `symbols_failed`
- `reconciliation_status`
- `open_positions_count`
- `foreign_positions_count`
- `next_tradable_windows`
- `final_status` (`READY_READONLY`, `BLOCKED`, `MT5_UNAVAILABLE`)

## Environnement cloud

Si MT5 n'est pas accessible:

- le script ne plante pas
- affiche `MT5 terminal is not available in cloud environment.`
- status final `MT5_UNAVAILABLE`
