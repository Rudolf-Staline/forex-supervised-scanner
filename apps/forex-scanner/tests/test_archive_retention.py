"""Tests for local archival, retention, rotation, and restore-for-review workflows."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app.archive.retention import (
    ArchiveVerificationStatus,
    build_rotation_plan,
    collect_retention_candidates,
    create_archive_package,
    inspect_archive_manifest,
    restore_archive_for_review,
    verify_archive_package,
)
from app.execution.operator_identity import authenticate_operator, resolve_identity
from app.execution.operator_workflows import OperatorActionType, record_operator_action
from app.reporting.archive import generate_archive_report
from app.storage.database import Database


def _auth_context(settings, database: Database, now: datetime):
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
    return supervisor, session


def test_retention_candidate_evaluation_finds_old_records_and_files(settings, tmp_path) -> None:
    database = Database(tmp_path / "archive.sqlite")
    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_archive.journal_events_retention_days + 10)
    supervisor, session = _auth_context(settings, database, old)
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.SESSION_CLOSED,
        mode="broker_sandbox",
        reason="old session closed",
        auth_context=None,
        now=old,
    )
    database.save_operator_actions([action])

    reports_dir = tmp_path / "reports" / "broker"
    reports_dir.mkdir(parents=True)
    report_file = reports_dir / "old_report.md"
    report_file.write_text("# old report\n", encoding="utf-8")
    old_mtime = (datetime.now(timezone.utc) - timedelta(days=settings.retention_archive.reports_exports_retention_days + 5)).timestamp()
    os.utime(report_file, (old_mtime, old_mtime))

    evaluation = collect_retention_candidates(database, settings, project_root=tmp_path)

    assert evaluation.total_database_candidates >= 1
    assert evaluation.total_file_candidates == 1
    assert any(candidate.record_id == action.action_id for candidate in evaluation.candidates)
    assert any(candidate.relative_path == "reports/broker/old_report.md" for candidate in evaluation.candidates)


def test_archive_package_generation_manifest_and_verification(settings, tmp_path) -> None:
    database = Database(tmp_path / "archive.sqlite")
    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_archive.journal_events_retention_days + 20)
    supervisor, session = _auth_context(settings, database, old)
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.HANDOVER_CREATED,
        mode="operator_review",
        reason="old handover created",
        auth_context=None,
        now=old,
    )
    database.save_operator_actions([action])

    manifest, archive_path = create_archive_package(
        database,
        settings,
        output_dir=tmp_path / "archives",
        label="unit-test",
        include_files=False,
        project_root=tmp_path,
    )
    verification = verify_archive_package(archive_path)
    inspected = inspect_archive_manifest(archive_path)

    assert archive_path.exists()
    assert manifest.records
    assert inspected is not None
    assert inspected.archive_id == manifest.archive_id
    assert verification.status == ArchiveVerificationStatus.PASSED
    assert verification.checked_records == len(manifest.records)


def test_archive_verification_fails_for_corrupt_package(settings, tmp_path) -> None:
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_text("not a zip archive", encoding="utf-8")

    verification = verify_archive_package(corrupt)

    assert verification.status == ArchiveVerificationStatus.FAILED
    assert verification.issues


def test_restore_archive_for_review_is_non_destructive(settings, tmp_path) -> None:
    database = Database(tmp_path / "archive.sqlite")
    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_archive.journal_events_retention_days + 20)
    supervisor, session = _auth_context(settings, database, old)
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.SESSION_OPENED,
        mode="broker_sandbox",
        reason="old session opened",
        auth_context=None,
        now=old,
    )
    database.save_operator_actions([action])
    manifest, archive_path = create_archive_package(
        database,
        settings,
        output_dir=tmp_path / "archives",
        include_files=False,
        project_root=tmp_path,
    )

    result = restore_archive_for_review(archive_path, tmp_path / "restore_review")

    assert result.status == "restored_for_review"
    assert result.verification_status == ArchiveVerificationStatus.PASSED
    assert (tmp_path / "restore_review" / manifest.archive_id / "manifest.json").exists()
    assert database.load_operator_actions()


def test_rotation_plan_blocks_destructive_rotation_by_default(settings, tmp_path) -> None:
    database = Database(tmp_path / "archive.sqlite")
    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_archive.journal_events_retention_days + 20)
    supervisor, session = _auth_context(settings, database, old)
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.SESSION_CLOSED,
        mode="broker_sandbox",
        reason="old session closed",
        auth_context=None,
        now=old,
    )
    database.save_operator_actions([action])
    evaluation = collect_retention_candidates(database, settings, include_files=False, project_root=tmp_path)

    plan = build_rotation_plan(evaluation, settings.retention_archive)

    assert plan.dry_run
    assert not plan.database_purge_allowed
    assert plan.database_records_to_archive >= 1
    assert any("database record purge is disabled" in item for item in plan.blocked_actions)


def test_archive_report_generation(settings, tmp_path) -> None:
    database = Database(tmp_path / "archive.sqlite")
    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_archive.journal_events_retention_days + 20)
    supervisor, session = _auth_context(settings, database, old)
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.SESSION_CLOSED,
        mode="broker_sandbox",
        reason="old session closed",
        auth_context=None,
        now=old,
    )
    database.save_operator_actions([action])
    evaluation = collect_retention_candidates(database, settings, include_files=False, project_root=tmp_path)
    manifest, archive_path = create_archive_package(
        database,
        settings,
        output_dir=tmp_path / "archives",
        include_files=False,
        project_root=tmp_path,
    )
    verification = verify_archive_package(archive_path)
    plan = build_rotation_plan(evaluation, settings.retention_archive)

    outputs = generate_archive_report(
        settings,
        evaluation,
        [manifest],
        tmp_path / "reports",
        rotation_plan=plan,
        verification=verification,
    )

    assert outputs["summary_markdown"].exists()
    assert outputs["pending_candidates_csv"].exists()
    assert outputs["archive_inventory_csv"].exists()
    assert outputs["rotation_plan_markdown"].exists()
