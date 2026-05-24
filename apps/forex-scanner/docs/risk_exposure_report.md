# Risk Exposure Report

Script: `python scripts/risk_exposure_report.py`

## Objectif

Rapport d'exposition au risque en mode **lecture seule**. Ce script:

- N'envoie aucun ordre.
- N'appelle pas MT5.
- Ne modifie aucune configuration.
- Ne change aucun seuil.

## Entrées analysées

- `reports/signal_journal.jsonl`
- `reports/forward_test_paper.csv`
- `reports/forward_test_summary.json`
- `reports/paper_fill_report.csv`
- `reports/paper_fill_summary.json`
- `reports/readiness_report.json`

Le script continue même si des fichiers sont absents.

## Options CLI

- `--reports-dir reports`
- `--asset-class forex|commodities|indices|all`
- `--symbol SYMBOL`
- `--export-json`
- `--export-csv`
- `--top-n 10`

## Sorties

- `reports/risk_exposure_summary.json` (si `--export-json`)
- `reports/risk_exposure_report.csv` (si `--export-csv`)
