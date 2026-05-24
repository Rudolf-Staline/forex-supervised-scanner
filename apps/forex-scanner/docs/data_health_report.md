# Data Health Report

Script d'analyse **read-only** de la qualité des données de `reports/`.

## Usage

```bash
python scripts/data_health_report.py --reports-dir reports --export-json --export-txt --max-age-hours 48 --min-records 10
```

## Vérifications couvertes

- présence/absence des fichiers attendus
- fichiers vides et fichiers stale
- JSONL invalide
- lignes CSV invalides
- doublons `cycle_id`
- champs requis manquants dans `signal_journal.jsonl`
- couverture symboles / classes d'actifs / sessions
- distributions: `score`, `spread_atr`, `risk_reward`

## Statuts

- `HEALTHY`: dataset exploitable sans anomalie majeure
- `WARN`: dataset utilisable mais incomplet, ancien ou faible en volume
- `DEGRADED`: dataset présent mais corrompu/invalide
- `BLOCKED`: dataset insuffisant pour analyse (ex: aucun signal exploitable)

## Sorties

- `reports/data_health_report.json`
- `reports/data_health_report.txt`

> Le script n'exécute aucune stratégie, ne modifie pas les seuils, et ne touche jamais au trading live.
