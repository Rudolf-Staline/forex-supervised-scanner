# Near-miss review queue

Le script `scripts/near_miss_review_queue.py` construit une file de revue des signaux **near-miss** pour analyse manuelle.

## Objectif

Identifier des opportunités presque valides sans changer la stratégie ni les seuils, et sans autoriser l'exécution.

Message de sécurité affiché :

`Near-miss review is informational and does not authorize execution.`

## Entrées

Le rapport agrège les fichiers suivants quand ils existent :

- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/signal_quality_summary.json`
- `reports/multi_asset_signal_report_summary.json`

## Options CLI

- `--asset-class forex|commodities|indices|all`
- `--symbol SYMBOL`
- `--session SESSION`
- `--min-score 65`
- `--top-n 25`
- `--export-json`
- `--export-csv`

## Sorties

- `reports/near_miss_review_queue.json`
- `reports/near_miss_review_queue.csv`

## Définition near-miss

Un enregistrement est classé near-miss si au moins un des cas suivants est vrai :

- score proche du seuil mais inférieur,
- risk/reward proche du minimum mais insuffisant,
- spread/ATR légèrement trop élevé,
- statut `watchlist` ou `detected` avec score élevé,
- plusieurs critères positifs mais un bloqueur majeur.

Le champ `review_priority_score` est purement informatif et favorise les candidats à score élevé, avec RR correct, spread/ATR raisonnable, session tradable et peu de raisons de rejet.
