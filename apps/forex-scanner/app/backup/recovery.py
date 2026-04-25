"""Local backup, restore, disaster-recovery, and continuity helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from app.archive.retention import list_archive_manifests
from app.audit.integrity import AuditVerificationStatus
from app.backup.types import ContinuityMode, RecoveryValidationResult, RecoveryValidationStatus
from app.config.settings import AppSettings, PROJECT_ROOT
from app.execution.models import TradeEvent, TradeEventType
from app.execution.operations import AlertSeverity, AlertStatus, BrokerIncidentSeverity, BrokerIncidentStatus
from app.storage.database import Database


BACKUP_FORMAT_VERSION = "1"


class BackupScopeItem(str, Enum):
    """Critical local state included in backup packages."""

    ACTIVE_DATABASE = "active_database"
    AUDIT_JOURNAL_RECORDS = "audit_journal_records"
    INTEGRITY_METADATA = "integrity_metadata"
    INCIDENTS_ALERTS = "incidents_alerts"
    MONITORING_HISTORY = "monitoring_history"
    OPERATOR_SESSION_STATE = "operator_session_state"
    CONFIG_SNAPSHOT = "config_snapshot"
    ARCHIVE_MANIFESTS = "archive_manifests"
    CRITICAL_REPORTS = "critical_reports"


class BackupVerificationStatus(str, Enum):
    """Backup verification outcome."""

    PASSED = "passed"
    FAILED = "failed"


class BackupFileEntry(BaseModel):
    """One file included in a backup package."""

    scope: BackupScopeItem
    source_path: str
    archive_path: str
    sha256: str
    size_bytes: int


class BackupManifest(BaseModel):
    """Reviewable manifest for one local backup package."""

    backup_id: str
    created_at: datetime
    format_version: str = BACKUP_FORMAT_VERSION
    label: str | None = None
    reason: str | None = None
    package_path: str
    package_sha256: str | None = None
    scope: list[BackupScopeItem] = Field(default_factory=list)
    files: list[BackupFileEntry] = Field(default_factory=list)
    database_path: str | None = None
    database_sha256: str | None = None
    sqlite_integrity_status: str | None = None
    audit_verification_id: str | None = None
    audit_verification_status: str | None = None
    archive_manifest_count: int = 0
    critical_report_count: int = 0
    config_snapshot_hash: str | None = None
    notes: list[str] = Field(default_factory=list)


class BackupVerificationResult(BaseModel):
    """Result of verifying a local backup package."""

    verification_id: str
    verified_at: datetime
    status: BackupVerificationStatus
    backup_id: str | None = None
    package_path: str
    package_sha256: str | None = None
    checked_files: int = 0
    sqlite_integrity_status: str | None = None
    audit_verification_status: str | None = None
    issues: list[str] = Field(default_factory=list)


class BackupRestoreResult(BaseModel):
    """Result of restoring a backup package."""

    restore_id: str
    restored_at: datetime
    backup_id: str
    package_path: str
    mode: ContinuityMode
    status: str
    restore_path: str | None = None
    active_database_path: str | None = None
    safety_backup_path: str | None = None
    verification_status: BackupVerificationStatus
    warnings: list[str] = Field(default_factory=list)


def create_backup_package(
    database: Database,
    settings: AppSettings,
    *,
    output_dir: Path | None = None,
    label: str | None = None,
    reason: str | None = None,
    pre_maintenance: bool = False,
    include_reports: bool | None = None,
    project_root: Path = PROJECT_ROOT,
    now: datetime | None = None,
) -> tuple[BackupManifest, Path]:
    """Create a structured local backup package for critical operational state."""

    timestamp = now or datetime.now(timezone.utc)
    backup_settings = settings.backup_recovery
    target_dir = output_dir or _project_path(backup_settings.backup_output_dir, project_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    backup_id = _package_id(backup_settings.backup_name_prefix, timestamp, label)
    package_path = target_dir / f"{backup_id}.zip"
    notes: list[str] = []
    scope = _backup_scope(settings, include_reports=include_reports)
    if pre_maintenance:
        notes.append("pre-maintenance backup")

    database.save_trade_events(
        [
            TradeEvent(
                event_id=str(uuid.uuid4()),
                trade_id="backup-recovery",
                event_type=TradeEventType.BROKER_RECOVERY_ACTION,
                occurred_at=timestamp,
                symbol="SYSTEM",
                status="backup_created",
                reason=reason or "local operational backup package created",
                payload={"backup_id": backup_id, "label": label or "", "pre_maintenance": pre_maintenance},
            )
        ]
    )
    audit_verification = None
    if settings.audit_integrity.enabled and backup_settings.require_audit_verification_before_backup:
        audit_verification = database.verify_audit_integrity(strict=settings.audit_integrity.strict_verification, save_result=True)
        if audit_verification.status != AuditVerificationStatus.PASSED:
            notes.append("audit integrity verification failed before backup; package still created for recovery evidence")

    with TemporaryDirectory() as temp_name:
        temp_dir = Path(temp_name)
        package_root = temp_dir / backup_id
        package_root.mkdir(parents=True, exist_ok=True)
        files: list[BackupFileEntry] = []

        db_copy = package_root / "state" / "forex_scanner.sqlite"
        db_copy.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_backup(database.path, db_copy)
        sqlite_status = _sqlite_integrity_status(db_copy)
        db_entry = _file_entry(BackupScopeItem.ACTIVE_DATABASE, database.path, db_copy, package_root)
        files.append(db_entry)

        integrity_dir = package_root / "integrity"
        integrity_dir.mkdir()
        _write_json(integrity_dir / "audit_seals.json", [seal.model_dump(mode="json") for seal in database.load_audit_seals()])
        _write_json(integrity_dir / "audit_export_packages.json", [item.model_dump(mode="json") for item in database.load_audit_export_packages()])
        if audit_verification is not None:
            _write_json(integrity_dir / "audit_verification.json", audit_verification.model_dump(mode="json"))
        for path in sorted(integrity_dir.rglob("*")):
            if path.is_file():
                files.append(_file_entry(BackupScopeItem.INTEGRITY_METADATA, path, path, package_root))

        config_hash = None
        if backup_settings.include_config_snapshot:
            config_dir = package_root / "config"
            config_dir.mkdir()
            settings_path = config_dir / "settings_snapshot.json"
            _write_json(settings_path, settings.model_dump(mode="json"))
            config_hash = _sha256_file(settings_path)
            files.append(_file_entry(BackupScopeItem.CONFIG_SNAPSHOT, settings_path, settings_path, package_root))

        archive_manifest_count = 0
        if backup_settings.include_archive_manifests:
            archive_dir = package_root / "archive_manifests"
            archive_dir.mkdir()
            manifests = list_archive_manifests(_project_path(settings.retention_archive.archive_output_dir, project_root))
            _write_json(archive_dir / "archive_inventory.json", [manifest.model_dump(mode="json") for manifest in manifests])
            archive_manifest_count = len(manifests)
            files.append(_file_entry(BackupScopeItem.ARCHIVE_MANIFESTS, archive_dir / "archive_inventory.json", archive_dir / "archive_inventory.json", package_root))
            for source in _project_path(settings.retention_archive.archive_output_dir, project_root).glob("*.manifest.json"):
                target = archive_dir / source.name
                shutil.copy2(source, target)
                files.append(_file_entry(BackupScopeItem.ARCHIVE_MANIFESTS, source, target, package_root))
            for source in _project_path(settings.retention_archive.archive_output_dir, project_root).glob("*.sha256"):
                target = archive_dir / source.name
                shutil.copy2(source, target)
                files.append(_file_entry(BackupScopeItem.ARCHIVE_MANIFESTS, source, target, package_root))

        critical_report_count = 0
        if _include_reports_enabled(settings, include_reports):
            reports_root = _project_path("reports", project_root)
            if reports_root.exists():
                reports_dir = package_root / "reports"
                for source in reports_root.rglob("*"):
                    if not source.is_file():
                        continue
                    relative = source.relative_to(reports_root)
                    target = reports_dir / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    files.append(_file_entry(BackupScopeItem.CRITICAL_REPORTS, source, target, package_root))
                    critical_report_count += 1

        manifest = BackupManifest(
            backup_id=backup_id,
            created_at=timestamp,
            label=label,
            reason=reason,
            package_path=str(package_path),
            scope=scope,
            files=sorted(files, key=lambda item: item.archive_path),
            database_path=str(database.path),
            database_sha256=db_entry.sha256,
            sqlite_integrity_status=sqlite_status,
            audit_verification_id=audit_verification.verification_id if audit_verification else None,
            audit_verification_status=audit_verification.status.value if audit_verification else None,
            archive_manifest_count=archive_manifest_count,
            critical_report_count=critical_report_count,
            config_snapshot_hash=config_hash,
            notes=notes,
        )
        manifest_path = package_root / "manifest.json"
        _write_json(manifest_path, manifest.model_dump(mode="json"))
        (package_root / "manifest.sha256").write_text(f"{_sha256_file(manifest_path)}  manifest.json\n", encoding="utf-8")
        _write_text(package_root / "README.md", _backup_readme(manifest))

        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package_root).as_posix())

    package_hash = _sha256_file(package_path)
    manifest = manifest.model_copy(update={"package_sha256": package_hash})
    _write_json(package_path.with_suffix(".manifest.json"), manifest.model_dump(mode="json"))
    package_path.with_suffix(".sha256").write_text(f"{package_hash}  {package_path.name}\n", encoding="utf-8")
    return manifest, package_path


def list_backup_manifests(backup_dir: Path) -> list[BackupManifest]:
    """Load manifests for local backup packages."""

    if not backup_dir.exists():
        return []
    manifests: list[BackupManifest] = []
    for package_path in sorted(backup_dir.glob("*.zip")):
        manifest = inspect_backup_manifest(package_path)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def inspect_backup_manifest(package_path: Path) -> BackupManifest | None:
    """Read one backup manifest from sidecar or package contents."""

    sidecar = package_path.with_suffix(".manifest.json")
    if sidecar.exists():
        return BackupManifest.model_validate_json(sidecar.read_text(encoding="utf-8"))
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            return BackupManifest.model_validate_json(archive.read("manifest.json").decode("utf-8"))
    except (FileNotFoundError, KeyError, zipfile.BadZipFile, OSError):
        return None


def verify_backup_package(package_path: Path) -> BackupVerificationResult:
    """Verify package hashes, SQLite state, and embedded audit integrity."""

    timestamp = datetime.now(timezone.utc)
    issues: list[str] = []
    checked_files = 0
    sqlite_status: str | None = None
    audit_status: str | None = None
    package_hash: str | None = None
    manifest = inspect_backup_manifest(package_path)
    try:
        package_hash = _sha256_file(package_path)
        if manifest is None:
            issues.append("backup manifest is missing or unreadable")
        elif manifest.package_sha256 and manifest.package_sha256 != package_hash:
            issues.append("sidecar package hash does not match backup bytes")
        with zipfile.ZipFile(package_path, "r") as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                issues.append(f"zip CRC check failed for {bad_member}")
            names = set(archive.namelist())
            if "manifest.json" not in names:
                issues.append("manifest.json is missing")
            if "manifest.sha256" in names and "manifest.json" in names:
                expected = archive.read("manifest.sha256").decode("utf-8").split()[0]
                actual = hashlib.sha256(archive.read("manifest.json")).hexdigest()
                if expected != actual:
                    issues.append("manifest.sha256 does not match manifest.json")
            if manifest is not None:
                checked_files = len(manifest.files)
                for entry in manifest.files:
                    if entry.archive_path not in names:
                        issues.append(f"backup file missing: {entry.archive_path}")
                        continue
                    actual = hashlib.sha256(archive.read(entry.archive_path)).hexdigest()
                    if actual != entry.sha256:
                        issues.append(f"backup file hash mismatch: {entry.archive_path}")
                if "state/forex_scanner.sqlite" not in names:
                    issues.append("active database backup is missing")
                else:
                    with TemporaryDirectory() as temp_name:
                        target = Path(temp_name) / "forex_scanner.sqlite"
                        target.write_bytes(archive.read("state/forex_scanner.sqlite"))
                        sqlite_status = _sqlite_integrity_status(target)
                        if sqlite_status != "ok":
                            issues.append(f"SQLite integrity check failed: {sqlite_status}")
                        try:
                            verification = Database(target).verify_audit_integrity(save_result=False)
                            audit_status = verification.status.value
                            if verification.status != AuditVerificationStatus.PASSED:
                                issues.append("restored database audit verification failed")
                        except Exception as exc:
                            issues.append(f"restored database audit verification failed: {exc}")
    except (FileNotFoundError, zipfile.BadZipFile, OSError, json.JSONDecodeError) as exc:
        issues.append(str(exc))
    return BackupVerificationResult(
        verification_id=str(uuid.uuid4()),
        verified_at=timestamp,
        status=BackupVerificationStatus.FAILED if issues else BackupVerificationStatus.PASSED,
        backup_id=manifest.backup_id if manifest else None,
        package_path=str(package_path),
        package_sha256=package_hash,
        checked_files=checked_files,
        sqlite_integrity_status=sqlite_status,
        audit_verification_status=audit_status,
        issues=issues,
    )


def restore_backup_to_review(
    package_path: Path,
    restore_dir: Path,
    settings: AppSettings,
    *,
    overwrite: bool = False,
) -> tuple[BackupRestoreResult, RecoveryValidationResult | None]:
    """Extract a backup package into a non-destructive review/staging directory."""

    verification = verify_backup_package(package_path)
    manifest = inspect_backup_manifest(package_path)
    if manifest is None:
        return (
            BackupRestoreResult(
                restore_id=str(uuid.uuid4()),
                restored_at=datetime.now(timezone.utc),
                backup_id="unknown",
                package_path=str(package_path),
                mode=ContinuityMode.RESTORE_REVIEW,
                status="failed",
                restore_path=str(restore_dir),
                verification_status=verification.status,
                warnings=["backup manifest is missing or unreadable"],
            ),
            None,
        )
    target = restore_dir / manifest.backup_id
    warnings: list[str] = []
    if settings.backup_recovery.verify_before_restore and verification.status != BackupVerificationStatus.PASSED:
        return (
            BackupRestoreResult(
                restore_id=str(uuid.uuid4()),
                restored_at=datetime.now(timezone.utc),
                backup_id=manifest.backup_id,
                package_path=str(package_path),
                mode=ContinuityMode.RESTORE_REVIEW,
                status="failed",
                restore_path=str(target),
                verification_status=verification.status,
                warnings=verification.issues,
            ),
            None,
        )
    if target.exists():
        if not overwrite:
            return (
                BackupRestoreResult(
                    restore_id=str(uuid.uuid4()),
                    restored_at=datetime.now(timezone.utc),
                    backup_id=manifest.backup_id,
                    package_path=str(package_path),
                    mode=ContinuityMode.RESTORE_REVIEW,
                    status="blocked",
                    restore_path=str(target),
                    verification_status=verification.status,
                    warnings=["restore review target already exists"],
                ),
                None,
            )
        shutil.rmtree(target)
        warnings.append("existing restore review directory was replaced")
    _extract_zip_safe(package_path, target)
    db_path = target / "state" / "forex_scanner.sqlite"
    validation = validate_recovered_state(db_path, settings, mode=ContinuityMode.RESTORE_REVIEW, save_state=False)
    _write_text(target / "RESTORE_REVIEW.md", _restore_readme(manifest, validation))
    return (
        BackupRestoreResult(
            restore_id=str(uuid.uuid4()),
            restored_at=datetime.now(timezone.utc),
            backup_id=manifest.backup_id,
            package_path=str(package_path),
            mode=ContinuityMode.RESTORE_REVIEW,
            status="restored_for_review",
            restore_path=str(target),
            verification_status=verification.status,
            warnings=warnings,
        ),
        validation,
    )


def restore_backup_to_active(
    package_path: Path,
    active_database_path: Path,
    settings: AppSettings,
    *,
    confirm: bool = False,
    project_root: Path = PROJECT_ROOT,
) -> tuple[BackupRestoreResult, RecoveryValidationResult | None]:
    """Explicitly restore the active database from a verified backup package."""

    verification = verify_backup_package(package_path)
    manifest = inspect_backup_manifest(package_path)
    if manifest is None:
        return (
            BackupRestoreResult(
                restore_id=str(uuid.uuid4()),
                restored_at=datetime.now(timezone.utc),
                backup_id="unknown",
                package_path=str(package_path),
                mode=ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW,
                status="failed",
                active_database_path=str(active_database_path),
                verification_status=verification.status,
                warnings=["backup manifest is missing or unreadable"],
            ),
            None,
        )
    warnings: list[str] = []
    if not settings.backup_recovery.allow_active_restore:
        warnings.append("active restore is disabled by backup_recovery.allow_active_restore")
    if settings.backup_recovery.active_restore_requires_confirmation and not confirm:
        warnings.append("active restore requires explicit confirmation")
    if settings.backup_recovery.verify_before_restore and verification.status != BackupVerificationStatus.PASSED:
        warnings.extend(verification.issues)
    if warnings:
        result = BackupRestoreResult(
            restore_id=str(uuid.uuid4()),
            restored_at=datetime.now(timezone.utc),
            backup_id=manifest.backup_id,
            package_path=str(package_path),
            mode=ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW,
            status="blocked",
            active_database_path=str(active_database_path),
            verification_status=verification.status,
            warnings=warnings,
        )
        validation = _blocked_validation(active_database_path, settings, warnings)
        write_recovery_state(validation, settings, project_root=project_root)
        return result, validation

    with TemporaryDirectory() as temp_name:
        extract_root = Path(temp_name) / manifest.backup_id
        _extract_zip_safe(package_path, extract_root)
        restored_db = extract_root / "state" / "forex_scanner.sqlite"
        if not restored_db.exists():
            result = BackupRestoreResult(
                restore_id=str(uuid.uuid4()),
                restored_at=datetime.now(timezone.utc),
                backup_id=manifest.backup_id,
                package_path=str(package_path),
                mode=ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW,
                status="failed",
                active_database_path=str(active_database_path),
                verification_status=verification.status,
                warnings=["backup package does not contain state/forex_scanner.sqlite"],
            )
            validation = _blocked_validation(active_database_path, settings, result.warnings)
            write_recovery_state(validation, settings, project_root=project_root)
            return result, validation
        active_database_path.parent.mkdir(parents=True, exist_ok=True)
        safety_path = None
        if active_database_path.exists():
            safety_dir = _project_path(settings.backup_recovery.pre_restore_backup_dir, project_root)
            safety_dir.mkdir(parents=True, exist_ok=True)
            safety_path = safety_dir / f"{active_database_path.stem}_pre_restore_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{active_database_path.suffix}"
            shutil.copy2(active_database_path, safety_path)
        shutil.copy2(restored_db, active_database_path)

    pending = RecoveryValidationResult(
        validation_id=str(uuid.uuid4()),
        validated_at=datetime.now(timezone.utc),
        mode=ContinuityMode.POST_RESTORE_VALIDATION,
        status=RecoveryValidationStatus.PENDING,
        database_path=str(active_database_path),
        sqlite_integrity_status="pending",
        sensitive_actions_blocked=True,
        blockers=["post-restore validation has not completed"],
        recommendations=["run backup_recovery.py post-restore-check before sensitive broker/live workflows"],
    )
    write_recovery_state(pending, settings, project_root=project_root)
    return (
        BackupRestoreResult(
            restore_id=str(uuid.uuid4()),
            restored_at=datetime.now(timezone.utc),
            backup_id=manifest.backup_id,
            package_path=str(package_path),
            mode=ContinuityMode.POST_RESTORE_VALIDATION,
            status="active_restored_pending_validation",
            active_database_path=str(active_database_path),
            safety_backup_path=str(safety_path) if safety_path else None,
            verification_status=verification.status,
        ),
        pending,
    )


def validate_recovered_state(
    database_path: Path,
    settings: AppSettings,
    *,
    mode: ContinuityMode | None = None,
    save_state: bool = True,
    project_root: Path = PROJECT_ROOT,
) -> RecoveryValidationResult:
    """Run local post-restore/startup validation and return continuity state."""

    blockers: list[str] = []
    warnings: list[str] = []
    recommendations: list[str] = []
    sqlite_status = _sqlite_integrity_status(database_path) if database_path.exists() else "missing_database"
    if sqlite_status != "ok":
        blockers.append(f"SQLite integrity check failed: {sqlite_status}")
    audit_status: str | None = None
    open_incidents = 0
    active_alerts = 0
    severe_anomalies = 0
    open_orders = 0
    if database_path.exists() and sqlite_status == "ok":
        try:
            database = Database(database_path)
            verification = database.verify_audit_integrity(save_result=False)
            audit_status = verification.status.value
            if verification.status != AuditVerificationStatus.PASSED:
                blockers.append("audit integrity verification failed")
            incidents = database.load_broker_incidents()
            alerts = database.load_operational_alerts()
            anomalies = database.load_reconciliation_anomalies()
            orders = database.load_broker_orders()
            open_incidents = len([item for item in incidents if item.status == BrokerIncidentStatus.OPEN])
            active_alerts = len([item for item in alerts if item.status == AlertStatus.ACTIVE])
            severe_anomalies = len([item for item in anomalies if item.severity in {"high", "critical"}])
            open_orders = len([item for item in orders if item.is_open])
            if any(item.status == BrokerIncidentStatus.OPEN and item.severity in {BrokerIncidentSeverity.HIGH, BrokerIncidentSeverity.CRITICAL} for item in incidents):
                blockers.append("high/critical incidents remain open")
            if any(item.status == AlertStatus.ACTIVE and item.severity in {AlertSeverity.HIGH, AlertSeverity.CRITICAL} for item in alerts):
                blockers.append("high/critical alerts remain active")
            if severe_anomalies:
                blockers.append("high/critical reconciliation anomalies remain unresolved")
            if open_orders:
                warnings.append("open broker orders exist and require reconciliation review")
        except Exception as exc:
            blockers.append(f"post-restore validation failed: {exc}")
    if blockers:
        status = RecoveryValidationStatus.FAILED
        resolved_mode = ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW
        recommendations.append("keep sensitive broker/live workflows blocked until blockers are resolved")
    elif warnings:
        status = RecoveryValidationStatus.WARNING
        resolved_mode = ContinuityMode.DEGRADED
        recommendations.append("continue supervised review before resuming sensitive workflows")
    else:
        status = RecoveryValidationStatus.PASSED
        resolved_mode = ContinuityMode.NORMAL
        recommendations.append("local continuity validation passed; continue normal guarded workflow")
    if mode == ContinuityMode.RESTORE_REVIEW:
        resolved_mode = ContinuityMode.RESTORE_REVIEW
    result = RecoveryValidationResult(
        validation_id=str(uuid.uuid4()),
        validated_at=datetime.now(timezone.utc),
        mode=resolved_mode,
        status=status,
        database_path=str(database_path),
        sqlite_integrity_status=sqlite_status,
        audit_verification_status=audit_status,
        open_incidents=open_incidents,
        active_alerts=active_alerts,
        severe_anomalies=severe_anomalies,
        open_broker_orders=open_orders,
        sensitive_actions_blocked=status == RecoveryValidationStatus.FAILED or resolved_mode in {
            ContinuityMode.RESTORE_REVIEW,
            ContinuityMode.POST_RESTORE_VALIDATION,
            ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW,
        },
        blockers=blockers,
        warnings=warnings,
        recommendations=recommendations,
    )
    if save_state:
        write_recovery_state(result, settings, project_root=project_root)
    return result


def load_recovery_state(settings: AppSettings, *, project_root: Path = PROJECT_ROOT) -> RecoveryValidationResult | None:
    """Load the latest service-continuity marker if one exists."""

    path = _project_path(settings.backup_recovery.recovery_state_path, project_root)
    if not path.exists():
        return None
    return RecoveryValidationResult.model_validate_json(path.read_text(encoding="utf-8"))


def write_recovery_state(
    result: RecoveryValidationResult,
    settings: AppSettings,
    *,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    """Persist the latest service-continuity marker."""

    path = _project_path(settings.backup_recovery.recovery_state_path, project_root)
    _write_json(path, result.model_dump(mode="json"))
    return path


def _backup_scope(settings: AppSettings, *, include_reports: bool | None) -> list[BackupScopeItem]:
    scope = [
        BackupScopeItem.ACTIVE_DATABASE,
        BackupScopeItem.AUDIT_JOURNAL_RECORDS,
        BackupScopeItem.INTEGRITY_METADATA,
        BackupScopeItem.INCIDENTS_ALERTS,
        BackupScopeItem.MONITORING_HISTORY,
        BackupScopeItem.OPERATOR_SESSION_STATE,
    ]
    if settings.backup_recovery.include_config_snapshot:
        scope.append(BackupScopeItem.CONFIG_SNAPSHOT)
    if settings.backup_recovery.include_archive_manifests:
        scope.append(BackupScopeItem.ARCHIVE_MANIFESTS)
    if _include_reports_enabled(settings, include_reports):
        scope.append(BackupScopeItem.CRITICAL_REPORTS)
    return scope


def _include_reports_enabled(settings: AppSettings, include_reports: bool | None) -> bool:
    return include_reports if include_reports is not None else settings.backup_recovery.include_critical_reports


def _file_entry(scope: BackupScopeItem, source_path: Path, package_path: Path, package_root: Path) -> BackupFileEntry:
    return BackupFileEntry(
        scope=scope,
        source_path=str(source_path),
        archive_path=package_path.relative_to(package_root).as_posix(),
        sha256=_sha256_file(package_path),
        size_bytes=package_path.stat().st_size,
    )


def _sqlite_backup(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _sqlite_integrity_status(path: Path) -> str:
    if not path.exists():
        return "missing"
    connection = None
    try:
        connection = sqlite3.connect(path)
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "no_result"
    except sqlite3.DatabaseError as exc:
        return f"error: {exc}"
    finally:
        if connection is not None:
            connection.close()


def _blocked_validation(database_path: Path, settings: AppSettings, blockers: list[str]) -> RecoveryValidationResult:
    return RecoveryValidationResult(
        validation_id=str(uuid.uuid4()),
        validated_at=datetime.now(timezone.utc),
        mode=ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW,
        status=RecoveryValidationStatus.FAILED,
        database_path=str(database_path),
        sqlite_integrity_status=_sqlite_integrity_status(database_path),
        sensitive_actions_blocked=True,
        blockers=blockers,
        recommendations=["resolve restore blockers before retrying active restore or broker/live workflows"],
    )


def _extract_zip_safe(package_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path, "r") as archive:
        for member in archive.infolist():
            _validate_zip_member(member.filename)
            archive.extract(member, target_dir)


def _validate_zip_member(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"unsafe backup member path: {name}")


def _backup_readme(manifest: BackupManifest) -> str:
    return "\n".join(
        [
            "# Forex Scanner Backup Package",
            "",
            f"Backup id: {manifest.backup_id}",
            f"Created at: {manifest.created_at.isoformat()}",
            f"SQLite integrity: {manifest.sqlite_integrity_status}",
            f"Audit verification: {manifest.audit_verification_status or 'not_run'}",
            "",
            "Restore into review/staging first. Active restore requires explicit confirmation and post-restore validation.",
        ]
    ) + "\n"


def _restore_readme(manifest: BackupManifest, validation: RecoveryValidationResult) -> str:
    return "\n".join(
        [
            "# Backup Restore Review",
            "",
            f"Backup id: {manifest.backup_id}",
            f"Validation status: {validation.status.value}",
            f"Continuity mode: {validation.mode.value}",
            "",
            "This directory is isolated review/staging output and should not be merged into active state directly.",
        ]
    ) + "\n"


def _package_id(prefix: str, timestamp: datetime, label: str | None) -> str:
    suffix = f"_{_slug(label)}" if label else ""
    return f"{_slug(prefix)}_{timestamp.strftime('%Y%m%dT%H%M%SZ')}{suffix}_{uuid.uuid4().hex[:8]}"


def _slug(value: str | None) -> str:
    if not value:
        return "backup"
    safe = "".join(char.lower() if char.isalnum() else "_" for char in value.strip())
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_") or "backup"


def _project_path(value: str, project_root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


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
