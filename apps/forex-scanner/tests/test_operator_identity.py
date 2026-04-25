"""Tests for lightweight operator identity, auth, and approval signatures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.execution.operator_identity import (
    ApprovalAction,
    ApprovalSignatureStatus,
    AuthenticatedOperatorContext,
    OperatorAuthSessionStatus,
    PermissionAction,
    auth_reauth_required,
    authenticate_operator,
    build_approval_signature,
    effective_approval_status,
    reauthenticate_operator_session,
    require_authenticated_context,
    resolve_identity,
    sign_out_operator_session,
)
from app.execution.operator_workflows import OperatorActionType, record_operator_action
from app.reporting.operator import generate_operator_workflow_report
from app.storage.database import Database


def test_operator_identity_sync_and_auth_session_lifecycle(settings, tmp_path) -> None:
    database = Database(tmp_path / "operator_identity.sqlite")
    identities = database.sync_operator_identities(settings)
    supervisor = resolve_identity(identities, "supervisor")

    assert supervisor is not None
    assert supervisor.role.value == "supervisor"

    now = datetime.now(timezone.utc)
    auth_session = authenticate_operator(
        supervisor,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now,
    )
    database.save_operator_auth_session(auth_session)
    loaded = database.load_latest_operator_auth_session("supervisor")

    assert loaded is not None
    assert loaded.operator_id == "supervisor"
    assert loaded.status == OperatorAuthSessionStatus.ACTIVE

    signed_out = sign_out_operator_session(loaded, now=now + timedelta(minutes=5))
    database.save_operator_auth_session(signed_out)
    latest = database.load_latest_operator_auth_session("supervisor")

    assert latest is not None
    assert latest.status == OperatorAuthSessionStatus.SIGNED_OUT
    assert latest.signed_out_at is not None


def test_authenticated_context_is_refused_without_active_session(settings, tmp_path) -> None:
    database = Database(tmp_path / "operator_identity.sqlite")
    database.sync_operator_identities(settings)

    auth_context, decision = require_authenticated_context(
        database.load_operator_identities(),
        [],
        operator_id="supervisor",
        action=PermissionAction.AUTHORIZE_LIVE,
    )

    assert auth_context is None
    assert not decision.allowed
    assert any("no authenticated operator session" in reason for reason in decision.reasons)


def test_reauth_requirement_and_refresh(settings, tmp_path) -> None:
    database = Database(tmp_path / "operator_identity.sqlite")
    identities = database.sync_operator_identities(settings)
    supervisor = resolve_identity(identities, "supervisor")
    assert supervisor is not None

    now = datetime.now(timezone.utc)
    auth_session = authenticate_operator(
        supervisor,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now - timedelta(minutes=30),
    )
    stale_session = auth_session.model_copy(update={"last_verified_at": now - timedelta(minutes=30)})

    reauth_actions = {ApprovalAction(value) for value in settings.operator_auth.reauth_required_actions}
    assert auth_reauth_required(
        stale_session,
        action=ApprovalAction.PRE_LIVE_AUTHORIZATION,
        reauth_actions=reauth_actions,
        reauth_window_minutes=settings.operator_auth.reauth_window_minutes,
        now=now,
    )

    refreshed = reauthenticate_operator_session(
        supervisor,
        stale_session,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now,
    )

    assert refreshed.last_verified_at == now
    assert refreshed.expires_at > now


def test_approval_signature_persistence_and_expiry(settings, tmp_path) -> None:
    database = Database(tmp_path / "operator_identity.sqlite")
    identities = database.sync_operator_identities(settings)
    supervisor = resolve_identity(identities, "supervisor")
    assert supervisor is not None

    now = datetime.now(timezone.utc)
    auth_session = authenticate_operator(
        supervisor,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now,
    )
    auth_context = AuthenticatedOperatorContext(identity=supervisor, auth_session=auth_session)
    approval = build_approval_signature(
        auth_context,
        ApprovalAction.PRE_LIVE_AUTHORIZATION,
        target_type="live_authorization",
        target_id="auth-1",
        expires_at=now + timedelta(minutes=5),
        comment="manual review complete",
        reason="manual review complete",
        linked_authorization_id="auth-1",
    )
    database.save_operator_auth_session(auth_session)
    database.save_approval_signature(approval)

    loaded = database.load_approval_signatures()
    assert loaded
    assert loaded[0].action == ApprovalAction.PRE_LIVE_AUTHORIZATION
    assert effective_approval_status(loaded[0], now=now) == ApprovalSignatureStatus.ACTIVE

    expired = approval.model_copy(update={"expires_at": now - timedelta(minutes=1)})
    assert effective_approval_status(expired, now=now) == ApprovalSignatureStatus.EXPIRED


def test_identity_aware_operator_action_persists_auth_fields(settings, tmp_path) -> None:
    database = Database(tmp_path / "operator_identity.sqlite")
    identities = database.sync_operator_identities(settings)
    supervisor = resolve_identity(identities, "supervisor")
    assert supervisor is not None

    now = datetime.now(timezone.utc)
    auth_session = authenticate_operator(
        supervisor,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now,
    )
    auth_context = AuthenticatedOperatorContext(identity=supervisor, auth_session=auth_session)
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.LIVE_AUTHORIZATION_GRANTED,
        mode="broker_live",
        target_type="live_authorization",
        target_id="auth-1",
        reason="signed by authenticated supervisor",
        auth_context=auth_context,
        approval_signature_id="approval-1",
        now=now,
    )
    database.save_operator_auth_session(auth_session)
    database.save_operator_actions([action])

    stored = database.load_operator_actions()
    assert stored
    assert stored[0].operator_id == "supervisor"
    assert stored[0].operator_role is not None
    assert stored[0].auth_session_id == auth_session.auth_session_id
    assert stored[0].approval_signature_id == "approval-1"


def test_identity_reports_include_auth_and_approval_outputs(settings, tmp_path) -> None:
    database = Database(tmp_path / "operator_identity.sqlite")
    identities = database.sync_operator_identities(settings)
    supervisor = resolve_identity(identities, "supervisor")
    assert supervisor is not None

    now = datetime.now(timezone.utc)
    auth_session = authenticate_operator(
        supervisor,
        "supervisor-pass",
        session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
        now=now,
    )
    auth_context = AuthenticatedOperatorContext(identity=supervisor, auth_session=auth_session)
    approval = build_approval_signature(
        auth_context,
        ApprovalAction.PRE_LIVE_AUTHORIZATION,
        target_type="live_authorization",
        target_id="auth-1",
        expires_at=now + timedelta(minutes=5),
        comment="manual review complete",
        reason="manual review complete",
    )
    action = record_operator_action(
        operator=supervisor.display_name,
        action_type=OperatorActionType.LIVE_AUTHORIZATION_GRANTED,
        mode="broker_live",
        target_type="live_authorization",
        target_id="auth-1",
        reason="granted after manual review",
        auth_context=auth_context,
        approval_signature_id=approval.approval_id,
        now=now,
    )

    outputs = generate_operator_workflow_report(
        checklists=[],
        authorizations=[],
        sessions=[],
        actions=[action],
        blockers=[],
        output_dir=tmp_path / "operator_report",
        identities=identities,
        auth_sessions=[auth_session],
        approval_signatures=[approval],
    )

    assert outputs["active_auth_sessions"].exists()
    assert outputs["approval_history"].exists()
    assert outputs["expired_approvals"].exists()
    assert outputs["identity_audit_summary"].exists()
