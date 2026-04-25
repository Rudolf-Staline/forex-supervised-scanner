"""Operator-facing backup, restore, and service-continuity reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.backup.recovery import (
    BackupManifest,
    BackupRestoreResult,
    BackupVerificationResult,
    RecoveryValidationResult,
)
from app.config.settings import AppSettings


def generate_backup_recovery_report(
    settings: AppSettings,
    manifests: list[BackupManifest],
    output_dir: Path,
    *,
    latest_verification: BackupVerificationResult | None = None,
    latest_restore: BackupRestoreResult | None = None,
    latest_recovery_validation: RecoveryValidationResult | None = None,
) -> dict[str, Path]:
    """Write local DR reports for operator review."""

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}

    policy_json = output_dir / "backup_policy.json"
    policy_md = output_dir / "backup_policy.md"
    _write_json(policy_json, settings.backup_recovery.model_dump(mode="json"))
    _write_text(policy_md, _policy_markdown(settings))
    outputs["backup_policy_json"] = policy_json
    outputs["backup_policy_markdown"] = policy_md

    inventory_csv = output_dir / "backup_inventory.csv"
    inventory_json = output_dir / "backup_inventory.json"
    _write_json(inventory_json, [manifest.model_dump(mode="json") for manifest in manifests])
    _write_inventory_csv(inventory_csv, manifests)
    outputs["backup_inventory_csv"] = inventory_csv
    outputs["backup_inventory_json"] = inventory_json

    coverage_json = output_dir / "backup_coverage.json"
    coverage_md = output_dir / "backup_coverage.md"
    _write_json(coverage_json, _coverage_payload(manifests))
    _write_text(coverage_md, _coverage_markdown(manifests))
    outputs["backup_coverage_json"] = coverage_json
    outputs["backup_coverage_markdown"] = coverage_md

    summary_md = output_dir / "summary.md"
    _write_text(summary_md, _summary_markdown(manifests, latest_verification, latest_restore, latest_recovery_validation))
    outputs["summary_markdown"] = summary_md

    if latest_verification is not None:
        verification_json = output_dir / "latest_backup_verification.json"
        verification_md = output_dir / "latest_backup_verification.md"
        _write_json(verification_json, latest_verification.model_dump(mode="json"))
        _write_text(verification_md, _verification_markdown(latest_verification))
        outputs["latest_backup_verification_json"] = verification_json
        outputs["latest_backup_verification_markdown"] = verification_md

    if latest_restore is not None:
        restore_json = output_dir / "latest_restore_result.json"
        restore_md = output_dir / "latest_restore_result.md"
        _write_json(restore_json, latest_restore.model_dump(mode="json"))
        _write_text(restore_md, _restore_markdown(latest_restore))
        outputs["latest_restore_result_json"] = restore_json
        outputs["latest_restore_result_markdown"] = restore_md

    if latest_recovery_validation is not None:
        recovery_json = output_dir / "recovery_validation_status.json"
        recovery_md = output_dir / "recovery_validation_status.md"
        _write_json(recovery_json, latest_recovery_validation.model_dump(mode="json"))
        _write_text(recovery_md, _recovery_markdown(latest_recovery_validation))
        outputs["recovery_validation_json"] = recovery_json
        outputs["recovery_validation_markdown"] = recovery_md

    return outputs


def _policy_markdown(settings: AppSettings) -> str:
    policy = settings.backup_recovery
    return "\n".join(
        [
            "# Backup And Recovery Policy",
            "",
            f"- Backup output: `{policy.backup_output_dir}`",
            f"- Restore review output: `{policy.restore_review_dir}`",
            f"- Pre-restore safety backup output: `{policy.pre_restore_backup_dir}`",
            f"- Recovery state path: `{policy.recovery_state_path}`",
            f"- Include database: {policy.include_database}",
            f"- Include config snapshot: {policy.include_config_snapshot}",
            f"- Include archive manifests: {policy.include_archive_manifests}",
            f"- Include critical reports: {policy.include_critical_reports}",
            f"- Active restore enabled: {policy.allow_active_restore}",
            f"- Verify before restore: {policy.verify_before_restore}",
            f"- Block sensitive actions until validation: {policy.block_sensitive_actions_until_recovery_validation}",
        ]
    ) + "\n"


def _summary_markdown(
    manifests: list[BackupManifest],
    latest_verification: BackupVerificationResult | None,
    latest_restore: BackupRestoreResult | None,
    latest_recovery_validation: RecoveryValidationResult | None,
) -> str:
    lines = ["# Backup And Recovery Summary", "", f"- Known backups: {len(manifests)}"]
    if manifests:
        latest = max(manifests, key=lambda manifest: manifest.created_at)
        lines.extend(
            [
                f"- Latest backup: `{latest.backup_id}`",
                f"- Latest backup package: `{latest.package_path}`",
                f"- Latest SQLite integrity: {latest.sqlite_integrity_status or 'unknown'}",
                f"- Latest audit verification: {latest.audit_verification_status or 'not_run'}",
                f"- Latest backup files: {len(latest.files)}",
            ]
        )
    if latest_verification is not None:
        lines.extend(["", "## Latest Verification", "", f"- Status: {latest_verification.status.value}", f"- Issues: {len(latest_verification.issues)}"])
    if latest_restore is not None:
        lines.extend(["", "## Latest Restore", "", f"- Status: {latest_restore.status}", f"- Mode: {latest_restore.mode.value}"])
    if latest_recovery_validation is not None:
        lines.extend(
            [
                "",
                "## Recovery Validation",
                "",
                f"- Status: {latest_recovery_validation.status.value}",
                f"- Continuity mode: {latest_recovery_validation.mode.value}",
                f"- Sensitive actions blocked: {latest_recovery_validation.sensitive_actions_blocked}",
            ]
        )
        if latest_recovery_validation.blockers:
            lines.extend(["", "### Blockers", *[f"- {item}" for item in latest_recovery_validation.blockers]])
    return "\n".join(lines) + "\n"


def _coverage_payload(manifests: list[BackupManifest]) -> dict[str, Any]:
    latest = max(manifests, key=lambda manifest: manifest.created_at) if manifests else None
    return {
        "backup_count": len(manifests),
        "latest_backup_id": latest.backup_id if latest else None,
        "latest_backup_at": latest.created_at.isoformat() if latest else None,
        "latest_scope": [item.value for item in latest.scope] if latest else [],
        "missing_latest_scope": _missing_scope(latest) if latest else [],
    }


def _coverage_markdown(manifests: list[BackupManifest]) -> str:
    payload = _coverage_payload(manifests)
    lines = [
        "# Backup Coverage",
        "",
        f"- Backup count: {payload['backup_count']}",
        f"- Latest backup: `{payload['latest_backup_id'] or 'none'}`",
        f"- Latest backup at: {payload['latest_backup_at'] or 'none'}",
        "",
        "## Latest Scope",
    ]
    scope = payload["latest_scope"]
    lines.extend([f"- {item}" for item in scope] if scope else ["- none"])
    missing = payload["missing_latest_scope"]
    if missing:
        lines.extend(["", "## Missing Or At-Risk Scope", *[f"- {item}" for item in missing]])
    return "\n".join(lines) + "\n"


def _missing_scope(manifest: BackupManifest | None) -> list[str]:
    if manifest is None:
        return ["no backup package exists"]
    required = {
        "active_database",
        "audit_journal_records",
        "integrity_metadata",
        "incidents_alerts",
        "monitoring_history",
        "operator_session_state",
    }
    present = {item.value for item in manifest.scope}
    return sorted(required - present)


def _verification_markdown(result: BackupVerificationResult) -> str:
    lines = [
        "# Backup Verification",
        "",
        f"- Status: {result.status.value}",
        f"- Backup id: {result.backup_id or 'unknown'}",
        f"- Package: `{result.package_path}`",
        f"- SQLite integrity: {result.sqlite_integrity_status or 'unknown'}",
        f"- Audit verification: {result.audit_verification_status or 'not_run'}",
        f"- Checked files: {result.checked_files}",
        f"- Issues: {len(result.issues)}",
    ]
    if result.issues:
        lines.extend(["", "## Issues", *[f"- {item}" for item in result.issues]])
    return "\n".join(lines) + "\n"


def _restore_markdown(result: BackupRestoreResult) -> str:
    lines = [
        "# Restore Result",
        "",
        f"- Status: {result.status}",
        f"- Mode: {result.mode.value}",
        f"- Backup id: {result.backup_id}",
        f"- Restore path: `{result.restore_path or ''}`",
        f"- Active database path: `{result.active_database_path or ''}`",
        f"- Safety backup path: `{result.safety_backup_path or ''}`",
        f"- Verification: {result.verification_status.value}",
    ]
    if result.warnings:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in result.warnings]])
    return "\n".join(lines) + "\n"


def _recovery_markdown(result: RecoveryValidationResult) -> str:
    lines = [
        "# Recovery Validation",
        "",
        f"- Status: {result.status.value}",
        f"- Continuity mode: {result.mode.value}",
        f"- Database: `{result.database_path}`",
        f"- SQLite integrity: {result.sqlite_integrity_status}",
        f"- Audit verification: {result.audit_verification_status or 'not_run'}",
        f"- Sensitive actions blocked: {result.sensitive_actions_blocked}",
        f"- Open incidents: {result.open_incidents}",
        f"- Active alerts: {result.active_alerts}",
        f"- Severe anomalies: {result.severe_anomalies}",
        f"- Open broker orders: {result.open_broker_orders}",
    ]
    if result.blockers:
        lines.extend(["", "## Blockers", *[f"- {item}" for item in result.blockers]])
    if result.warnings:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in result.warnings]])
    if result.recommendations:
        lines.extend(["", "## Recommendations", *[f"- {item}" for item in result.recommendations]])
    return "\n".join(lines) + "\n"


def _write_inventory_csv(path: Path, manifests: list[BackupManifest]) -> None:
    rows = [
        {
            "backup_id": manifest.backup_id,
            "created_at": manifest.created_at.isoformat(),
            "package_path": manifest.package_path,
            "package_sha256": manifest.package_sha256 or "",
            "sqlite_integrity_status": manifest.sqlite_integrity_status or "",
            "audit_verification_status": manifest.audit_verification_status or "",
            "files": len(manifest.files),
            "archive_manifest_count": manifest.archive_manifest_count,
            "critical_report_count": manifest.critical_report_count,
        }
        for manifest in manifests
    ]
    _write_csv(
        path,
        rows,
        [
            "backup_id",
            "created_at",
            "package_path",
            "package_sha256",
            "sqlite_integrity_status",
            "audit_verification_status",
            "files",
            "archive_manifest_count",
            "critical_report_count",
        ],
    )


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")

