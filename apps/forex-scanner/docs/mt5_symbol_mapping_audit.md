# MT5 Symbol Mapping Audit (read-only)

Script: `python scripts/mt5_symbol_mapping_audit.py --watchlist multi_asset_demo --check-static --check-reports --export-json --export-csv`

## Objectif
- Auditer la cohérence entre symboles logiques et symboles MT5 attendus.
- Ne jamais modifier le mapping, les watchlists, ni la stratégie.
- Ne jamais envoyer d'ordre.

## Sorties
- `reports/mt5_symbol_mapping_audit.json`
- `reports/mt5_symbol_mapping_audit.csv`

## Champs du rapport
- `expected_mappings`
- `resolved_mappings`
- `missing_mappings`
- `mismatched_mappings`
- `unused_mappings`
- `symbols_seen_in_reports`
- `symbols_missing_from_reports`
- `asset_class_consistency`
- `mapping_status` (`CLEAN`, `WARN`, `NEEDS_REVIEW`, `BLOCKED`)
- `recommendations`
- `safety_warning`

## Sécurité
- Audit strictement read-only.
- Aucun appel MT5 obligatoire.
- Aucun envoi d'ordre.
