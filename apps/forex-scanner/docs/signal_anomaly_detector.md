# Signal Anomaly Detector

Le détecteur d'anomalies des signaux identifie des incohérences de qualité dans les journaux de reporting, sans modifier la stratégie ni les seuils.

## Commande

```bash
python scripts/signal_anomaly_detector.py --reports-dir reports --asset-class all --export-json --export-csv --top-n 20
```

## Entrées lues

- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/signal_quality_summary.json`
- `reports/risk_exposure_summary.json`

## Sorties

- `reports/signal_anomaly_summary.json`
- `reports/signal_anomaly_report.csv`

## Sécurité

- Aucune correction automatique des données.
- Aucun ordre n'est envoyé.
- Aucun appel MT5.
- Le détecteur est purement informationnel.
- Message affiché :

> Anomaly detection is informational and does not authorize execution.
