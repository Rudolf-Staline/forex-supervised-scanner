"""Local retention, archival, rotation planning, and review restore workflows."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from app.config.settings import AppSettings, PROJECT_ROOT, RetentionArchiveSettings
from app.storage.database import Database


ARCHIVE_FORMAT_VERSION = "1"


class RetentionRecordClass(str, Enum):
    """Operational record classes covered by local retention policy."""

    AUDIT_RECORDS = "audit_records"
    JOURNAL_EVENTS = "journal_events"
    ALERTS_INCIDENTS = "alerts_incidents"
    MONITORING_SNAPSHOTS = "monitoring_snapshots"
    SOAK_CAMPAIGN_OUTPUTS = "soak_campaign_outputs"
    REPORTS_EXPORTS = "reports_exports"
    CHECKPOINT_SEALS = "checkpoint_seals"


class ArchiveVerificationStatus(str, Enum):
    """Archive verification outcome."""

    PASSED = "passed"
    FAILED = "failed"


class RestoreMode(str, Enum):
    """Supported restore modes."""

    REVIEW = "review"
    REHYDRATE = "rehydrate"


class RetentionCandidate(BaseModel):
    """One database record or filesystem artifact eligible for archival."""

    candidate_id: str
    record_class: RetentionRecordClass
    source: str
    record_id: str
    created_at: datetime
    cutoff_at: datetime
    age_days: float
    payload_hash: str | None = None
    file_path: str | None = None
    relative_path: str | None = None
    size_bytes: int | None = None


class RetentionEvaluation(BaseModel):
    """Retention candidate summary for operator review."""

    evaluation_id: str
    generated_at: datetime
    cutoffs: dict[str, datetime]
    candidates: list[RetentionCandidate] = Field(default_factory=list)
    total_database_candidates: int = 0
    total_file_candidates: int = 0
    warnings: list[str] = Field(default_factory=list)


class ArchivedRecordReference(BaseModel):
    """Metadata for one archived record payload."""

    record_class: RetentionRecordClass
    source: str
    record_id: str
    created_at: datetime
    payload_hash: str


class ArchivedFileReference(BaseModel):
    """Metadata for one archived file artifact."""

    source_path: str
    archive_path: str
    relative_path: str
    modified_at: datetime
    size_bytes: int
    sha256: str


class ArchiveManifest(BaseModel):
    """Reviewable manifest for one local archive package."""

    archive_id: str
    created_at: datetime
    format_version: str = ARCHIVE_FORMAT_VERSION
    mode: str = "local"
    label: str | None = None
    package_path: str
    package_sha256: str | None = None
    scope_from: datetime | None = None
    scope_to: datetime | None = None
    record_counts: dict[str, int] = Field(default_factory=dict)
    file_count: int = 0
    records: list[ArchivedRecordReference] = Field(default_factory=list)
    files: list[ArchivedFileReference] = Field(default_factory=list)
    integrity_verification_id: str | None = None
    integrity_verification_status: str | None = None
    seal_count: int = 0
    preserved_integrity_metadata: bool = True
    rotation_safe: bool = True
    notes: list[str] = Field(default_factory=list)


class ArchiveVerificationResult(BaseModel):
    """Result of verifying one archive package and its manifest."""

    verification_id: str
    verified_at: datetime
    status: ArchiveVerificationStatus
    archive_path: str
    archive_id: str | None = None
    checked_files: int = 0
    checked_records: int = 0
    issues: list[str] = Field(default_factory=list)
    manifest_path: str | None = None
    package_sha256: str | None = None


class ArchiveRestoreResult(BaseModel):
    """Result of restoring an archive into a non-destructive review area."""

    restore_id: str
    restored_at: datetime
    mode: RestoreMode
    archive_id: str
    archive_path: str
    restore_path: str
    status: str
    verification_status: ArchiveVerificationStatus
    rehydrated_record_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class RotationPlan(BaseModel):
    """Safe rotation plan for active operational data."""

    plan_id: str
    generated_at: datetime
    dry_run: bool
    database_purge_allowed: bool
    file_rotation_allowed: bool
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    database_records_to_archive: int = 0
    files_to_rotate: int = 0
    blocked_actions: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class _RecordCollector:
    record_class: RetentionRecordClass
    source: str
    loader: str
    retention_attr: str


_DATABASE_COLLECTORS: tuple[_RecordCollector, ...] = (
    _RecordCollector(RetentionRecordClass.AUDIT_RECORDS, "audit_integrity_records", "load_audit_integrity_records", "audit_records_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "trade_events", "load_trade_events", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "trading_journal", "load_journal_entries", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "paper_orders", "load_paper_orders", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "paper_blocks", "load_paper_blocks", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "broker_orders", "load_broker_orders", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "pre_session_checklists", "load_pre_session_checklists", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "live_authorizations", "load_live_authorizations", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "trading_sessions", "load_trading_sessions", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "operator_actions", "load_operator_actions", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "handovers", "load_handovers", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "operator_auth_sessions", "load_operator_auth_sessions", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.JOURNAL_EVENTS, "approval_signatures", "load_approval_signatures", "journal_events_retention_days"),
    _RecordCollector(RetentionRecordClass.ALERTS_INCIDENTS, "broker_incidents", "load_broker_incidents", "alerts_incidents_retention_days"),
    _RecordCollector(RetentionRecordClass.ALERTS_INCIDENTS, "operational_alerts", "load_operational_alerts", "alerts_incidents_retention_days"),
    _RecordCollector(RetentionRecordClass.ALERTS_INCIDENTS, "reconciliation_anomalies", "load_reconciliation_anomalies", "alerts_incidents_retention_days"),
    _RecordCollector(RetentionRecordClass.MONITORING_SNAPSHOTS, "broker_health_snapshots", "load_broker_health_snapshots", "monitoring_snapshots_retention_days"),
    _RecordCollector(RetentionRecordClass.MONITORING_SNAPSHOTS, "operational_metrics", "load_operational_metrics", "monitoring_snapshots_retention_days"),
    _RecordCollector(RetentionRecordClass.SOAK_CAMPAIGN_OUTPUTS, "soak_campaigns", "load_soak_campaigns", "soak_campaign_retention_days"),
    _RecordCollector(RetentionRecordClass.SOAK_CAMPAIGN_OUTPUTS, "soak_runs", "load_soak_runs", "soak_campaign_retention_days"),
    _RecordCollector(RetentionRecordClass.SOAK_CAMPAIGN_OUTPUTS, "soak_samples", "load_soak_samples", "soak_campaign_retention_days"),
    _RecordCollector(RetentionRecordClass.SOAK_CAMPAIGN_OUTPUTS, "soak_anomalies", "load_soak_anomalies", "soak_campaign_retention_days"),
    _RecordCollector(RetentionRecordClass.REPORTS_EXPORTS, "audit_export_packages", "load_audit_export_packages", "reports_exports_retention_days"),
    _RecordCollector(RetentionRecordClass.CHECKPOINT_SEALS, "audit_seals", "load_audit_seals", "checkpoint_seals_retention_days"),
)


def collect_retention_candidates(
    database: Database,
    settings: AppSettings,
    *,
    now: datetime | None = None,
    project_root: Path = PROJECT_ROOT,
    include_files: bool = True,
) -> RetentionEvaluation:
    """Collect old operational records/files that should be archived."""

    timestamp = now or datetime.now(timezone.utc)
    cutoffs = _cutoffs(settings.retention_archive, timestamp)
    candidates: list[RetentionCandidate] = []
    warnings: list[str] = []

    for collector in _DATABASE_COLLECTORS:
        loader = getattr(database, collector.loader, None)
        if loader is None:
            warnings.append(f"loader {collector.loader} is unavailable")
            continue
        cutoff = cutoffs[collector.record_class.value]
        try:
            records = loader()
        except TypeError:
            records = loader(None)
        for record in records:
            created_at = _record_timestamp(record)
            if created_at is None:
                warnings.append(f"{collector.source} record missing timestamp; skipped")
                continue
            normalized = _utc(created_at)
            if normalized > cutoff:
                continue
            candidates.append(
                RetentionCandidate(
                    candidate_id=str(uuid.uuid4()),
                    record_class=collector.record_class,
                    source=collector.source,
                    record_id=_record_id(record),
                    created_at=normalized,
                    cutoff_at=cutoff,
                    age_days=round(max(0.0, (timestamp - normalized).total_seconds() / 86400.0), 2),
                    payload_hash=_sha256_text(_record_payload(record)),
                )
            )

    if include_files:
        candidates.extend(_collect_file_candidates(settings.retention_archive, timestamp, project_root))

    db_candidates = sum(1 for item in candidates if item.file_path is None)
    file_candidates = sum(1 for item in candidates if item.file_path is not None)
    return RetentionEvaluation(
        evaluation_id=str(uuid.uuid4()),
        generated_at=timestamp,
        cutoffs=cutoffs,
        candidates=sorted(candidates, key=lambda item: (item.record_class.value, item.created_at, item.source)),
        total_database_candidates=db_candidates,
        total_file_candidates=file_candidates,
        warnings=warnings,
    )


def create_archive_package(
    database: Database,
    settings: AppSettings,
    *,
    output_dir: Path | None = None,
    now: datetime | None = None,
    label: str | None = None,
    include_files: bool = True,
    project_root: Path = PROJECT_ROOT,
) -> tuple[ArchiveManifest, Path]:
    """Create a local ZIP archive package from current retention candidates."""

    timestamp = now or datetime.now(timezone.utc)
    archive_settings = settings.retention_archive
    target_dir = output_dir or _project_path(archive_settings.archive_output_dir, project_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    evaluation = collect_retention_candidates(database, settings, now=timestamp, project_root=project_root, include_files=include_files)
    selected = evaluation.candidates[: archive_settings.max_archive_records]
    archive_id = _archive_id(archive_settings.archive_name_prefix, timestamp, label)
    archive_path = target_dir / f"{archive_id}.zip"

    verification = None
    if settings.audit_integrity.enabled:
        verification = database.verify_audit_integrity(strict=settings.audit_integrity.strict_verification, save_result=True)

    records_by_class = _payloads_for_candidates(database, selected)
    record_refs: list[ArchivedRecordReference] = []
    file_refs: list[ArchivedFileReference] = []
    record_counts: dict[str, int] = {}
    manifest_notes: list[str] = []
    if len(evaluation.candidates) > len(selected):
        manifest_notes.append(f"archive capped at {archive_settings.max_archive_records} records; rerun to archive remaining candidates")

    with TemporaryDirectory() as temp_name:
        temp_dir = Path(temp_name)
        package_root = temp_dir / archive_id
        package_root.mkdir(parents=True, exist_ok=True)
        records_dir = package_root / "records"
        records_dir.mkdir()
        files_dir = package_root / "files"
        files_dir.mkdir()

        for record_class, rows in records_by_class.items():
            record_counts[record_class.value] = len(rows)
            if not rows:
                continue
            json_path = records_dir / f"{record_class.value}.json"
            csv_path = records_dir / f"{record_class.value}.csv"
            _write_json(json_path, rows)
            _write_record_csv(csv_path, rows)
            for row in rows:
                record_refs.append(
                    ArchivedRecordReference(
                        record_class=record_class,
                        source=str(row["source"]),
                        record_id=str(row["record_id"]),
                        created_at=_parse_datetime(str(row["created_at"])),
                        payload_hash=str(row["payload_hash"]),
                    )
                )

        for candidate in selected:
            if candidate.file_path is None:
                continue
            source_path = Path(candidate.file_path)
            if not source_path.exists() or not source_path.is_file():
                continue
            archive_member = Path("files") / _safe_relative(candidate.relative_path or source_path.name)
            destination = package_root / archive_member
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            file_refs.append(
                ArchivedFileReference(
                    source_path=str(source_path),
                    archive_path=archive_member.as_posix(),
                    relative_path=candidate.relative_path or source_path.name,
                    modified_at=candidate.created_at,
                    size_bytes=source_path.stat().st_size,
                    sha256=_sha256_file(source_path),
                )
            )

        seals = database.load_audit_seals()
        _write_json(records_dir / "audit_seals.json", [seal.model_dump(mode="json") for seal in seals])
        if verification is not None:
            _write_json(package_root / "audit_verification.json", verification.model_dump(mode="json"))
        _write_json(package_root / "retention_evaluation.json", evaluation.model_dump(mode="json"))

        manifest = ArchiveManifest(
            archive_id=archive_id,
            created_at=timestamp,
            label=label,
            package_path=str(archive_path),
            scope_from=min((item.created_at for item in selected), default=None),
            scope_to=max((item.created_at for item in selected), default=None),
            record_counts=record_counts,
            file_count=len(file_refs),
            records=sorted(record_refs, key=lambda item: (item.record_class.value, item.created_at, item.record_id)),
            files=sorted(file_refs, key=lambda item: item.relative_path),
            integrity_verification_id=verification.verification_id if verification else None,
            integrity_verification_status=verification.status.value if verification else None,
            seal_count=len(seals),
            preserved_integrity_metadata=True,
            rotation_safe=verification.status.value == "passed" if verification else True,
            notes=manifest_notes,
        )
        manifest_path = package_root / "manifest.json"
        _write_json(manifest_path, manifest.model_dump(mode="json"))
        manifest_hash_path = package_root / "manifest.sha256"
        manifest_hash_path.write_text(f"{_sha256_file(manifest_path)}  manifest.json\n", encoding="utf-8")
        _write_text(package_root / "README.md", _archive_readme(manifest))

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package_root).as_posix())

    package_sha = _sha256_file(archive_path)
    manifest = manifest.model_copy(update={"package_sha256": package_sha})
    sidecar = archive_path.with_suffix(".manifest.json")
    _write_json(sidecar, manifest.model_dump(mode="json"))
    archive_path.with_suffix(".sha256").write_text(f"{package_sha}  {archive_path.name}\n", encoding="utf-8")
    return manifest, archive_path


def list_archive_manifests(archive_dir: Path) -> list[ArchiveManifest]:
    """Load sidecar or embedded manifests from local archive packages."""

    if not archive_dir.exists():
        return []
    manifests: list[ArchiveManifest] = []
    for package_path in sorted(archive_dir.glob("*.zip")):
        manifest = inspect_archive_manifest(package_path)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def inspect_archive_manifest(archive_path: Path) -> ArchiveManifest | None:
    """Read one archive manifest without extracting active records."""

    sidecar = archive_path.with_suffix(".manifest.json")
    if sidecar.exists():
        return ArchiveManifest.model_validate_json(sidecar.read_text(encoding="utf-8"))
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            with archive.open("manifest.json") as handle:
                payload = handle.read().decode("utf-8")
    except (FileNotFoundError, zipfile.BadZipFile, KeyError):
        return None
    return ArchiveManifest.model_validate_json(payload)


def verify_archive_package(archive_path: Path) -> ArchiveVerificationResult:
    """Verify archive readability, manifest hashes, and payload references."""

    timestamp = datetime.now(timezone.utc)
    issues: list[str] = []
    checked_files = 0
    checked_records = 0
    manifest: ArchiveManifest | None = None
    package_sha: str | None = None
    try:
        package_sha = _sha256_file(archive_path)
        manifest = inspect_archive_manifest(archive_path)
        if manifest is None:
            issues.append("manifest is missing or unreadable")
        elif manifest.package_sha256 and manifest.package_sha256 != package_sha:
            issues.append("sidecar package hash does not match archive bytes")
        with zipfile.ZipFile(archive_path, "r") as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                issues.append(f"zip CRC check failed for {bad_member}")
            names = set(archive.namelist())
            if "manifest.json" not in names:
                issues.append("manifest.json is missing from archive package")
            if "manifest.sha256" in names and "manifest.json" in names:
                expected = archive.read("manifest.sha256").decode("utf-8").split()[0]
                actual = hashlib.sha256(archive.read("manifest.json")).hexdigest()
                if expected != actual:
                    issues.append("manifest.sha256 does not match manifest.json")
            if manifest is not None:
                checked_records = len(manifest.records)
                checked_files = len(manifest.files)
                for file_ref in manifest.files:
                    if file_ref.archive_path not in names:
                        issues.append(f"archived file missing: {file_ref.archive_path}")
                        continue
                    actual = hashlib.sha256(archive.read(file_ref.archive_path)).hexdigest()
                    if actual != file_ref.sha256:
                        issues.append(f"archived file hash mismatch: {file_ref.archive_path}")
                for record_class, count in manifest.record_counts.items():
                    if count <= 0:
                        continue
                    member = f"records/{record_class}.json"
                    if member not in names:
                        issues.append(f"record payload missing: {member}")
                        continue
                    payload = json.loads(archive.read(member).decode("utf-8"))
                    if len(payload) != count:
                        issues.append(f"record count mismatch for {record_class}: manifest={count} actual={len(payload)}")
                    for row in payload:
                        payload_hash = _sha256_text(_canonical_json(row.get("payload", {})))
                        if payload_hash != row.get("payload_hash"):
                            issues.append(f"record payload hash mismatch: {row.get('source')}:{row.get('record_id')}")
    except (FileNotFoundError, zipfile.BadZipFile, OSError, json.JSONDecodeError) as exc:
        issues.append(str(exc))

    status = ArchiveVerificationStatus.FAILED if issues else ArchiveVerificationStatus.PASSED
    return ArchiveVerificationResult(
        verification_id=str(uuid.uuid4()),
        verified_at=timestamp,
        status=status,
        archive_path=str(archive_path),
        archive_id=manifest.archive_id if manifest else None,
        checked_files=checked_files,
        checked_records=checked_records,
        issues=issues,
        manifest_path=str(archive_path.with_suffix(".manifest.json")) if archive_path.with_suffix(".manifest.json").exists() else None,
        package_sha256=package_sha,
    )


def restore_archive_for_review(
    archive_path: Path,
    restore_dir: Path,
    *,
    overwrite: bool = False,
) -> ArchiveRestoreResult:
    """Extract an archive into an isolated review directory after verification."""

    verification = verify_archive_package(archive_path)
    manifest = inspect_archive_manifest(archive_path)
    if manifest is None:
        return ArchiveRestoreResult(
            restore_id=str(uuid.uuid4()),
            restored_at=datetime.now(timezone.utc),
            mode=RestoreMode.REVIEW,
            archive_id="unknown",
            archive_path=str(archive_path),
            restore_path=str(restore_dir),
            status="failed",
            verification_status=verification.status,
            warnings=["archive manifest is missing or unreadable"],
        )
    target = restore_dir / manifest.archive_id
    warnings: list[str] = []
    if verification.status != ArchiveVerificationStatus.PASSED:
        return ArchiveRestoreResult(
            restore_id=str(uuid.uuid4()),
            restored_at=datetime.now(timezone.utc),
            mode=RestoreMode.REVIEW,
            archive_id=manifest.archive_id,
            archive_path=str(archive_path),
            restore_path=str(target),
            status="failed",
            verification_status=verification.status,
            warnings=verification.issues,
        )
    if target.exists():
        if not overwrite:
            return ArchiveRestoreResult(
                restore_id=str(uuid.uuid4()),
                restored_at=datetime.now(timezone.utc),
                mode=RestoreMode.REVIEW,
                archive_id=manifest.archive_id,
                archive_path=str(archive_path),
                restore_path=str(target),
                status="blocked",
                verification_status=verification.status,
                warnings=["restore target already exists; pass overwrite only for review-area replacement"],
            )
        shutil.rmtree(target)
        warnings.append("existing restore review directory was replaced")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            _validate_zip_member(member.filename)
            archive.extract(member, target)
    _write_text(target / "RESTORE_REVIEW.md", _restore_review_readme(manifest))
    return ArchiveRestoreResult(
        restore_id=str(uuid.uuid4()),
        restored_at=datetime.now(timezone.utc),
        mode=RestoreMode.REVIEW,
        archive_id=manifest.archive_id,
        archive_path=str(archive_path),
        restore_path=str(target),
        status="restored_for_review",
        verification_status=verification.status,
        rehydrated_record_count=len(manifest.records),
        warnings=warnings,
    )


def build_rotation_plan(
    evaluation: RetentionEvaluation,
    settings: RetentionArchiveSettings,
    *,
    dry_run: bool | None = None,
) -> RotationPlan:
    """Create a safe rotation plan without deleting active protected evidence."""

    is_dry_run = settings.rotation_dry_run_default if dry_run is None else dry_run
    counts: dict[str, int] = {}
    for candidate in evaluation.candidates:
        counts[candidate.record_class.value] = counts.get(candidate.record_class.value, 0) + 1
    db_records = sum(1 for item in evaluation.candidates if item.file_path is None)
    files = sum(1 for item in evaluation.candidates if item.file_path is not None)
    blocked: list[str] = []
    recommended: list[str] = []
    if db_records and not settings.allow_database_purge:
        blocked.append("database record purge is disabled; archive packages preserve evidence but active SQLite rows are retained")
        recommended.append("review archive package verification before considering any future database compaction")
    if files and (is_dry_run or not settings.allow_file_rotation):
        blocked.append("file rotation is dry-run or disabled; report/export files are identified but not moved")
        recommended.append("run archive create, verify the package, then enable file rotation only during a supervised maintenance window")
    if not evaluation.candidates:
        recommended.append("no retention candidates currently exceed configured windows")
    else:
        recommended.append("create and verify an archive package for all listed candidates")
    return RotationPlan(
        plan_id=str(uuid.uuid4()),
        generated_at=evaluation.generated_at,
        dry_run=is_dry_run,
        database_purge_allowed=settings.allow_database_purge,
        file_rotation_allowed=settings.allow_file_rotation and not is_dry_run,
        candidate_counts=counts,
        database_records_to_archive=db_records,
        files_to_rotate=files,
        blocked_actions=blocked,
        recommended_actions=recommended,
    )


def write_archive_verification_result(result: ArchiveVerificationResult, output_dir: Path) -> dict[str, Path]:
    """Write a compact verification result for operator review."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "archive_verification.json"
    md_path = output_dir / "archive_verification.md"
    _write_json(json_path, result.model_dump(mode="json"))
    lines = [
        "# Archive Verification",
        "",
        f"- Status: {result.status.value}",
        f"- Archive: {result.archive_path}",
        f"- Archive id: {result.archive_id or 'unknown'}",
        f"- Checked records: {result.checked_records}",
        f"- Checked files: {result.checked_files}",
        f"- Issues: {len(result.issues)}",
    ]
    if result.issues:
        lines.extend(["", "## Issues", *[f"- {issue}" for issue in result.issues]])
    _write_text(md_path, "\n".join(lines) + "\n")
    return {"verification_json": json_path, "verification_markdown": md_path}


