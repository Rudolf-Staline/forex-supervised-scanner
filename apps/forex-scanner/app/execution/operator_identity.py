"""Lightweight local operator identity, authentication, and approval helpers."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OperatorRole(str, Enum):
    """Supported lightweight operator roles."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


class OperatorIdentityStatus(str, Enum):
    """Lifecycle state for a configured local operator identity."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class OperatorAuthSessionStatus(str, Enum):
    """Lifecycle state for a local authenticated operator session."""

    ACTIVE = "active"
    EXPIRED = "expired"
    SIGNED_OUT = "signed_out"


class ApprovalAction(str, Enum):
    """Sensitive approval and override actions that require attribution."""

    PRE_LIVE_AUTHORIZATION = "pre_live_authorization"
    RESUME_AFTER_MAJOR_INCIDENT = "resume_after_major_incident"
    ACCEPT_SEVERE_HANDOVER = "accept_severe_handover"
    ENABLE_SENSITIVE_EXECUTION = "enable_sensitive_execution"
    CLEAR_SEVERE_BLOCKER = "clear_severe_blocker"
    DISABLE_KILL_SWITCH = "disable_kill_switch"


class ApprovalSignatureStatus(str, Enum):
    """Lifecycle state for a sensitive approval signature."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class PermissionAction(str, Enum):
    """Operational actions gated by lightweight roles."""

    VIEW_STATUS = "view_status"
    RUN_CHECKLIST = "run_checklist"
    OPEN_SESSION = "open_session"
    CLOSE_SESSION = "close_session"
    CREATE_HANDOVER = "create_handover"
    ACCEPT_HANDOVER = "accept_handover"
    AUTHORIZE_LIVE = "authorize_live"
    RECORD_MANUAL_INTERVENTION = "record_manual_intervention"
    RESUME_AFTER_INCIDENT = "resume_after_incident"
    TOGGLE_OPERATOR_CONTROLS = "toggle_operator_controls"
    RUN_BROKER_RECOVERY = "run_broker_recovery"
    SUBMIT_BROKER_SANDBOX = "submit_broker_sandbox"
    SUBMIT_BROKER_LIVE = "submit_broker_live"


ROLE_RANK: dict[OperatorRole, int] = {
    OperatorRole.VIEWER: 0,
    OperatorRole.OPERATOR: 1,
    OperatorRole.SUPERVISOR: 2,
    OperatorRole.ADMIN: 3,
}


DEFAULT_PERMISSION_MINIMUMS: dict[PermissionAction, OperatorRole] = {
    PermissionAction.VIEW_STATUS: OperatorRole.VIEWER,
    PermissionAction.RUN_CHECKLIST: OperatorRole.OPERATOR,
    PermissionAction.OPEN_SESSION: OperatorRole.OPERATOR,
    PermissionAction.CLOSE_SESSION: OperatorRole.OPERATOR,
    PermissionAction.CREATE_HANDOVER: OperatorRole.OPERATOR,
    PermissionAction.ACCEPT_HANDOVER: OperatorRole.OPERATOR,
    PermissionAction.AUTHORIZE_LIVE: OperatorRole.SUPERVISOR,
    PermissionAction.RECORD_MANUAL_INTERVENTION: OperatorRole.OPERATOR,
    PermissionAction.RESUME_AFTER_INCIDENT: OperatorRole.SUPERVISOR,
    PermissionAction.TOGGLE_OPERATOR_CONTROLS: OperatorRole.SUPERVISOR,
    PermissionAction.RUN_BROKER_RECOVERY: OperatorRole.SUPERVISOR,
    PermissionAction.SUBMIT_BROKER_SANDBOX: OperatorRole.OPERATOR,
    PermissionAction.SUBMIT_BROKER_LIVE: OperatorRole.SUPERVISOR,
}


APPROVAL_ACTION_MINIMUMS: dict[ApprovalAction, OperatorRole] = {
    ApprovalAction.PRE_LIVE_AUTHORIZATION: OperatorRole.SUPERVISOR,
    ApprovalAction.RESUME_AFTER_MAJOR_INCIDENT: OperatorRole.SUPERVISOR,
    ApprovalAction.ACCEPT_SEVERE_HANDOVER: OperatorRole.SUPERVISOR,
    ApprovalAction.ENABLE_SENSITIVE_EXECUTION: OperatorRole.SUPERVISOR,
    ApprovalAction.CLEAR_SEVERE_BLOCKER: OperatorRole.SUPERVISOR,
    ApprovalAction.DISABLE_KILL_SWITCH: OperatorRole.ADMIN,
}


class OperatorIdentity(BaseModel):
    """Structured local operator identity persisted for audit and review."""

    operator_id: str
    display_name: str
    role: OperatorRole
    status: OperatorIdentityStatus = OperatorIdentityStatus.ACTIVE
    team: str | None = None
    shift: str | None = None
    secret_sha256: str
    created_at: datetime
    updated_at: datetime


