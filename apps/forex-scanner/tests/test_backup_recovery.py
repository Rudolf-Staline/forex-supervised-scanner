"""Tests for local backup, disaster recovery, and continuity workflows."""

from __future__ import annotations

from datetime import datetime, timezone

from app.backup.recovery import (
    BackupScopeItem,
    BackupVerificationStatus,
    ContinuityMode,
    RecoveryValidationResult,
    RecoveryValidationStatus,
    create_backup_package,
    restore_backup_to_active,
    restore_backup_to_review,
    validate_recovered_state,
    verify_backup_package,
)
from app.execution.operator_identity import authenticate_operator, resolve_identity
from app.execution.operator_workflows import OperatorActionType, OperatorWorkflowContext, live_authorization_block_reasons, record_operator_action
from app.execution.operations import OperatorControlState
from app.reporting.backup import generate_backup_recovery_report
from app.storage.database import Database


def _seed_action(settings, database: Database, reason: str = "backup seed") -> str:
    now = datetime.now(timezone.utc)
    identities = database.sync_operator_identities(settings)
    supervisor = resolve_identity(identities, "supervisor")
    assert supervisor is not None
    session = authenticate_operator(
        supervisor,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now,
    )
    database.save_operator_auth_session(session)
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.SESSION_CLOSED,
        mode="broker_sandbox",
        reason=reason,
        auth_context=None,
        now=now,
    )
    database.save_operator_actions([action])
    return action.action_id


def test_backup_package_creation_manifest_and_verification(settings, tmp_path) -> None:
    database = Database(tmp_path / "active.sqlite")
    _seed_action(settings, database)

    manifest, package_path = create_backup_package(
        database,
        settings,
        output_dir=tmp_path / "backups",
        label="unit-test",
        project_root=tmp_path,
    )
    verification = verify_backup_package(package_path)

    assert package_path.exists()
    assert manifest.package_sha256 is not None
    assert BackupScopeItem.ACTIVE_DATABASE in manifest.scope
    assert BackupScopeItem.INTEGRITY_METADATA in manifest.scope
    assert manifest.database_sha256
    assert manifest.sqlite_integrity_status == "ok"
    assert verification.status == BackupVerificationStatus.PASSED
    assert verification.sqlite_integrity_status == "ok"
    assert verification.audit_verification_status == "passed"


def test_backup_restore_review_is_non_destructive(settings, tmp_path) -> None:
    database = Database(tmp_path / "active.sqlite")
    action_id = _seed_action(settings, database)
    manifest, package_path = create_backup_package(
        database,
        settings,
        output_dir=tmp_path / "backups",
        project_root=tmp_path,
    )

    restore, validation = restore_backup_to_review(package_path, tmp_path / "restore_review", settings)

    assert restore.status == "restored_for_review"
    assert restore.mode == ContinuityMode.RESTORE_REVIEW
    assert validation is not None
    assert validation.mode == ContinuityMode.RESTORE_REVIEW
    assert validation.status == RecoveryValidationStatus.PASSED
    assert (tmp_path / "restore_review" / manifest.backup_id / "state" / "forex_scanner.sqlite").exists()
    assert any(action.action_id == action_id for action in database.load_operator_actions())


def test_active_restore_requires_explicit_enable_and_confirmation(settings, tmp_path) -> None:
    source = Database(tmp_path / "source.sqlite")
    _seed_action(settings, source, reason="source")
    manifest, package_path = create_backup_package(source, settings, output_dir=tmp_path / "backups", project_root=tmp_path)
    target = Database(tmp_path / "target.sqlite")
    _seed_action(settings, target, reason="target")

    blocked, blocked_validation = restore_backup_to_active(package_path, target.path, settings, confirm=True, project_root=tmp_path)

    assert blocked.status == "blocked"
    assert blocked_validation is not None
    assert blocked_validation.mode == ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW

    adjusted = settings.model_copy(deep=True)
    adjusted.backup_recovery.allow_active_restore = True
    restored, pending = restore_backup_to_active(package_path, target.path, adjusted, confirm=True, project_root=tmp_path)

    assert restored.status == "active_restored_pending_validation"
    assert restored.safety_backup_path is not None
    assert pending is not None
    assert pending.status == RecoveryValidationStatus.PENDING
    assert pending.sensitive_actions_blocked
    validation = validate_recovered_state(target.path, adjusted, save_state=True, project_root=tmp_path)
    assert validation.status == RecoveryValidationStatus.PASSED
    assert validation.mode == ContinuityMode.NORMAL
    assert not validation.sensitive_actions_blocked


def test_recovery_validation_blocks_corrupted_database(settings, tmp_path) -> None:
    corrupted = tmp_path / "corrupted.sqlite"
    corrupted.write_text("not sqlite", encoding="utf-8")

    validation = validate_recovered_state(corrupted, settings, save_state=False, project_root=tmp_path)

    assert validation.status == RecoveryValidationStatus.FAILED
    assert validation.mode == ContinuityMode.BLOCKED_PENDING_OPERATOR_REVIEW
    assert validation.sensitive_actions_blocked
    assert validation.blockers


def test_recovery_pending_state_blocks_broker_live_authorization(settings, tmp_path) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_live"
    adjusted.execution_capabilities.broker_live_enabled = True
    adjusted.broker.live_enabled = True
    adjusted.broker.provider = "mt5"
    pending = RecoveryValidationResult(
        validation_id="validation-1",
        validated_at=datetime.now(timezone.utc),
        mode=ContinuityMode.POST_RESTORE_VALIDATION,
        status=RecoveryValidationStatus.PENDING,
        database_path=str(tmp_path / "active.sqlite"),
        sqlite_integrity_status="pending",
        sensitive_actions_blocked=True,
        blockers=["post-restore validation has not completed"],
    )
    context = OperatorWorkflowContext(
        controls=OperatorControlState(updated_at=datetime.now(timezone.utc)),
        latest_recovery_validation=pending,
    )

    reasons = live_authorization_block_reasons(adjusted, None, None, context)

    assert any("service continuity mode" in reason for reason in reasons)
    assert any("post-restore validation" in reason for reason in reasons)


def test_backup_report_generation(settings, tmp_path) -> None:
    database = Database(tmp_path / "active.sqlite")
    _seed_action(settings, database)
    manifest, package_path = create_backup_package(database, settings, output_dir=tmp_path / "backups", project_root=tmp_path)
    verification = verify_backup_package(package_path)
    restore, validation = restore_backup_to_review(package_path, tmp_path / "restore_review", settings)

    outputs = generate_backup_recovery_report(
        settings,
        [manifest],
        tmp_path / "reports",
        latest_verification=verification,
        latest_restore=restore,
        latest_recovery_validation=validation,
    )

    assert outputs["summary_markdown"].exists()
    assert outputs["backup_inventory_csv"].exists()
    assert outputs["backup_coverage_markdown"].exists()
    assert outputs["latest_backup_verification_markdown"].exists()
    assert outputs["latest_restore_result_markdown"].exists()
    assert outputs["recovery_validation_markdown"].exists()
