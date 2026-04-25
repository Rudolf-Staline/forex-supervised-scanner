"""Tests for tamper-evident audit chaining, verification, and export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.audit.integrity import (
    AuditIntegrityIssue,
    AuditIntegrityIssueType,
    AuditProtectedRecordType,
    AuditSealTrigger,
    AuditVerificationRun,
    AuditVerificationStatus,
)
from app.execution.operator_identity import (
    ApprovalAction,
    AuthenticatedOperatorContext,
    authenticate_operator,
    build_approval_signature,
    resolve_identity,
)
from app.execution.operator_workflows import (
    ChecklistStatus,
    OperatorActionType,
    OperatorWorkflowContext,
    acknowledge_checklist,
    authorize_live,
    evaluate_pre_session_checklist,
    latest_campaign_with_readiness,
    record_operator_action,
)
from app.execution.operations import OperatorControlState, build_broker_health_snapshot
from app.execution.models import BrokerAccountState
from app.execution.soak import SoakCampaignReadiness, SoakCampaignStatus, create_soak_campaign
from app.reporting.audit import export_audit_evidence_package
from app.storage.database import Database


def _supervisor_context(settings, database: Database, now: datetime) -> AuthenticatedOperatorContext:
    identities = database.sync_operator_identities(settings)
    supervisor = resolve_identity(identities, "supervisor")
    assert supervisor is not None
    auth_session = authenticate_operator(
        supervisor,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now,
    )
    database.save_operator_auth_session(auth_session)
    return AuthenticatedOperatorContext(identity=supervisor, auth_session=auth_session)


def _campaign():
    campaign = create_soak_campaign("weekly", "broker_live", "mt5", 168.0 * 3600.0)
    return campaign.model_copy(update={"status": SoakCampaignStatus.FINALIZED, "readiness": SoakCampaignReadiness.SUPERVISED_READY})


def _healthy_context(settings, *, now: datetime, verification: AuditVerificationRun | None = None) -> OperatorWorkflowContext:
    account = BrokerAccountState(
        broker=settings.broker.provider,
        mode=settings.execution.mode,
        connected=True,
        can_trade=True,
        balance=100_000.0,
        equity=100_000.0,
        free_margin=90_000.0,
        retrieved_at=now,
    )
    snapshot = build_broker_health_snapshot(account, [], [], settings, now=now, last_reconciliation_at=now)
    return OperatorWorkflowContext(
        snapshot=snapshot,
        account_state=account,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now, live_submissions_enabled=True),
        broker_orders=[],
        latest_campaign=_campaign(),
        latest_audit_verification=verification,
    )


def test_audit_integrity_chain_and_seal_creation(settings, tmp_path) -> None:
    database = Database(tmp_path / "audit.sqlite")
    now = datetime.now(timezone.utc)
    auth_context = _supervisor_context(settings, database, now)

    action = record_operator_action(
        operator=auth_context.identity.display_name,
        action_type=OperatorActionType.LIVE_AUTHORIZATION_GRANTED,
        mode="broker_live",
        reason="manual review complete",
        auth_context=auth_context,
        now=now,
    )
    approval = build_approval_signature(
        auth_context,
        ApprovalAction.PRE_LIVE_AUTHORIZATION,
        target_type="live_authorization",
        target_id="auth-1",
        reason="manual review complete",
        comment="manual review complete",
        now=now + timedelta(seconds=1),
    )

    database.save_operator_actions([action])
    database.save_approval_signature(approval)

    records = database.load_audit_integrity_records()
    assert len(records) >= 3  # auth session + action + approval
    assert records[-2].previous_integrity_id == records[-3].integrity_id
    assert records[-1].previous_record_hash == records[-2].record_hash

    seal = database.create_audit_seal(
        trigger_type=AuditSealTrigger.MANUAL,
        trigger_id="test-manual-seal",
        notes="test seal",
        now=now + timedelta(seconds=2),
    )

    assert seal is not None
    assert seal.record_count >= 3
    assert database.load_audit_seals(trigger_type=AuditSealTrigger.MANUAL, trigger_id="test-manual-seal")


def test_audit_verification_detects_modified_source_record(settings, tmp_path) -> None:
    database = Database(tmp_path / "audit.sqlite")
    now = datetime.now(timezone.utc)
    auth_context = _supervisor_context(settings, database, now)

    action = record_operator_action(
        operator=auth_context.identity.display_name,
        action_type=OperatorActionType.SESSION_OPENED,
        mode="broker_sandbox",
        reason="session opened",
        auth_context=auth_context,
        now=now,
    )
    database.save_operator_actions([action])

    verification = database.verify_audit_integrity(save_result=False)
    assert verification.status == AuditVerificationStatus.PASSED

    tampered = action.model_copy(update={"reason": "tampered reason"})
    with database._connect() as connection:
        connection.execute(
            "UPDATE operator_actions SET payload_json = ?, reason = ? WHERE id = ?",
            (tampered.model_dump_json(), tampered.reason, action.action_id),
        )

    failed = database.verify_audit_integrity(save_result=False)
    assert failed.status == AuditVerificationStatus.FAILED
    assert failed.altered_source_records >= 1
    assert any(issue.issue_type == AuditIntegrityIssueType.ALTERED_SOURCE_RECORD for issue in failed.issues)


def test_audit_verification_detects_missing_source_record(settings, tmp_path) -> None:
    database = Database(tmp_path / "audit.sqlite")
    now = datetime.now(timezone.utc)
    auth_context = _supervisor_context(settings, database, now)

    action = record_operator_action(
        operator=auth_context.identity.display_name,
        action_type=OperatorActionType.SESSION_CLOSED,
        mode="broker_sandbox",
        reason="session closed",
        auth_context=auth_context,
        now=now,
    )
    database.save_operator_actions([action])

    with database._connect() as connection:
        connection.execute("DELETE FROM operator_actions WHERE id = ?", (action.action_id,))

    failed = database.verify_audit_integrity(save_result=False)
    assert failed.status == AuditVerificationStatus.FAILED
    assert failed.missing_source_records >= 1
    assert any(issue.issue_type == AuditIntegrityIssueType.MISSING_SOURCE_RECORD for issue in failed.issues)


def test_audit_export_package_generation(settings, tmp_path) -> None:
    database = Database(tmp_path / "audit.sqlite")
    now = datetime.now(timezone.utc)
    auth_context = _supervisor_context(settings, database, now)

    action = record_operator_action(
        operator=auth_context.identity.display_name,
        action_type=OperatorActionType.HANDOVER_CREATED,
        mode="operator_review",
        reason="handover created",
        auth_context=auth_context,
        now=now,
    )
    database.save_operator_actions([action])
    verification = database.verify_audit_integrity(save_result=False)
    seal = database.create_audit_seal(
        trigger_type=AuditSealTrigger.MANUAL,
        trigger_id="export-seal",
        notes="export test",
        now=now + timedelta(seconds=1),
    )

    export_package, outputs = export_audit_evidence_package(
        database.load_audit_integrity_records(),
        [seal] if seal is not None else [],
        verification,
        tmp_path / "audit_export",
    )
    database.save_audit_export_package(export_package)

    assert outputs["manifest"].exists()
    assert outputs["verification_json"].exists()
    assert outputs["integrity_records_csv"].exists()
    assert database.load_audit_export_packages()


def test_live_authorization_can_block_on_failed_audit_verification(settings, tmp_path) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_live"
    adjusted.execution_capabilities.broker_live_enabled = True
    adjusted.broker.live_enabled = True
    adjusted.broker.provider = "mt5"
    adjusted.audit_integrity.block_sensitive_actions_on_verification_failure = True

    database = Database(tmp_path / "audit.sqlite")
    now = datetime.now(timezone.utc)
    verification = AuditVerificationRun(
        verification_id="verification-1",
        verified_at=now,
        strict=True,
        status=AuditVerificationStatus.FAILED,
        issues=[
            AuditIntegrityIssue(
                issue_id="issue-1",
                issue_type=AuditIntegrityIssueType.ALTERED_SOURCE_RECORD,
                severity="critical",
                message="operator action payload differs from chained snapshot",
                record_type=AuditProtectedRecordType.OPERATOR_ACTION,
                source_record_id="action-1",
            )
        ],
    )
    context = _healthy_context(adjusted, now=now, verification=verification)
    checklist = evaluate_pre_session_checklist(adjusted, "Supervisor", context, now=now)
    checklist, _ = acknowledge_checklist(checklist, "Supervisor", now=now)

    authorization, _ = authorize_live(adjusted, "Supervisor", checklist, context, acknowledged=True, now=now)

    assert authorization.status.value == "denied"
    assert any("audit integrity verification" in reason for reason in authorization.reasons)