class OperatorAuthSession(BaseModel):
    """Authenticated local operator session used for sensitive actions."""

    auth_session_id: str
    operator_id: str
    display_name: str
    role: OperatorRole
    status: OperatorAuthSessionStatus = OperatorAuthSessionStatus.ACTIVE
    auth_method: str = "local_passphrase"
    authenticated_at: datetime
    last_verified_at: datetime
    expires_at: datetime
    signed_out_at: datetime | None = None
    team: str | None = None
    shift: str | None = None


class ApprovalSignature(BaseModel):
    """Signed approval record for a sensitive supervised action."""

    approval_id: str
    created_at: datetime
    operator_id: str
    operator_display_name: str
    role: OperatorRole
    auth_session_id: str
    action: ApprovalAction
    status: ApprovalSignatureStatus = ApprovalSignatureStatus.ACTIVE
    target_type: str
    target_id: str | None = None
    requires_reauth: bool = False
    expires_at: datetime | None = None
    reason: str | None = None
    comment: str | None = None
    linked_checklist_id: str | None = None
    linked_authorization_id: str | None = None
    linked_session_id: str | None = None
    linked_handover_id: str | None = None
    linked_incident_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AuthenticatedOperatorContext(BaseModel):
    """Resolved authenticated identity plus active auth session."""

    identity: OperatorIdentity
    auth_session: OperatorAuthSession


class PermissionDecision(BaseModel):
    """Result of a role/identity permission evaluation."""

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    requires_reauth: bool = False
    minimum_role: OperatorRole | None = None


def hash_operator_secret(secret: str) -> str:
    """Return the SHA-256 hex digest used for lightweight local auth."""

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_secret(secret_sha256: str, supplied_secret: str) -> bool:
    """Compare a supplied secret against the configured local hash."""

    return hmac.compare_digest(secret_sha256, hash_operator_secret(supplied_secret))


def effective_auth_session_status(
    session: OperatorAuthSession,
    *,
    now: datetime | None = None,
) -> OperatorAuthSessionStatus:
    """Return the effective status for a stored auth session."""

    if session.status == OperatorAuthSessionStatus.SIGNED_OUT or session.signed_out_at is not None:
        return OperatorAuthSessionStatus.SIGNED_OUT
    timestamp = now or datetime.now(timezone.utc)
    if session.expires_at <= timestamp:
        return OperatorAuthSessionStatus.EXPIRED
    return OperatorAuthSessionStatus.ACTIVE


def effective_approval_status(
    approval: ApprovalSignature,
    *,
    now: datetime | None = None,
) -> ApprovalSignatureStatus:
    """Return the effective status for a stored approval signature."""

    if approval.status == ApprovalSignatureStatus.REVOKED:
        return approval.status
    if approval.expires_at is None:
        return approval.status
    timestamp = now or datetime.now(timezone.utc)
    if approval.expires_at <= timestamp:
        return ApprovalSignatureStatus.EXPIRED
    return approval.status


def authenticate_operator(
    identity: OperatorIdentity,
    supplied_secret: str,
    *,
    session_expiry_minutes: float,
    now: datetime | None = None,
) -> OperatorAuthSession:
    """Authenticate one operator identity and return a fresh auth session."""

    if identity.status != OperatorIdentityStatus.ACTIVE:
        raise ValueError(f"operator identity {identity.operator_id} is inactive")
    if not verify_secret(identity.secret_sha256, supplied_secret):
        raise ValueError("operator authentication secret did not match")
    timestamp = now or datetime.now(timezone.utc)
    return OperatorAuthSession(
        auth_session_id=str(uuid.uuid4()),
        operator_id=identity.operator_id,
        display_name=identity.display_name,
        role=identity.role,
        authenticated_at=timestamp,
        last_verified_at=timestamp,
        expires_at=timestamp + timedelta(minutes=session_expiry_minutes),
        team=identity.team,
        shift=identity.shift,
    )


def sign_out_operator_session(
    session: OperatorAuthSession,
    *,
    now: datetime | None = None,
) -> OperatorAuthSession:
    """Mark an auth session as explicitly signed out."""

    timestamp = now or datetime.now(timezone.utc)
    return session.model_copy(
        update={
            "status": OperatorAuthSessionStatus.SIGNED_OUT,
            "signed_out_at": timestamp,
        }
    )


def reauthenticate_operator_session(
    identity: OperatorIdentity,
    session: OperatorAuthSession,
    supplied_secret: str,
    *,
    session_expiry_minutes: float,
    now: datetime | None = None,
) -> OperatorAuthSession:
    """Refresh verification time after an explicit re-auth step."""

    if not verify_secret(identity.secret_sha256, supplied_secret):
        raise ValueError("operator re-authentication secret did not match")
    timestamp = now or datetime.now(timezone.utc)
    return session.model_copy(
        update={
            "last_verified_at": timestamp,
            "expires_at": timestamp + timedelta(minutes=session_expiry_minutes),
            "status": OperatorAuthSessionStatus.ACTIVE,
        }
    )


