# Dashboard local (lecture seule)

## Objectif
Le dashboard local permet de surveiller le bot multi-asset **sans lire les logs PowerShell**.
Il est strictement en lecture seule :
- aucun ordre,
- aucune modification de configuration,
- aucune activation du live trading.

## Lancement
```bash
python scripts/dashboard.py --watchlist multi_asset_demo --refresh-seconds 10
```

Si Streamlit n'est pas installé, le script affiche :
`Install streamlit to run the dashboard: pip install streamlit`

## Sources de données
Le dashboard lit les fichiers suivants dans `reports/` :
- `signal_journal.jsonl`
- `multi_asset_signal_report_summary.json`
- `backtest_multi_asset_summary.json`
- `threshold_optimizer_summary.json`

Si un fichier est absent ou invalide, l'UI affiche **No data yet** au lieu d'échouer.

## Informations affichées
- current mode, broker, provider, watchlist
- safety status
- `ALLOW_LIVE_TRADING`, `MT5_DEMO_ONLY`, `ALLOW_MULTI_ASSET_DEMO_TRADING`, `ENABLE_DEMO_EXECUTION`
- next tradable sessions, tradable symbols now, off-hours symbols
- latest signals, near-miss signals, best scores, best setups
- rejection reasons
- spread/ATR by symbol
- backtest summary
- threshold optimizer summary

## Filtres
- `asset_class`
- `symbol`
- `setup`
- `status`
- `min_score`
- `session`

## Section Safety status
La section met en avant :
- live trading disabled
- demo only
- paper mode
- scan_only commodities/indices
- daily limits status (si disponible)
