# Audit de stabilité post-PR #45

Date UTC : 2026-05-29  
Branche : `codex/post-pr45-stability-audit`  
Portée : audit statique et validation locale sûre après le merge de la PR #45 (`edd0f75`).

## Garde-fous appliqués

Aucune commande de trading réel n'a été lancée pendant cet audit. La validation locale a forcé les variables de sécurité suivantes via le runner :

- `EXECUTION_MODE=paper`
- `BROKER_MODE=paper`
- `ALLOW_LIVE_TRADING=false`
- `MT5_DEMO_ONLY=true`
- `ENABLE_DEMO_EXECUTION=false`
- `AUTO_BOT_ENABLED=false`
- `NOTIFICATIONS_ENABLED=false`

## Résultats de l'audit

### Audit de maintenance du dépôt

Commande lancée depuis `apps/forex-scanner` :

```bash
python scripts/repository_maintenance_audit.py --root . --export-json --export-txt --show-suggestions
```

Résultat : `maintenance_status=BLOCKED`.

Ce statut est conservateur : il vient de la détection statique de mots-clés liés à l'exécution d'ordres dans le code et les tests. L'audit n'a exécuté aucun ordre, n'a pas appelé MT5 et n'a modifié aucun fichier de configuration local.

Résumé observé :

- `scripts_count=71`
- `tests_count=88`
- `docs_count=31`
- `potentially_duplicate_scripts=[]`
- `potentially_stale_reports=[]`
- `large_files=[]`
- `environment_files_detected=['.env.example']`

Actions recommandées par l'audit :

1. Ajouter des docs dédiées pour les scripts non documentés.
2. Ajouter ou rattacher des tests pour les scripts sans test dédié.
3. Revoir les tests orphelins et les convertir en tests d'intégration documentés si nécessaire.
4. Revoir manuellement les mots-clés sensibles détectés, sans exécution d'ordre.

### Validation locale sûre

Commande lancée depuis `apps/forex-scanner` :

```bash
python scripts/local_validation.py --quick --provider synthetic --watchlist multi_asset_demo --export-report
```

Résultat final après correction : succès (`readiness_status=ok`, `recommendation=safe_to_iterate`).

Anomalie trouvée pendant l'audit : le plan `local_validation.py` transmettait `--provider synthetic` à `multi_asset_signal_report.py`, alors que ce script accepte seulement `--asset-class`, `--watchlist`, `--min-score` et `--export-csv`. Le runner échouait donc avec `exit_code=2` malgré une validation synthétique sûre.

Correction appliquée : le runner local continue de fournir `--provider synthetic` aux commandes qui le supportent, mais appelle `multi_asset_signal_report.py` uniquement avec ses arguments supportés (`--watchlist`). Un test verrouille maintenant cette compatibilité d'interface.

### Tests unitaires

Commande lancée depuis `apps/forex-scanner` :

```bash
python -m pytest -q
```

Résultat : succès complet.

## Statut final

- Tâche traitée : Tâche 1.1 — Audit complet après PR #45.
- Statut final : terminé côté branche, prêt pour revue humaine.
- Blocage fonctionnel corrigé : validation locale quick synthétique rétablie.
- Point de vigilance restant : l'audit statique reste `BLOCKED` tant que les mots-clés d'exécution d'ordres légitimes ne sont pas classifiés plus finement ou revus manuellement.
- Prochaine tâche recommandée : attendre validation humaine de cette PR avant de passer à la tâche suivante du canvas.
