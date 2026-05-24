from __future__ import annotations

from app.ops.runbook_generator import SAFE_ENV_VARS, generate_runbook, write_runbook_reports


def test_markdown_generation_contains_critical_sections() -> None:
    doc = generate_runbook("paper")
    required_sections = [
        "## Prérequis",
        "## Variables d'environnement sûres",
        "## Commandes PowerShell recommandées",
        "## Commandes cloud-safe",
        "## Commandes paper",
        "## Commandes MT5 read-only",
        "## Commandes de rapports",
        "## Ordre recommandé d'exécution",
        "## Check-list avant toute démo limitée",
        "## Procédures de rollback",
        "## Procédures si GitHub Actions échoue",
        "## Procédure si MT5 est indisponible",
        "## Rappel sécurité",
    ]
    for section in required_sections:
        assert section in doc.markdown


def test_txt_generation_and_security_vars(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    doc = generate_runbook("mt5-readonly")
    written = write_runbook_reports(doc, export_md=True, export_txt=True)
    assert len(written) == 2

    md_content = (tmp_path / "reports" / "runbook.md").read_text(encoding="utf-8")
    txt_content = (tmp_path / "reports" / "runbook.txt").read_text(encoding="utf-8")

    for safe_var in SAFE_ENV_VARS:
        assert safe_var in md_content
        assert safe_var in txt_content


def test_no_live_trading_or_external_calls_keywords() -> None:
    doc = generate_runbook("forward-test")
    lowered = doc.markdown.lower()
    assert "live trading interdit" in lowered
    assert "subprocess" not in lowered
    assert "metatrader5" not in lowered
    assert "order_send" not in lowered
    assert "--live" not in lowered
