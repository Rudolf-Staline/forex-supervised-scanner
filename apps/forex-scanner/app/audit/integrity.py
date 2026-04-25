"""Tamper-evident audit chaining, sealing, verification, and export models."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

AUDIT_CHAIN_NAME = "audit"


class AuditProtectedRecordType(str, Enum):
    """Critical record classes protected by the local audit chain."""

    TRADE_EVENT = "trade_event"
    BROKER_INCIDENT = "broker_incident"
    OPERATOR_CONTROL = "operator_control"
    PRE_SESSION_CHECKLIST = "pre_session_checklist"
    LIVE_AUTHORIZATION = "live_authorization"
    TRADING_SESSION = "trading_session"
    OPERATOR_ACTION = "operator_action"
    HANDOVER = "handover"
    OPERATOR_AUTH_SESSION = "operator_auth_session"
    APPROVAL_SIGNATURE = "approval_signature"


class AuditSealTrigger(str, Enum):
    """Supported integrity checkpoint triggers."""

    MANUAL = "manual"
    DAILY = "daily"
    SESSION_CLOSE = "session_close"
    HANDOVER = "handover"
    SOAK_CAMPAIGN = "soak_campaign"
    INCIDENT_CLOSE = "incident_close"


class AuditIntegrityIssueType(str, Enum):
    """Verification failures or suspicious integrity conditions."""

    CHAIN_BREAK = "chain_break"
    RECORD_HASH_MISMATCH = "record_hash_mismatch"
    MISSING_SOURCE_RECORD = "missing_source_record"
    ALTERED_SOURCE_RECORD = "altered_source_record"
    MISSING_INTEGRITY_RECORD = "missing_integrity_record"
    SEAL_MISMATCH = "seal_mismatch"


class AuditVerificationStatus(str, Enum):
    """Overall outcome for one integrity verification run."""

    PASSED = "passed"
    FAILED = "failed"


class AuditSourceRecordSnapshot(BaseModel):
    """One current source-table record snapshot used for verification/export."""

    record_type: AuditProtectedRecordType
    source_record_id: str
    source_created_at: datetime
    payload_json: str


class AuditIntegrityRecord(BaseModel):
    """Append-only audit ledger record containing an immutable payload snapshot."""

    integrity_id: str
    chain_name: str = AUDIT_CHAIN_NAME
    sequence_number: int = Field(ge=1)
    captured_at: datetime
    record_type: AuditProtectedRecordType
    source_record_id: str
    source_version: int = Field(default=1, ge=1)
    source_created_at: datetime
    payload_hash: str
    payload_size: int = Field(ge=0)
    previous_integrity_id: str | None = None
    previous_record_hash: str | None = None
    record_hash: str
    payload_json: str


class AuditSeal(BaseModel):
    """Integrity checkpoint covering a contiguous range of chain records."""

    seal_id: str
    created_at: datetime
    trigger_type: AuditSealTrigger
    trigger_id: str | None = None
    notes: str | None = None
    start_sequence: int = Field(ge=1)
    end_sequence: int = Field(ge=1)
    record_count: int = Field(ge=1)
    start_integrity_id: str
    end_integrity_id: str
    start_record_hash: str
    end_record_hash: str
    covered_record_types: list[AuditProtectedRecordType] = Field(default_factory=list)
    seal_hash: str


class AuditIntegrityIssue(BaseModel):
    """One verification finding for audit integrity review."""

    issue_id: str
    issue_type: AuditIntegrityIssueType
    severity: str
    message: str
    integrity_id: str | None = None
    record_type: AuditProtectedRecordType | None = None
    source_record_id: str | None = None
    sequence_number: int | None = None
    seal_id: str | None = None


class AuditVerificationRun(BaseModel):
    """Persisted result of one full or scoped audit verification pass."""

    verification_id: str
    verified_at: datetime
    strict: bool = True
    status: AuditVerificationStatus
    scope_from: datetime | None = None
    scope_to: datetime | None = None
    record_types: list[AuditProtectedRecordType] = Field(default_factory=list)
    checked_records: int = Field(default=0, ge=0)
    source_records_checked: int = Field(default=0, ge=0)
    missing_source_records: int = Field(default=0, ge=0)
    altered_source_records: int = Field(default=0, ge=0)
    missing_integrity_records: int = Field(default=0, ge=0)
    chain_breaks: int = Field(default=0, ge=0)
    record_hash_mismatches: int = Field(default=0, ge=0)
    seal_failures: int = Field(default=0, ge=0)
    issues: list[AuditIntegrityIssue] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class AuditExportPackage(BaseModel):
    """Persisted summary for one immutable-style audit evidence export."""

    export_id: str
    created_at: datetime
    output_dir: str
    manifest_path: str
    package_hash: str
    scope_from: datetime | None = None
    scope_to: datetime | None = None
    record_types: list[AuditProtectedRecordType] = Field(default_factory=list)
    record_count: int = Field(default=0, ge=0)
    seal_count: int = Field(default=0, ge=0)
    verification_id: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


def canonicalize_payload_json(payload: str | dict[str, Any] | list[Any] | BaseModel) -> str:
    """Return stable JSON text for hashing and immutable-style export."""

    if isinstance(payload, BaseModel):
        normalized: Any = payload.model_dump(mode="json")
    elif isinstance(payload, str):
        try:
            normalized = json.loads(payload)
        except json.JSONDecodeError:
            normalized = payload
    else:
        normalized = payload
    if isinstance(normalized, str):
        return json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def compute_payload_hash(payload_json: str | dict[str, Any] | list[Any] | BaseModel) -> str:
    """Return the SHA-256 digest for one canonical payload."""

    canonical = canonicalize_payload_json(payload_json)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_record_hash(
    *,
    chain_name: str,
    sequence_number: int,
    captured_at: datetime,
    record_type: AuditProtectedRecordType,
    source_record_id: str,
    source_version: int,
    source_created_at: datetime,
    payload_hash: str,
    previous_integrity_id: str | None,
    previous_record_hash: str | None,
) -> str:
    """Return the chained SHA-256 digest for one integrity record."""

    payload = {
        "chain_name": chain_name,
        "sequence_number": sequence_number,
        "captured_at": _iso(captured_at),
        "record_type": record_type.value,
        "source_record_id": source_record_id,
        "source_version": source_version,
        "source_created_at": _iso(source_created_at),
        "payload_hash": payload_hash,
        "previous_integrity_id": previous_integrity_id,
        "previous_record_hash": previous_record_hash,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_audit_integrity_record(
    *,
    record_type: AuditProtectedRecordType,
    source_record_id: str,
    source_created_at: datetime,
    payload_json: str | dict[str, Any] | list[Any] | BaseModel,
    sequence_number: int,
    source_version: int = 1,
    previous_integrity_id: str | None = None,
    previous_record_hash: str | None = None,
    captured_at: datetime | None = None,
    chain_name: str = AUDIT_CHAIN_NAME,
) -> AuditIntegrityRecord:
    """Build one append-only integrity record from a protected source payload."""

    timestamp = captured_at or datetime.now(timezone.utc)
    canonical_payload = canonicalize_payload_json(payload_json)
    payload_hash = compute_payload_hash(canonical_payload)
    return AuditIntegrityRecord(
        integrity_id=str(uuid.uuid4()),
        chain_name=chain_name,
        sequence_number=sequence_number,
        captured_at=timestamp,
        record_type=record_type,
        source_record_id=source_record_id,
        source_version=source_version,
        source_created_at=source_created_at,
        payload_hash=payload_hash,
        payload_size=len(canonical_payload.encode("utf-8")),
        previous_integrity_id=previous_integrity_id,
        previous_record_hash=previous_record_hash,
        record_hash=compute_record_hash(
            chain_name=chain_name,
            sequence_number=sequence_number,
            captured_at=timestamp,
            record_type=record_type,
            source_record_id=source_record_id,
            source_version=source_version,
            source_created_at=source_created_at,
            payload_hash=payload_hash,
            previous_integrity_id=previous_integrity_id,
            previous_record_hash=previous_record_hash,
        ),
        payload_json=canonical_payload,
    )


def compute_seal_hash(
    *,
    created_at: datetime,
    trigger_type: AuditSealTrigger,
    trigger_id: str | None,
    start_sequence: int,
    end_sequence: int,
    record_count: int,
    start_integrity_id: str,
    end_integrity_id: str,
    start_record_hash: str,
    end_record_hash: str,
    covered_record_types: list[AuditProtectedRecordType],
    notes: str | None = None,
) -> str:
    """Return the SHA-256 digest for one integrity seal."""

    payload = {
        "created_at": _iso(created_at),
        "trigger_type": trigger_type.value,
        "trigger_id": trigger_id,
        "start_sequence": start_sequence,
        "end_sequence": end_sequence,
        "record_count": record_count,
        "start_integrity_id": start_integrity_id,
        "end_integrity_id": end_integrity_id,
        "start_record_hash": start_record_hash,
        "end_record_hash": end_record_hash,
        "covered_record_types": sorted(record_type.value for record_type in covered_record_types),
        "notes": notes,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_audit_seal(
    records: list[AuditIntegrityRecord],
    *,
    trigger_type: AuditSealTrigger,
    trigger_id: str | None = None,
    notes: str | None = None,
    created_at: datetime | None = None,
) -> AuditSeal | None:
    """Build one seal for a contiguous range of integrity records."""

    if not records:
        return None
    ordered = sorted(records, key=lambda record: record.sequence_number)
    timestamp = created_at or datetime.now(timezone.utc)
    covered_types = sorted({record.record_type for record in ordered}, key=lambda record_type: record_type.value)
    first = ordered[0]
    last = ordered[-1]
    return AuditSeal(
        seal_id=str(uuid.uuid4()),
        created_at=timestamp,
        trigger_type=trigger_type,
        trigger_id=trigger_id,
        notes=notes,
        start_sequence=first.sequence_number,
        end_sequence=last.sequence_number,
        record_count=len(ordered),
        start_integrity_id=first.integrity_id,
        end_integrity_id=last.integrity_id,
        start_record_hash=first.record_hash,
        end_record_hash=last.record_hash,
        covered_record_types=covered_types,
        seal_hash=compute_seal_hash(
            created_at=timestamp,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            start_sequence=first.sequence_number,
            end_sequence=last.sequence_number,
            record_count=len(ordered),
            start_integrity_id=first.integrity_id,
            end_integrity_id=last.integrity_id,
            start_record_hash=first.record_hash,
            end_record_hash=last.record_hash,
            covered_record_types=covered_types,
            notes=notes,
        ),
    )


def verify_integrity_records(
    records: list[AuditIntegrityRecord],
    *,
    current_source_records: list[AuditSourceRecordSnapshot] | None = None,
    seals: list[AuditSeal] | None = None,
    strict: bool = True,
    verified_at: datetime | None = None,
    scope_from: datetime | None = None,
    scope_to: datetime | None = None,
    record_types: list[AuditProtectedRecordType] | None = None,
    visible_integrity_ids: set[str] | None = None,
) -> AuditVerificationRun:
    """Verify the chain, source payload integrity, and seal ranges."""

    timestamp = verified_at or datetime.now(timezone.utc)
    ordered = sorted(records, key=lambda record: record.sequence_number)
    visible_ids = visible_integrity_ids or {record.integrity_id for record in ordered}
    issues: list[AuditIntegrityIssue] = []

    for index, record in enumerate(ordered):
        expected_record_hash = compute_record_hash(
            chain_name=record.chain_name,
            sequence_number=record.sequence_number,
            captured_at=record.captured_at,
            record_type=record.record_type,
            source_record_id=record.source_record_id,
            source_version=record.source_version,
            source_created_at=record.source_created_at,
            payload_hash=record.payload_hash,
            previous_integrity_id=record.previous_integrity_id,
            previous_record_hash=record.previous_record_hash,
        )
        if expected_record_hash != record.record_hash and record.integrity_id in visible_ids:
            issues.append(
                _issue(
                    AuditIntegrityIssueType.RECORD_HASH_MISMATCH,
                    "critical",
                    f"integrity record {record.integrity_id} hash does not match its stored fields",
                    record=record,
                )
            )
        if index == 0:
            continue
        previous = ordered[index - 1]
        if record.sequence_number != previous.sequence_number + 1 and record.integrity_id in visible_ids:
            issues.append(
                _issue(
                    AuditIntegrityIssueType.CHAIN_BREAK,
                    "critical",
                    f"sequence jump detected between {previous.sequence_number} and {record.sequence_number}",
                    record=record,
                )
            )
        if record.previous_integrity_id != previous.integrity_id and record.integrity_id in visible_ids:
            issues.append(
                _issue(
                    AuditIntegrityIssueType.CHAIN_BREAK,
                    "critical",
                    f"integrity record {record.integrity_id} does not reference the expected previous record",
                    record=record,
                )
            )
        if record.previous_record_hash != previous.record_hash and record.integrity_id in visible_ids:
            issues.append(
                _issue(
                    AuditIntegrityIssueType.CHAIN_BREAK,
                    "critical",
                    f"integrity record {record.integrity_id} previous hash does not match record {previous.integrity_id}",
                    record=record,
                )
            )

    current_sources = current_source_records or []
    current_source_map = {
        (source.record_type.value, source.source_record_id): source for source in current_sources
    }
    latest_by_source: dict[tuple[str, str], AuditIntegrityRecord] = {}
    for record in ordered:
        key = (record.record_type.value, record.source_record_id)
        previous = latest_by_source.get(key)
        if previous is None or previous.sequence_number < record.sequence_number:
            latest_by_source[key] = record

    for key, record in latest_by_source.items():
        source = current_source_map.get(key)
        if source is None:
            if record.integrity_id in visible_ids:
                issues.append(
                    _issue(
                        AuditIntegrityIssueType.MISSING_SOURCE_RECORD,
                        "warning" if not strict else "critical",
                        f"{record.record_type.value} {record.source_record_id} is missing from active storage",
                        record=record,
                    )
                )
            continue
        current_hash = compute_payload_hash(source.payload_json)
        if current_hash != record.payload_hash and record.integrity_id in visible_ids:
            issues.append(
                _issue(
                    AuditIntegrityIssueType.ALTERED_SOURCE_RECORD,
                    "critical",
                    f"{record.record_type.value} {record.source_record_id} payload differs from the latest chained snapshot",
                    record=record,
                )
            )

    latest_source_keys = set(latest_by_source)
    for key, source in current_source_map.items():
        if key not in latest_source_keys:
            issues.append(
                _issue(
                    AuditIntegrityIssueType.MISSING_INTEGRITY_RECORD,
                    "warning" if not strict else "critical",
                    f"{source.record_type.value} {source.source_record_id} exists but is not covered by the integrity chain",
                    record_type=source.record_type,
                    source_record_id=source.source_record_id,
                )
            )

    for seal in seals or []:
        covered = [record for record in ordered if seal.start_sequence <= record.sequence_number <= seal.end_sequence]
        expected_hash = compute_seal_hash(
            created_at=seal.created_at,
            trigger_type=seal.trigger_type,
            trigger_id=seal.trigger_id,
            start_sequence=seal.start_sequence,
            end_sequence=seal.end_sequence,
            record_count=seal.record_count,
            start_integrity_id=seal.start_integrity_id,
            end_integrity_id=seal.end_integrity_id,
            start_record_hash=seal.start_record_hash,
            end_record_hash=seal.end_record_hash,
            covered_record_types=seal.covered_record_types,
            notes=seal.notes,
        )
        seal_overlap = bool(covered)
        if not seal_overlap:
            continue
        if expected_hash != seal.seal_hash:
            issues.append(
                _issue(
                    AuditIntegrityIssueType.SEAL_MISMATCH,
                    "critical",
                    f"seal {seal.seal_id} hash does not match its stored fields",
                    seal_id=seal.seal_id,
                )
            )
            continue
        first = covered[0]
        last = covered[-1]
        covered_types = sorted({record.record_type for record in covered}, key=lambda record_type: record_type.value)
        if (
            len(covered) != seal.record_count
            or first.integrity_id != seal.start_integrity_id
            or last.integrity_id != seal.end_integrity_id
            or first.record_hash != seal.start_record_hash
            or last.record_hash != seal.end_record_hash
            or [record_type.value for record_type in covered_types] != [record_type.value for record_type in seal.covered_record_types]
        ):
            issues.append(
                _issue(
                    AuditIntegrityIssueType.SEAL_MISMATCH,
                    "critical",
                    f"seal {seal.seal_id} no longer matches the covered integrity range",
                    seal_id=seal.seal_id,
                )
            )

    status = AuditVerificationStatus.FAILED if issues else AuditVerificationStatus.PASSED
    chain_breaks = sum(1 for issue in issues if issue.issue_type == AuditIntegrityIssueType.CHAIN_BREAK)
    record_hash_mismatches = sum(1 for issue in issues if issue.issue_type == AuditIntegrityIssueType.RECORD_HASH_MISMATCH)
    missing_source_records = sum(1 for issue in issues if issue.issue_type == AuditIntegrityIssueType.MISSING_SOURCE_RECORD)
    altered_source_records = sum(1 for issue in issues if issue.issue_type == AuditIntegrityIssueType.ALTERED_SOURCE_RECORD)
    missing_integrity_records = sum(1 for issue in issues if issue.issue_type == AuditIntegrityIssueType.MISSING_INTEGRITY_RECORD)
    seal_failures = sum(1 for issue in issues if issue.issue_type == AuditIntegrityIssueType.SEAL_MISMATCH)

    return AuditVerificationRun(
        verification_id=str(uuid.uuid4()),
        verified_at=timestamp,
        strict=strict,
        status=status,
        scope_from=scope_from,
        scope_to=scope_to,
        record_types=record_types or [],
        checked_records=len([record for record in ordered if record.integrity_id in visible_ids]),
        source_records_checked=len(current_sources),
        missing_source_records=missing_source_records,
        altered_source_records=altered_source_records,
        missing_integrity_records=missing_integrity_records,
        chain_breaks=chain_breaks,
        record_hash_mismatches=record_hash_mismatches,
        seal_failures=seal_failures,
        issues=issues,
        summary={
            "latest_sequence": ordered[-1].sequence_number if ordered else 0,
            "covered_seals": len(seals or []),
            "strict": strict,
        },
    )


def effective_verification_status(verification: AuditVerificationRun | None) -> AuditVerificationStatus | None:
    """Return the current status for a stored verification run."""

    if verification is None:
        return None
    return verification.status


def _issue(
    issue_type: AuditIntegrityIssueType,
    severity: str,
    message: str,
    *,
    record: AuditIntegrityRecord | None = None,
    record_type: AuditProtectedRecordType | None = None,
    source_record_id: str | None = None,
    seal_id: str | None = None,
) -> AuditIntegrityIssue:
    return AuditIntegrityIssue(
        issue_id=str(uuid.uuid4()),
        issue_type=issue_type,
        severity=severity,
        message=message,
        integrity_id=record.integrity_id if record else None,
        record_type=record.record_type if record else record_type,
        source_record_id=record.source_record_id if record else source_record_id,
        sequence_number=record.sequence_number if record else None,
        seal_id=seal_id,
    )


def _iso(value: datetime) -> str:
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.isoformat()
