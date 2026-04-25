"""Operator-facing retention and archive reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.archive.retention import (
    ArchiveManifest,
    ArchiveRestoreResult,
    ArchiveVerificationResult,
    RetentionEvaluation,
    RotationPlan,
)
from app.config.settings import AppSettings


def generate_archive_report(
    settings: AppSettings,
    evaluation: RetentionEvaluation,
    manifests: list[ArchiveManifest],
    output_dir: Path,
    *,
    rotation_plan: RotationPlan | None = None,
    verification: ArchiveVerificationResult | None = None,
    restore: ArchiveRestoreResult | None = None,
) -> dict[str, Path]:
    """Write retention, archive inventory, and rotation reports."""

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}

    policy_json = output_dir / "retention_policy.json"
    policy_md = output_dir / "retention_policy.md"
    _write_json(policy_json, settings.retention_archive.model_dump(mode="json"))
    _write_text(policy_md, _policy_markdown(settings))
    outputs["retention_policy_json"] = policy_json
    outputs["retention_policy_markdown"] = policy_md

    candidates_csv = output_dir / "pending_archival_candidates.csv"
    candidates_json = output_dir / "pending_archival_candidates.json"
    _write_json(candidates_json, evaluation.model_dump(mode="json"))
    _write_candidate_csv(candidates_csv, evaluation)
    outputs["pending_candidates_csv"] = candidates_csv
    outputs["pending_candidates_json"] = candidates_json

    inventory_csv = output_dir / "archive_inventory.csv"
    inventory_json = output_dir / "archive_inventory.json"
    _write_json(inventory_json, [manifest.model_dump(mode="json") for manifest in manifests])
    _write_archive_inventory_csv(inventory_csv, manifests)
    outputs["archive_inventory_csv"] = inventory_csv
    outputs["archive_inventory_json"] = inventory_json

    summary_md = output_dir / "summary.md"
    _write_text(summary_md, _summary_markdown(evaluation, manifests, rotation_plan, verification, restore))
    outputs["summary_markdown"] = summary_md

    if rotation_plan is not None:
        rotation_json = output_dir / "rotation_plan.json"
        rotation_md = output_dir / "rotation_plan.md"
        _write_json(rotation_json, rotation_plan.model_dump(mode="json"))
        _write_text(rotation_md, _rotation_markdown(rotation_plan))
        outputs["rotation_plan_json"] = rotation_json
        outputs["rotation_plan_markdown"] = rotation_md

    if verification is not None:
        verification_json = output_dir / "latest_archive_verification.json"
        _write_json(verification_json, verification.model_dump(mode="json"))
        outputs["latest_archive_verification_json"] = verification_json

    if restore is not None:
        restore_json = output_dir / "latest_restore_result.json"
        _write_json(restore_json, restore.model_dump(mode="json"))
        outputs["latest_restore_result_json"] = restore_json

    return outputs


def _policy_markdown(settings: AppSettings) -> str:
    policy = settings.retention_archive
    return "\n".join(
        [
            "# Retention Policy",
            "",
            f"- Archive output: `{policy.archive_output_dir}`",
            f"- Restore review output: `{policy.restore_output_dir}`",
            f"- Audit records: {policy.audit_records_retention_days} days",
            f"- Journal/events: {policy.journal_events_retention_days} days",
            f"- Alerts/incidents: {policy.alerts_incidents_retention_days} days",
            f"- Monitoring snapshots: {policy.monitoring_snapshots_retention_days} days",
            f"- Soak/campaign outputs: {policy.soak_campaign_retention_days} days",
            f"- Reports/exports: {policy.reports_exports_retention_days} days",
            f"- Checkpoints/seals: {policy.checkpoint_seals_retention_days} days",
            f"- Database purge allowed: {policy.allow_database_purge}",
            f"- File rotation allowed: {policy.allow_file_rotation}",
            "",
            "Active protected database evidence is retained by default. Archive packages preserve review copies and integrity metadata.",
        ]
    ) + "\n"


def _summary_markdown(
    evaluation: RetentionEvaluation,
    manifests: list[ArchiveManifest],
    rotation_plan: RotationPlan | None,
    verification: ArchiveVerificationResult | None,
    restore: ArchiveRestoreResult | None,
) -> str:
    lines = [
        "# Archive And Retention Summary",
        "",
        f"- Pending database candidates: {evaluation.total_database_candidates}",
        f"- Pending file candidates: {evaluation.total_file_candidates}",
        f"- Known archives: {len(manifests)}",
    ]
    if manifests:
        latest = max(manifests, key=lambda manifest: manifest.created_at)
        lines.extend(
            [
                f"- Latest archive: `{latest.archive_id}`",
                f"- Latest archive package: `{latest.package_path}`",
                f"- Latest archive records: {len(latest.records)}",
                f"- Latest archive files: {latest.file_count}",
                f"- Latest integrity status: {latest.integrity_verification_status or 'not_run'}",
            ]
        )
    if rotation_plan is not None:
        lines.extend(
            [
                "",
                "## Rotation Status",
                "",
                f"- Dry run: {rotation_plan.dry_run}",
                f"- Database records to archive: {rotation_plan.database_records_to_archive}",
                f"- Files to rotate: {rotation_plan.files_to_rotate}",
            ]
        )
        if rotation_plan.blocked_actions:
            lines.extend(["", "### Blocked Actions", *[f"- {item}" for item in rotation_plan.blocked_actions]])
        if rotation_plan.recommended_actions:
            lines.extend(["", "### Recommended Actions", *[f"- {item}" for item in rotation_plan.recommended_actions]])
    if verification is not None:
        lines.extend(["", "## Latest Verification", "", f"- Status: {verification.status.value}", f"- Issues: {len(verification.issues)}"])
    if restore is not None:
        lines.extend(["", "## Latest Restore", "", f"- Status: {restore.status}", f"- Restore path: `{restore.restore_path}`"])
    if evaluation.warnings:
        lines.extend(["", "## Warnings", *[f"- {warning}" for warning in evaluation.warnings]])
    return "\n".join(lines) + "\n"


def _rotation_markdown(plan: RotationPlan) -> str:
    lines = [
        "# Rotation Plan",
        "",
        f"- Plan id: `{plan.plan_id}`",
        f"- Dry run: {plan.dry_run}",
        f"- Database purge allowed: {plan.database_purge_allowed}",
        f"- File rotation allowed: {plan.file_rotation_allowed}",
        f"- Database records to archive: {plan.database_records_to_archive}",
        f"- Files to rotate: {plan.files_to_rotate}",
        "",
        "## Candidate Counts",
    ]
    if plan.candidate_counts:
        lines.extend(f"- {key}: {value}" for key, value in sorted(plan.candidate_counts.items()))
    else:
        lines.append("- none")
    if plan.blocked_actions:
        lines.extend(["", "## Blocked Actions", *[f"- {item}" for item in plan.blocked_actions]])
    if plan.recommended_actions:
        lines.extend(["", "## Recommended Actions", *[f"- {item}" for item in plan.recommended_actions]])
    return "\n".join(lines) + "\n"


def _write_candidate_csv(path: Path, evaluation: RetentionEvaluation) -> None:
    rows = [
        {
            "record_class": candidate.record_class.value,
            "source": candidate.source,
            "record_id": candidate.record_id,
            "created_at": candidate.created_at.isoformat(),
            "cutoff_at": candidate.cutoff_at.isoformat(),
            "age_days": candidate.age_days,
            "file_path": candidate.file_path or "",
            "size_bytes": candidate.size_bytes or "",
        }
        for candidate in evaluation.candidates
    ]
    _write_csv(path, rows, ["record_class", "source", "record_id", "created_at", "cutoff_at", "age_days", "file_path", "size_bytes"])


def _write_archive_inventory_csv(path: Path, manifests: list[ArchiveManifest]) -> None:
    rows = [
        {
            "archive_id": manifest.archive_id,
            "created_at": manifest.created_at.isoformat(),
            "package_path": manifest.package_path,
            "package_sha256": manifest.package_sha256 or "",
            "records": len(manifest.records),
            "files": manifest.file_count,
            "integrity_verification_status": manifest.integrity_verification_status or "",
            "rotation_safe": manifest.rotation_safe,
        }
        for manifest in manifests
    ]
    _write_csv(path, rows, ["archive_id", "created_at", "package_path", "package_sha256", "records", "files", "integrity_verification_status", "rotation_safe"])


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

