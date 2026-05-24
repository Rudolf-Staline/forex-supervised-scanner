# Experiment Registry

Le registre d'expériences permet de tracer localement les backtests, forward tests et rapports sans exécuter de trading.

## Emplacement

- `reports/experiments/experiments.jsonl`
- `reports/experiments/<experiment_id>.json`
- `reports/experiments/summary.json` (si export)

## Commandes

```bash
python scripts/experiment_registry.py --create --name "Initial paper analysis" --description "Track paper-mode reports" --tag paper --status draft
python scripts/experiment_registry.py --list
python scripts/experiment_registry.py --show EXPERIMENT_ID
python scripts/experiment_registry.py --show EXPERIMENT_ID --attach-report reports/paper_report.json
python scripts/experiment_registry.py --show EXPERIMENT_ID --status completed
python scripts/experiment_registry.py --export-summary
```

## Champs enregistrés

- `experiment_id`
- `created_at`
- `updated_at`
- `name`
- `description`
- `tags`
- `status` (`draft|running|completed|discarded`)
- `git_commit_if_available`
- `branch_if_available`
- `command_examples`
- `attached_reports`
- `notes`
- `safety_status` (`paper_only`)

## Règles de sécurité

- aucun lancement de stratégie
- aucun appel MT5
- aucun envoi d'ordre
- aucune modification des rapports attachés
- écriture limitée à `reports/experiments/`
