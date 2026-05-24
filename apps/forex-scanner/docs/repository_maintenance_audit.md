# Repository Maintenance Audit

Le script `scripts/repository_maintenance_audit.py` réalise un audit **statique** du sous-projet `apps/forex-scanner` en mode lecture seule.

## Portée analysée

- `scripts/*.py`
- `tests/*.py`
- `docs/*.md`
- `app/**/*.py`
- `reports/` (si présent)
- `pyproject.toml`
- `requirements.txt`
- `.env.example` (si présent)

## CLI

```bash
python scripts/repository_maintenance_audit.py --root . --export-json --export-txt --show-suggestions
```

Options disponibles :

- `--root .`
- `--export-json`
- `--export-txt`
- `--show-suggestions`

## Sorties

- `reports/repository_maintenance_audit.json`
- `reports/repository_maintenance_audit.txt`

## Champs rapportés

- `scripts_count`
- `tests_count`
- `docs_count`
- `missing_docs_for_scripts`
- `missing_tests_for_scripts`
- `orphan_tests`
- `potentially_duplicate_scripts`
- `potentially_stale_reports`
- `large_files`
- `unsafe_keywords_detected`
- `order_execution_keywords_detected`
- `environment_files_detected`
- `maintenance_status` (`CLEAN`, `WARN`, `NEEDS_REVIEW`, `BLOCKED`)
- `suggestions`

## Sécurité

Le script n'exécute aucun script métier, n'appelle pas MT5 et n'envoie aucun ordre. Les mots-clés sensibles sont seulement détectés et rapportés.
