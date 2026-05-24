# Command Catalog

Le catalogue des commandes est généré de manière **statique** à partir de `scripts/*.py`.

## Génération

```bash
cd apps/forex-scanner
python scripts/command_catalog.py --export-json --export-md --show-unsafe
```

Sorties:
- `reports/command_catalog.json`
- `reports/command_catalog.md`

## Colonnes

- `script_name`: nom du script.
- `path`: chemin scanné.
- `guessed_category`: `reports|validation|mt5|paper|ops`.
- `safety_level`: `READ_ONLY|PAPER_ONLY|MT5_READONLY|DEMO_GATED|UNKNOWN`.
- `description`: description courte (docstring si disponible).
- `example_command`: commande d'exécution suggérée.
- `requires_mt5`: usage MT5 détecté statiquement.
- `can_send_order`: détection statique de mots-clés d'envoi d'ordre.
- `recommended_env`: environnement recommandé.
- `warnings`: avertissements éventuels.

## Règles de sécurité

- Aucun script scanné n'est exécuté.
- Aucun appel MT5 runtime n'est effectué par le catalogue.
- Aucun ordre n'est envoyé.
- En cas de doute, `safety_level=UNKNOWN` et revue manuelle requise.