def write_restore_result(result: ArchiveRestoreResult, output_dir: Path) -> dict[str, Path]:
    """Write restore-for-review status files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "archive_restore.json"
    md_path = output_dir / "archive_restore.md"
    _write_json(json_path, result.model_dump(mode="json"))
    lines = [
        "# Archive Restore",
        "",
        f"- Status: {result.status}",
        f"- Mode: {result.mode.value}",
        f"- Archive id: {result.archive_id}",
        f"- Restore path: {result.restore_path}",
        f"- Verification: {result.verification_status.value}",
        f"- Rehydrated records: {result.rehydrated_record_count}",
    ]
    if result.warnings:
        lines.extend(["", "## Warnings", *[f"- {warning}" for warning in result.warnings]])
    _write_text(md_path, "\n".join(lines) + "\n")
    return {"restore_json": json_path, "restore_markdown": md_path}


def _payloads_for_candidates(database: Database, candidates: list[RetentionCandidate]) -> dict[RetentionRecordClass, list[dict[str, Any]]]:
    candidate_keys = {(item.source, item.record_id) for item in candidates if item.file_path is None}
    rows_by_class: dict[RetentionRecordClass, list[dict[str, Any]]] = {record_class: [] for record_class in RetentionRecordClass}
    if not candidate_keys:
        return rows_by_class
    for collector in _DATABASE_COLLECTORS:
        loader = getattr(database, collector.loader, None)
        if loader is None:
            continue
        try:
            records = loader()
        except TypeError:
            records = loader(None)
        for record in records:
            record_id = _record_id(record)
            key = (collector.source, record_id)
            if key not in candidate_keys:
                continue
            payload = _model_payload(record)
            rows_by_class[collector.record_class].append(
                {
                    "record_class": collector.record_class.value,
                    "source": collector.source,
                    "record_id": record_id,
                    "created_at": _utc(_record_timestamp(record) or datetime.now(timezone.utc)).isoformat(),
                    "payload_hash": _sha256_text(_canonical_json(payload)),
                    "payload": payload,
                }
            )
    return rows_by_class


def _collect_file_candidates(
    settings: RetentionArchiveSettings,
    now: datetime,
    project_root: Path,
) -> list[RetentionCandidate]:
    cutoff = now - timedelta(days=settings.reports_exports_retention_days)
    size_threshold = int(settings.report_file_size_rotation_mb * 1024 * 1024)
    roots = [
        _project_path("reports", project_root),
        _project_path(settings.archive_output_dir, project_root).parent,
    ]
    excluded = {
        _project_path(settings.archive_output_dir, project_root).resolve(),
        _project_path(settings.restore_output_dir, project_root).resolve(),
    }
    candidates: list[RetentionCandidate] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if any(_is_relative_to(resolved, excluded_path) for excluded_path in excluded):
                continue
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            size_bytes = path.stat().st_size
            if modified_at > cutoff and size_bytes < size_threshold:
                continue
            relative = _relative_to_project(path, project_root)
            candidates.append(
                RetentionCandidate(
                    candidate_id=str(uuid.uuid4()),
                    record_class=RetentionRecordClass.REPORTS_EXPORTS,
                    source="filesystem",
                    record_id=relative,
                    created_at=modified_at,
                    cutoff_at=cutoff,
                    age_days=round(max(0.0, (now - modified_at).total_seconds() / 86400.0), 2),
                    payload_hash=_sha256_file(path),
                    file_path=str(path),
                    relative_path=relative,
                    size_bytes=size_bytes,
                )
            )
    return candidates


def _cutoffs(settings: RetentionArchiveSettings, now: datetime) -> dict[str, datetime]:
    return {
        RetentionRecordClass.AUDIT_RECORDS.value: now - timedelta(days=settings.audit_records_retention_days),
        RetentionRecordClass.JOURNAL_EVENTS.value: now - timedelta(days=settings.journal_events_retention_days),
        RetentionRecordClass.ALERTS_INCIDENTS.value: now - timedelta(days=settings.alerts_incidents_retention_days),
        RetentionRecordClass.MONITORING_SNAPSHOTS.value: now - timedelta(days=settings.monitoring_snapshots_retention_days),
        RetentionRecordClass.SOAK_CAMPAIGN_OUTPUTS.value: now - timedelta(days=settings.soak_campaign_retention_days),
        RetentionRecordClass.REPORTS_EXPORTS.value: now - timedelta(days=settings.reports_exports_retention_days),
        RetentionRecordClass.CHECKPOINT_SEALS.value: now - timedelta(days=settings.checkpoint_seals_retention_days),
    }


def _record_timestamp(record: Any) -> datetime | None:
    for name in (
        "captured_at",
        "occurred_at",
        "created_at",
        "opened_at",
        "authenticated_at",
        "started_at",
        "sampled_at",
        "detected_at",
        "recorded_at",
        "updated_at",
    ):
        value = getattr(record, name, None)
        if isinstance(value, datetime):
            return value
    return None


def _record_id(record: Any) -> str:
    for name in (
        "integrity_id",
        "event_id",
        "entry_id",
        "journal_id",
        "order_id",
        "block_id",
        "incident_id",
        "metric_id",
        "alert_id",
        "snapshot_id",
        "anomaly_id",
        "checklist_id",
        "authorization_id",
        "session_id",
        "action_id",
        "handover_id",
        "auth_session_id",
        "signature_id",
        "campaign_id",
        "run_id",
        "sample_id",
        "seal_id",
        "export_id",
    ):
        value = getattr(record, name, None)
        if value:
            return str(value)
    return _sha256_text(_record_payload(record))[:16]


def _record_payload(record: Any) -> str:
    return _canonical_json(_model_payload(record))


def _model_payload(record: Any) -> Any:
    if hasattr(record, "model_dump"):
        return record.model_dump(mode="json")
    return record


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _write_record_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["record_class", "source", "record_id", "created_at", "payload_hash"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames or []})


def _archive_readme(manifest: ArchiveManifest) -> str:
    return "\n".join(
        [
            "# Forex Scanner Operational Archive",
            "",
            f"Archive id: {manifest.archive_id}",
            f"Created at: {manifest.created_at.isoformat()}",
            f"Records: {len(manifest.records)}",
            f"Files: {manifest.file_count}",
            f"Integrity verification: {manifest.integrity_verification_status or 'not_run'}",
            "",
            "This package is intended for local evidence review and restore-for-review workflows.",
            "Verify `manifest.sha256` and the sidecar `.sha256` file before relying on the package.",
        ]
    ) + "\n"


def _restore_review_readme(manifest: ArchiveManifest) -> str:
    return "\n".join(
        [
            "# Restore Review Area",
            "",
            f"Archive id: {manifest.archive_id}",
            f"Created at: {manifest.created_at.isoformat()}",
            "",
            "This directory is an isolated review extraction. Do not merge it into active state without a separate supervised restore plan.",
        ]
    ) + "\n"


def _archive_id(prefix: str, timestamp: datetime, label: str | None) -> str:
    safe_label = f"_{_slug(label)}" if label else ""
    return f"{_slug(prefix)}_{timestamp.strftime('%Y%m%dT%H%M%SZ')}{safe_label}_{uuid.uuid4().hex[:8]}"


def _slug(value: str | None) -> str:
    if not value:
        return "archive"
    safe = "".join(char.lower() if char.isalnum() else "_" for char in value.strip())
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_") or "archive"


def _safe_relative(value: str) -> Path:
    path = Path(value.replace("\\", "/"))
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return Path(_slug(path.name))
    return path


def _validate_zip_member(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"unsafe archive member path: {name}")


def _relative_to_project(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _project_path(value: str, project_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
