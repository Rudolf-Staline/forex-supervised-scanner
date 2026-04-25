"""Audit integrity reporting and evidence export helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.audit.integrity import (
    AuditExportPackage,
    AuditIntegrityIssue,
    AuditIntegrityIssueType,
    AuditIntegrityRecord,
    AuditProtectedRecordType,
    AuditSeal,
    AuditVerificationRun,
    AuditVerificationStatus,
)


def generate_audit_integrity_report(
    records: list[AuditIntegrityRecord],
    seals: list[AuditSeal],
    verifications: list[AuditVerificationRun],
    exports: list[AuditExportPackage],
    output_dir: Path,
) -> dict[str, Path]:
    """Write operator-facing audit integrity summaries and machine-readable tables."""

    output_dir.mkdir(parents=True, exist_ok=True)
    latest_verification = verifications[-1] if verifications else None
    issues = latest_verification.issues if latest_verification else []
    failed_verifications = [verification for verification in verifications if verification.status == AuditVerificationStatus.FAILED]
    suspicious = _suspicious_records_frame(issues)
    outputs = {
        "summary": output_dir / "summary.md",
        "summary_json": output_dir / "summary.json",
        "integrity_records": output_dir / "integrity_records.csv",
        "seals_history": output_dir / "seals_history.csv",
        "verification_history": output_dir / "verification_history.csv",
        "failed_verifications": output_dir / "failed_verifications.csv",
        "latest_verification": output_dir / "latest_verification.md",
        "latest_verification_json": output_dir / "latest_verification.json",
        "verification_issues": output_dir / "verification_issues.csv",
        "export_history": output_dir / "export_history.csv",
        "suspicious_records": output_dir / "suspicious_records.csv",
    }
    _records_frame(records).to_csv(outputs["integrity_records"], index=False)
    _seals_frame(seals).to_csv(outputs["seals_history"], index=False)
    _verification_frame(verifications).to_csv(outputs["verification_history"], index=False)
    _verification_frame(failed_verifications).to_csv(outputs["failed_verifications"], index=False)
    _issues_frame(issues).to_csv(outputs["verification_issues"], index=False)
    _exports_frame(exports).to_csv(outputs["export_history"], index=False)
    suspicious.to_csv(outputs["suspicious_records"], index=False)

    summary_payload = {
        "integrity_records": len(records),
        "latest_sequence": records[-1].sequence_number if records else 0,
        "seals": len(seals),
        "verifications": len(verifications),
        "failed_verifications": len(failed_verifications),
        "exports": len(exports),
        "latest_status": latest_verification.status.value if latest_verification else "not_verified",
        "latest_checked_records": latest_verification.checked_records if latest_verification else 0,
        "latest_issues": len(issues),
        "latest_altered_source_records": latest_verification.altered_source_records if latest_verification else 0,
        "latest_missing_source_records": latest_verification.missing_source_records if latest_verification else 0,
        "latest_missing_integrity_records": latest_verification.missing_integrity_records if latest_verification else 0,
        "latest_chain_breaks": latest_verification.chain_breaks if latest_verification else 0,
        "latest_seal_failures": latest_verification.seal_failures if latest_verification else 0,
    }
    outputs["summary_json"].write_text(json.dumps(summary_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["summary"].write_text(_summary_markdown(summary_payload), encoding="utf-8")
    outputs["latest_verification_json"].write_text(_json_or_empty(latest_verification), encoding="utf-8")
    outputs["latest_verification"].write_text(_latest_verification_markdown(latest_verification), encoding="utf-8")
    return outputs


def export_audit_evidence_package(
    records: list[AuditIntegrityRecord],
    seals: list[AuditSeal],
    verification: AuditVerificationRun | None,
    output_dir: Path,
    *,
    scope_from: datetime | None = None,
    scope_to: datetime | None = None,
    record_types: list[AuditProtectedRecordType] | None = None,
    created_at: datetime | None = None,
) -> tuple[AuditExportPackage, dict[str, Path]]:
    """Create a reviewable local audit evidence package with manifest and hashes."""

    timestamp = created_at or datetime.now(timezone.utc)
    package_dir = output_dir / f"audit_export_{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    package_dir.mkdir(parents=True, exist_ok=True)
    selected_types = record_types or sorted({record.record_type for record in records}, key=lambda item: item.value)

    outputs = {
        "integrity_records_json": package_dir / "integrity_records.json",
        "integrity_records_csv": package_dir / "integrity_records.csv",
        "seals_json": package_dir / "seals.json",
        "seals_csv": package_dir / "seals.csv",
        "verification_json": package_dir / "verification.json",
        "verification_issues_csv": package_dir / "verification_issues.csv",
        "summary": package_dir / "summary.md",
        "manifest": package_dir / "manifest.json",
        "manifest_sha256": package_dir / "manifest.sha256",
    }

    outputs["integrity_records_json"].write_text(
        json.dumps([record.model_dump(mode="json") for record in records], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _records_frame(records).to_csv(outputs["integrity_records_csv"], index=False)
    outputs["seals_json"].write_text(
        json.dumps([seal.model_dump(mode="json") for seal in seals], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _seals_frame(seals).to_csv(outputs["seals_csv"], index=False)
    outputs["verification_json"].write_text(_json_or_empty(verification), encoding="utf-8")
    _issues_frame(verification.issues if verification else []).to_csv(outputs["verification_issues_csv"], index=False)
    outputs["summary"].write_text(
        _export_summary_markdown(records, seals, verification, scope_from=scope_from, scope_to=scope_to),
        encoding="utf-8",
    )

    artifact_hashes = {
        name: _sha256_file(path)
        for name, path in outputs.items()
        if name != "manifest" and name != "manifest_sha256"
    }
    manifest_payload = {
        "created_at": timestamp.isoformat(),
        "scope_from": scope_from.isoformat() if scope_from else None,
        "scope_to": scope_to.isoformat() if scope_to else None,
        "record_types": [record_type.value for record_type in selected_types],
        "record_count": len(records),
        "seal_count": len(seals),
        "verification_status": verification.status.value if verification else "not_verified",
        "verification_id": verification.verification_id if verification else None,
        "artifacts": {name: str(path.name) for name, path in outputs.items() if name not in {"manifest", "manifest_sha256"}},
        "artifact_hashes": artifact_hashes,
    }
    outputs["manifest"].write_text(json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_hash = _sha256_file(outputs["manifest"])
    outputs["manifest_sha256"].write_text(f"{manifest_hash}  manifest.json\n", encoding="utf-8")
    package_hash = hashlib.sha256(
        json.dumps(
            {
                "manifest_hash": manifest_hash,
                "artifact_hashes": artifact_hashes,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    export_package = AuditExportPackage(
        export_id=f"audit-export-{timestamp.strftime('%Y%m%d%H%M%S')}",
        created_at=timestamp,
        output_dir=str(package_dir),
        manifest_path=str(outputs["manifest"]),
        package_hash=package_hash,
        scope_from=scope_from,
        scope_to=scope_to,
        record_types=selected_types,
        record_count=len(records),
        seal_count=len(seals),
        verification_id=verification.verification_id if verification else None,
        summary={
            "verification_status": verification.status.value if verification else "not_verified",
            "critical_issues": len([issue for issue in (verification.issues if verification else []) if issue.severity == "critical"]),
            "suspicious_records": len(_suspicious_records_frame(verification.issues if verification else [])),
            "manifest_hash": manifest_hash,
        },
    )
    return export_package, outputs


def _records_frame(records: list[AuditIntegrityRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "integrity_id": record.integrity_id,
                "sequence_number": record.sequence_number,
                "captured_at": record.captured_at.isoformat(),
                "record_type": record.record_type.value,
                "source_record_id": record.source_record_id,
                "source_version": record.source_version,
                "source_created_at": record.source_created_at.isoformat(),
                "payload_hash": record.payload_hash,
                "payload_size": record.payload_size,
                "previous_integrity_id": record.previous_integrity_id or "",
                "previous_record_hash": record.previous_record_hash or "",
                "record_hash": record.record_hash,
            }
            for record in records
        ]
    )


def _seals_frame(seals: list[AuditSeal]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "seal_id": seal.seal_id,
                "created_at": seal.created_at.isoformat(),
                "trigger_type": seal.trigger_type.value,
                "trigger_id": seal.trigger_id or "",
                "start_sequence": seal.start_sequence,
                "end_sequence": seal.end_sequence,
                "record_count": seal.record_count,
                "start_integrity_id": seal.start_integrity_id,
                "end_integrity_id": seal.end_integrity_id,
                "seal_hash": seal.seal_hash,
                "covered_record_types": "; ".join(record_type.value for record_type in seal.covered_record_types),
                "notes": seal.notes or "",
            }
            for seal in seals
        ]
    )


def _verification_frame(verifications: list[AuditVerificationRun]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "verification_id": verification.verification_id,
                "verified_at": verification.verified_at.isoformat(),
                "strict": verification.strict,
                "status": verification.status.value,
                "scope_from": verification.scope_from.isoformat() if verification.scope_from else "",
                "scope_to": verification.scope_to.isoformat() if verification.scope_to else "",
                "record_types": "; ".join(record_type.value for record_type in verification.record_types),
                "checked_records": verification.checked_records,
                "source_records_checked": verification.source_records_checked,
                "missing_source_records": verification.missing_source_records,
                "altered_source_records": verification.altered_source_records,
                "missing_integrity_records": verification.missing_integrity_records,
                "chain_breaks": verification.chain_breaks,
                "record_hash_mismatches": verification.record_hash_mismatches,
                "seal_failures": verification.seal_failures,
                "issues": len(verification.issues),
            }
            for verification in verifications
        ]
    )


def _issues_frame(issues: list[AuditIntegrityIssue]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "issue_id": issue.issue_id,
                "issue_type": issue.issue_type.value,
                "severity": issue.severity,
                "message": issue.message,
                "integrity_id": issue.integrity_id or "",
                "record_type": issue.record_type.value if issue.record_type else "",
                "source_record_id": issue.source_record_id or "",
                "sequence_number": issue.sequence_number or "",
                "seal_id": issue.seal_id or "",
            }
            for issue in issues
        ]
    )


def _exports_frame(exports: list[AuditExportPackage]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "export_id": export_package.export_id,
                "created_at": export_package.created_at.isoformat(),
                "output_dir": export_package.output_dir,
                "manifest_path": export_package.manifest_path,
                "package_hash": export_package.package_hash,
                "scope_from": export_package.scope_from.isoformat() if export_package.scope_from else "",
                "scope_to": export_package.scope_to.isoformat() if export_package.scope_to else "",
                "record_types": "; ".join(record_type.value for record_type in export_package.record_types),
                "record_count": export_package.record_count,
                "seal_count": export_package.seal_count,
                "verification_id": export_package.verification_id or "",
            }
            for export_package in exports
        ]
    )


def _suspicious_records_frame(issues: list[AuditIntegrityIssue]) -> pd.DataFrame:
    suspicious_types = {
        AuditIntegrityIssueType.ALTERED_SOURCE_RECORD,
        AuditIntegrityIssueType.MISSING_SOURCE_RECORD,
        AuditIntegrityIssueType.MISSING_INTEGRITY_RECORD,
        AuditIntegrityIssueType.CHAIN_BREAK,
        AuditIntegrityIssueType.RECORD_HASH_MISMATCH,
        AuditIntegrityIssueType.SEAL_MISMATCH,
    }
    return _issues_frame([issue for issue in issues if issue.issue_type in suspicious_types])


def _summary_markdown(summary: dict[str, object]) -> str:
    lines = [
        "# Audit Integrity Summary",
        "",
        f"Integrity records: {summary['integrity_records']}",
        f"Latest sequence: {summary['latest_sequence']}",
        f"Seals: {summary['seals']}",
        f"Verification runs: {summary['verifications']}",
        f"Failed verification runs: {summary['failed_verifications']}",
        f"Export packages: {summary['exports']}",
        f"Latest status: {summary['latest_status']}",
        f"Latest checked records: {summary['latest_checked_records']}",
        f"Latest issues: {summary['latest_issues']}",
        f"Altered source records: {summary['latest_altered_source_records']}",
        f"Missing source records: {summary['latest_missing_source_records']}",
        f"Missing integrity records: {summary['latest_missing_integrity_records']}",
        f"Chain breaks: {summary['latest_chain_breaks']}",
        f"Seal failures: {summary['latest_seal_failures']}",
    ]
    return "\n".join(lines) + "\n"


def _latest_verification_markdown(verification: AuditVerificationRun | None) -> str:
    if verification is None:
        return "# Latest Audit Verification\n\nNo verification run has been recorded yet.\n"
    lines = [
        "# Latest Audit Verification",
        "",
        f"Verification id: {verification.verification_id}",
        f"Verified at: {verification.verified_at.isoformat()}",
        f"Status: {verification.status.value}",
        f"Strict: {verification.strict}",
        f"Checked records: {verification.checked_records}",
        f"Source records checked: {verification.source_records_checked}",
        f"Missing source records: {verification.missing_source_records}",
        f"Altered source records: {verification.altered_source_records}",
        f"Missing integrity records: {verification.missing_integrity_records}",
        f"Chain breaks: {verification.chain_breaks}",
        f"Record hash mismatches: {verification.record_hash_mismatches}",
        f"Seal failures: {verification.seal_failures}",
        "",
        "Issues:",
    ]
    if verification.issues:
        lines.extend(f"- [{issue.severity}] {issue.message}" for issue in verification.issues)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _export_summary_markdown(
    records: list[AuditIntegrityRecord],
    seals: list[AuditSeal],
    verification: AuditVerificationRun | None,
    *,
    scope_from: datetime | None = None,
    scope_to: datetime | None = None,
) -> str:
    lines = [
        "# Audit Evidence Export",
        "",
        f"Exported at: {datetime.now(timezone.utc).isoformat()}",
        f"Scope from: {scope_from.isoformat() if scope_from else 'full'}",
        f"Scope to: {scope_to.isoformat() if scope_to else 'full'}",
        f"Integrity records: {len(records)}",
        f"Seals: {len(seals)}",
        f"Verification status: {verification.status.value if verification else 'not_verified'}",
        f"Verification id: {verification.verification_id if verification else 'n/a'}",
        f"Issues: {len(verification.issues) if verification else 0}",
    ]
    return "\n".join(lines) + "\n"


def _json_or_empty(value: object | None) -> str:
    if value is None:
        return "{}\n"
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")  # type: ignore[call-arg]
    else:
        payload = value
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
