from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RunMode = Literal["paper", "mt5-readonly", "forward-test"]

SAFE_ENV_VARS: tuple[str, ...] = (
    "TRADING_MODE=paper",
    "ENABLE_LIVE_TRADING=0",
    "ALLOW_ORDER_EXECUTION=0",
    "MT5_READONLY=1",
    "CONFIRM_LIVE_ORDER=false",
)


@dataclass(frozen=True)
class RunbookDocument:
    mode: RunMode
    markdown: str
    text: str


def _mode_specific_commands(mode: RunMode) -> str:
    if mode == "paper":
        return "- `python scripts/run_session_aware_scan.py --mode paper --no-orders`"
    if mode == "mt5-readonly":
        return "- `python scripts/run_mt5_readonly_validation.py`"
    return "- `python scripts/run_forward_test_paper.py --duration 60 --no-orders`"


def generate_runbook(mode: RunMode) -> RunbookDocument:
    md = f"""# Runbook opérationnel local ({mode})

## Prérequis
- Python 3.11+ installé localement.
- Dépendances installées via `pip install -r requirements.txt`.
- Dossier `reports/` accessible en écriture.

## Variables d'environnement sûres
- `{SAFE_ENV_VARS[0]}`
- `{SAFE_ENV_VARS[1]}`
- `{SAFE_ENV_VARS[2]}`
- `{SAFE_ENV_VARS[3]}`
- `{SAFE_ENV_VARS[4]}`

## Commandes PowerShell recommandées
- `$env:TRADING_MODE='paper'`
- `$env:ENABLE_LIVE_TRADING='0'`
- `$env:ALLOW_ORDER_EXECUTION='0'`

## Commandes cloud-safe
- `python scripts/generate_readiness_report.py --strict`
- `python scripts/generate_data_health_report.py`

## Commandes paper
- `python scripts/run_paper_fill_report.py`
- `python scripts/generate_risk_exposure_report.py`

## Commandes MT5 read-only
- `python scripts/run_mt5_readonly_validation.py`
- `python scripts/generate_broker_execution_report.py --readonly`

## Commandes de rapports
- `python scripts/generate_report_index.py`
- `python scripts/generate_signal_quality_report.py`

## Ordre recommandé d'exécution
1. Vérifier les variables d'environnement sûres.
2. Lancer diagnostics cloud-safe.
3. Lancer mode opératoire ciblé.
4. Générer les rapports.

## Mode demandé
{_mode_specific_commands(mode)}

## Check-list avant toute démo limitée
- Vérifier `ENABLE_LIVE_TRADING=0`.
- Vérifier `ALLOW_ORDER_EXECUTION=0`.
- Vérifier que seule l'exécution paper/read-only est active.

## Procédures de rollback
- Revenir au dernier commit stable: `git checkout -- .`
- Supprimer les artefacts locaux: `rm -f reports/runbook.*`

## Procédures si GitHub Actions échoue
- Exécuter localement les tests ciblés.
- Régénérer les rapports et comparer les diffs.
- Ouvrir une investigation sans modifier les seuils.

## Procédure si MT5 est indisponible
- Basculer en `--mode paper`.
- Continuer uniquement les rapports non connectés.
- Documenter l'incident dans le journal local.

## Rappel sécurité
- Live trading interdit dans ce runbook.
- Aucun ordre ne doit être envoyé sans confirmation explicite future.
"""
    txt = md.replace("`", "")
    return RunbookDocument(mode=mode, markdown=md, text=txt)


def write_runbook_reports(doc: RunbookDocument, export_md: bool, export_txt: bool) -> list[Path]:
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if export_md:
        md_path = reports_dir / "runbook.md"
        md_path.write_text(doc.markdown, encoding="utf-8")
        written.append(md_path)
    if export_txt:
        txt_path = reports_dir / "runbook.txt"
        txt_path.write_text(doc.text, encoding="utf-8")
        written.append(txt_path)
    return written
