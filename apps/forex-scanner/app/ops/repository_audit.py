"""Static repository maintenance audit for apps/forex-scanner."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

UNSAFE_KEYWORDS = (
    "live trading",
    "order_send",
    "mt5.order_send",
    "send_order",
    "place_order",
    "submit_order",
)
ORDER_EXECUTION_KEYWORDS = (
    "order_send",
    "send_order",
    "place_order",
    "submit_order",
)
ENV_FILE_CANDIDATES = (".env", ".env.example", ".env.sample", ".env.local")


@dataclass
class RepositoryAuditReport:
    scripts_count: int
    tests_count: int
    docs_count: int
    missing_docs_for_scripts: list[str]
    missing_tests_for_scripts: list[str]
    orphan_tests: list[str]
    potentially_duplicate_scripts: list[list[str]]
    potentially_stale_reports: list[str]
    large_files: list[str]
    unsafe_keywords_detected: list[str]
    order_execution_keywords_detected: list[str]
    environment_files_detected: list[str]
    maintenance_status: str
    suggestions: list[str]


def _stem_name(path: Path) -> str:
    return path.stem.lower()


def _find_keyword_hits(paths: list[Path], keywords: tuple[str, ...], root: Path) -> list[str]:
    hits: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for kw in keywords:
            if kw in text:
                hits.append(f"{path.relative_to(root).as_posix()}::{kw}")
    return sorted(set(hits))


def _group_potential_duplicates(script_paths: list[Path], root: Path) -> list[list[str]]:
    buckets: dict[str, list[str]] = {}
    for path in script_paths:
        tokens = tuple(sorted(t for t in _stem_name(path).replace("-", "_").split("_") if t))
        key = "_".join(tokens)
        buckets.setdefault(key, []).append(path.relative_to(root).as_posix())
    return sorted([sorted(v) for v in buckets.values() if len(v) > 1])


def _compute_status(report: RepositoryAuditReport) -> str:
    if report.order_execution_keywords_detected:
        return "BLOCKED"
    if report.unsafe_keywords_detected:
        return "NEEDS_REVIEW"
    if report.missing_docs_for_scripts or report.missing_tests_for_scripts or report.orphan_tests:
        return "WARN"
    return "CLEAN"


def build_repository_audit(root: Path, large_file_threshold_bytes: int = 200_000) -> RepositoryAuditReport:
    root = root.resolve()
    scripts_dir = root / "scripts"
    tests_dir = root / "tests"
    docs_dir = root / "docs"
    app_dir = root / "app"
    reports_dir = root / "reports"

    script_paths = sorted(scripts_dir.glob("*.py")) if scripts_dir.exists() else []
    test_paths = sorted(tests_dir.glob("*.py")) if tests_dir.exists() else []
    doc_paths = sorted(docs_dir.glob("*.md")) if docs_dir.exists() else []
    app_paths = sorted(app_dir.rglob("*.py")) if app_dir.exists() else []
    report_files = sorted(reports_dir.glob("*") if reports_dir.exists() else [])

    doc_stems = {_stem_name(p) for p in doc_paths}
    test_stems = {_stem_name(p).replace("test_", "", 1) for p in test_paths if p.name.startswith("test_")}
    script_stems = {_stem_name(p) for p in script_paths}

    missing_docs = [p.name for p in script_paths if _stem_name(p) not in doc_stems]
    missing_tests = [p.name for p in script_paths if _stem_name(p) not in test_stems]
    orphan_tests = [p.name for p in test_paths if p.name.startswith("test_") and _stem_name(p).replace("test_", "", 1) not in script_stems]

    manifest_paths = [p for p in [root / "pyproject.toml", root / "requirements.txt", root / ".env.example"] if p.exists()]
    scanned_text_paths = script_paths + test_paths + app_paths + manifest_paths
    unsafe_hits = _find_keyword_hits(scanned_text_paths, UNSAFE_KEYWORDS, root)
    order_hits = _find_keyword_hits(scanned_text_paths, ORDER_EXECUTION_KEYWORDS, root)

    stale_reports: list[str] = []
    if reports_dir.exists():
        report_names = {p.name.lower() for p in report_files if p.is_file()}
        if not any("repository_maintenance_audit" in n for n in report_names):
            stale_reports.append("reports/repository_maintenance_audit.* missing")

    large_files: list[str] = []
    for path in [*script_paths, *test_paths, *doc_paths, *app_paths, *manifest_paths]:
        if path.stat().st_size > large_file_threshold_bytes:
            size_kb = round(path.stat().st_size / 1024, 1)
            large_files.append(f"{path.relative_to(root).as_posix()} ({size_kb}KB)")

    environment_files = [p.relative_to(root).as_posix() for p in sorted(root.glob(".env*") ) if p.is_file()]
    for candidate in ENV_FILE_CANDIDATES:
        p = root / candidate
        if p.exists() and p.is_file() and p.relative_to(root).as_posix() not in environment_files:
            environment_files.append(p.relative_to(root).as_posix())

    suggestions: list[str] = []
    if missing_docs:
        suggestions.append("Ajouter des docs/*.md alignées sur les scripts sans documentation dédiée.")
    if missing_tests:
        suggestions.append("Ajouter des tests unitaires test_<script>.py pour les scripts non couverts.")
    if orphan_tests:
        suggestions.append("Vérifier les tests orphelins et rattacher à un script existant ou convertir en test d'intégration documenté.")
    if unsafe_hits:
        suggestions.append("Passer en revue manuellement les mots-clés dangereux détectés (aucune exécution d'ordre).")
    if not suggestions:
        suggestions.append("Aucune action prioritaire détectée; maintenir une cadence d'audit régulière.")

    report = RepositoryAuditReport(
        scripts_count=len(script_paths),
        tests_count=len(test_paths),
        docs_count=len(doc_paths),
        missing_docs_for_scripts=missing_docs,
        missing_tests_for_scripts=missing_tests,
        orphan_tests=orphan_tests,
        potentially_duplicate_scripts=_group_potential_duplicates(script_paths, root),
        potentially_stale_reports=stale_reports,
        large_files=sorted(large_files),
        unsafe_keywords_detected=unsafe_hits,
        order_execution_keywords_detected=order_hits,
        environment_files_detected=sorted(set(environment_files)),
        maintenance_status="CLEAN",
        suggestions=suggestions,
    )
    report.maintenance_status = _compute_status(report)
    return report


def export_report_json(report: RepositoryAuditReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "repository_maintenance_audit.json"
    out.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def export_report_txt(report: RepositoryAuditReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "repository_maintenance_audit.txt"
    lines = [
        "# Repository Maintenance Audit",
        f"maintenance_status: {report.maintenance_status}",
        f"scripts_count: {report.scripts_count}",
        f"tests_count: {report.tests_count}",
        f"docs_count: {report.docs_count}",
        f"missing_docs_for_scripts: {report.missing_docs_for_scripts}",
        f"missing_tests_for_scripts: {report.missing_tests_for_scripts}",
        f"orphan_tests: {report.orphan_tests}",
        f"potentially_duplicate_scripts: {report.potentially_duplicate_scripts}",
        f"potentially_stale_reports: {report.potentially_stale_reports}",
        f"large_files: {report.large_files}",
        f"unsafe_keywords_detected: {report.unsafe_keywords_detected}",
        f"order_execution_keywords_detected: {report.order_execution_keywords_detected}",
        f"environment_files_detected: {report.environment_files_detected}",
        "suggestions:",
    ]
    lines.extend([f"- {s}" for s in report.suggestions])
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