def resolve_identity(identities: list[OperatorIdentity], operator_id: str) -> OperatorIdentity | None:
    """Return one configured operator identity by id."""

    for identity in identities:
        if identity.operator_id == operator_id:
            return identity
    return None


def resolve_auth_session(
    auth_sessions: list[OperatorAuthSession],
    *,
    operator_id: str | None = None,
    auth_session_id: str | None = None,
    now: datetime | None = None,
) -> OperatorAuthSession | None:
    """Resolve the latest active auth session by id or operator."""

    timestamp = now or datetime.now(timezone.utc)
    if auth_session_id:
        for session in auth_sessions:
            if session.auth_session_id == auth_session_id:
                return session.model_copy(update={"status": effective_auth_session_status(session, now=timestamp)})
        return None
    candidates = [session for session in auth_sessions if operator_id is None or session.operator_id == operator_id]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.authenticated_at)
    return latest.model_copy(update={"status": effective_auth_session_status(latest, now=timestamp)})


def permission_allowed(role: OperatorRole, action: PermissionAction) -> bool:
    """Return whether the given role can perform the requested action."""

    minimum = DEFAULT_PERMISSION_MINIMUMS[action]
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


def approval_role_allowed(role: OperatorRole, action: ApprovalAction) -> bool:
    """Return whether the given role can sign the requested approval."""

    minimum = APPROVAL_ACTION_MINIMUMS[action]
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


def auth_reauth_required(
    session: OperatorAuthSession,
    *,
    action: ApprovalAction,
    reauth_actions: set[ApprovalAction],
    reauth_window_minutes: float,
    now: datetime | None = None,
) -> bool:
    """Return whether this approval action needs an explicit re-auth step."""

    if action not in reauth_actions:
        return False
    timestamp = now or datetime.now(timezone.utc)
    return session.last_verified_at + timedelta(minutes=reauth_window_minutes) < timestamp


def require_authenticated_context(
    identities: list[OperatorIdentity],
    auth_sessions: list[OperatorAuthSession],
    *,
    operator_id: str,
    action: PermissionAction,
    auth_session_id: str | None = None,
    now: datetime | None = None,
) -> tuple[AuthenticatedOperatorContext | None, PermissionDecision]:
    """Resolve and validate authenticated context for a privileged action."""

    timestamp = now or datetime.now(timezone.utc)
    identity = resolve_identity(identities, operator_id)
    if identity is None:
        return None, PermissionDecision(allowed=False, reasons=[f"unknown operator identity {operator_id}"])
    if identity.status != OperatorIdentityStatus.ACTIVE:
        return None, PermissionDecision(allowed=False, reasons=[f"operator identity {operator_id} is inactive"])
    session = resolve_auth_session(auth_sessions, operator_id=operator_id, auth_session_id=auth_session_id, now=timestamp)
    if session is None:
        return None, PermissionDecision(allowed=False, reasons=[f"no authenticated operator session exists for {operator_id}"])
    effective = effective_auth_session_status(session, now=timestamp)
    if effective != OperatorAuthSessionStatus.ACTIVE:
        return None, PermissionDecision(allowed=False, reasons=[f"authenticated operator session is {effective.value}"])
    if session.operator_id != identity.operator_id:
        return None, PermissionDecision(allowed=False, reasons=["authenticated operator session does not match requested operator identity"])
    minimum_role = DEFAULT_PERMISSION_MINIMUMS[action]
    if not permission_allowed(identity.role, action):
        return None, PermissionDecision(
            allowed=False,
            reasons=[f"role {identity.role.value} cannot perform {action.value}; minimum role is {minimum_role.value}"],
            minimum_role=minimum_role,
        )
    context = AuthenticatedOperatorContext(identity=identity, auth_session=session)
    return context, PermissionDecision(allowed=True, minimum_role=minimum_role)


def build_approval_signature(
    context: AuthenticatedOperatorContext,
    action: ApprovalAction,
    *,
    target_type: str,
    target_id: str | None = None,
    expires_at: datetime | None = None,
    reason: str | None = None,
    comment: str | None = None,
    requires_reauth: bool = False,
    linked_checklist_id: str | None = None,
    linked_authorization_id: str | None = None,
    linked_session_id: str | None = None,
    linked_handover_id: str | None = None,
    linked_incident_id: str | None = None,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> ApprovalSignature:
    """Create a signed approval record tied to an authenticated identity."""

    timestamp = now or datetime.now(timezone.utc)
    return ApprovalSignature(
        approval_id=str(uuid.uuid4()),
        created_at=timestamp,
        operator_id=context.identity.operator_id,
        operator_display_name=context.identity.display_name,
        role=context.identity.role,
        auth_session_id=context.auth_session.auth_session_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        requires_reauth=requires_reauth,
        expires_at=expires_at,
        reason=reason,
        comment=comment,
        linked_checklist_id=linked_checklist_id,
        linked_authorization_id=linked_authorization_id,
        linked_session_id=linked_session_id,
        linked_handover_id=linked_handover_id,
        linked_incident_id=linked_incident_id,
        payload=payload or {},
    )
