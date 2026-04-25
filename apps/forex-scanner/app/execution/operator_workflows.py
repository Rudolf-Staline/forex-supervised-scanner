"""Operator-facing pre-session, session, and pre-live workflow helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.audit.integrity import AuditVerificationRun, AuditVerificationStatus
from app.backup.types import ContinuityMode, RecoveryValidationResult, RecoveryValidationStatus
from app.config.settings import AppSettings
from app.execution.operator_identity import ApprovalSignature, AuthenticatedOperatorContext, OperatorRole
from app.execution.models import BrokerAccountState, ExecutionOrder
from app.execution.operations import (
    AlertCategory,
    AlertSeverity,
    AlertStatus,
    BrokerHealthSnapshot,
    BrokerIncident,
    BrokerIncidentSeverity,
    BrokerIncidentStatus,
    OperationalAlert,
    OperatorControlState,
    ResumeReadinessStatus,
    assess_resume_readiness,
)
from app.execution.reconciliation import ReconciliationAnomaly
from app.execution.soak import SoakCampaign, SoakCampaignReadiness


class ChecklistItemKey(str, Enum):
    """Structured pre-session checklist item identifiers."""

    EXECUTION_MODE = "execution_mode"
    BROKER_CONNECTIVITY = "broker_connectivity"
    ACCOUNT_SYNC_FRESHNESS = "account_sync_freshness"
    POSITION_SYNC_FRESHNESS = "position_sync_freshness"
    RECONCILIATION_FRESHNESS = "reconciliation_freshness"
    UNRESOLVED_INCIDENTS = "unresolved_incidents"
    ACTIVE_SEVERE_ALERTS = "active_severe_alerts"
    DEGRADED_MODE_STATE = "degraded_mode_state"
    KILL_SWITCH_STATE = "kill_switch_state"
    DATA_QUALITY_STATUS = "data_quality_status"
    SPREAD_SANITY = "spread_sanity"
    GUARDRAIL_CONFIGURATION = "guardrail_configuration"
    MONITORING_EXPORTER_HEALTH = "monitoring_exporter_health"
    CAMPAIGN_READINESS = "campaign_readiness"


class ChecklistStatus(str, Enum):
    """Pass, warning, or fail result for checklist items and the full checklist."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class LiveAuthorizationStatus(str, Enum):
    """Status of a manual pre-live authorization record."""

    GRANTED = "granted"
    DENIED = "denied"
    EXPIRED = "expired"


class TradingSessionStatus(str, Enum):
    """Lifecycle state for an operator-reviewed trading session."""

    OPEN = "open"
    CLOSED = "closed"
    HANDOFF_REQUIRED = "handoff_required"


class HandoverStatus(str, Enum):
    """Lifecycle state for an inter-session handover package."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REFUSED = "refused"
    EXPIRED = "expired"


class OperatorActionType(str, Enum):
    """Explicit operator-review actions stored for audit and reporting."""

    AUTHENTICATION_SUCCEEDED = "authentication_succeeded"
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHENTICATION_SIGNED_OUT = "authentication_signed_out"
    REAUTH_COMPLETED = "reauth_completed"
    REAUTH_REQUIRED = "reauth_required"
    OPERATOR_CONTROL_UPDATED = "operator_control_updated"
    CHECKLIST_ACKNOWLEDGED = "checklist_acknowledged"
    SESSION_OPENED = "session_opened"
    SESSION_OPEN_BLOCKED = "session_open_blocked"
    LIVE_AUTHORIZATION_GRANTED = "live_authorization_granted"
    LIVE_AUTHORIZATION_DENIED = "live_authorization_denied"
    SESSION_CLOSED = "session_closed"
    HANDOFF_REQUIRED = "handoff_required"
    HANDOVER_CREATED = "handover_created"
    HANDOVER_ACCEPTED = "handover_accepted"
    HANDOVER_REFUSED = "handover_refused"
    MANUAL_INTERVENTION_COMPLETED = "manual_intervention_completed"
    RESUME_AFTER_INCIDENT_APPROVED = "resume_after_incident_approved"


class OperatorActionResult(str, Enum):
    """Outcome recorded for an operator action."""

    COMPLETED = "completed"
    DENIED = "denied"


class ChecklistItemResult(BaseModel):
    """One pre-session checklist item with structured evidence."""

    item_key: ChecklistItemKey
    label: str
    status: ChecklistStatus
    reason: str
    details: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class PreSessionChecklist(BaseModel):
    """Persisted operator checklist result for one session-open review."""

    checklist_id: str
    created_at: datetime
    operator: str
    mode: str
    broker: str
    status: ChecklistStatus
    items: list[ChecklistItemResult] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    acknowledged: bool = False
    acknowledged_at: datetime | None = None
    linked_campaign_id: str | None = None
    linked_campaign_readiness: SoakCampaignReadiness | None = None
    summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class LiveAuthorizationRecord(BaseModel):
    """Manual operator authorization required before broker-live submission."""

    authorization_id: str
    created_at: datetime
    operator: str
    operator_id: str | None = None
    operator_role: OperatorRole | None = None
    auth_session_id: str | None = None
    approval_signature_id: str | None = None
    secondary_operator: str | None = None
    mode: str
    broker: str
    status: LiveAuthorizationStatus
    linked_checklist_id: str | None = None
    linked_campaign_id: str | None = None
    checklist_status: ChecklistStatus | None = None
    campaign_readiness: SoakCampaignReadiness | None = None
    acknowledged: bool = False
    expires_at: datetime | None = None
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    comment: str | None = None
    summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class TradingSessionRecord(BaseModel):
    """Structured session-open/session-close record for supervised operation."""

    session_id: str
    opened_at: datetime
    operator: str
    mode: str
    broker: str
    status: TradingSessionStatus
    linked_checklist_id: str | None = None
    linked_authorization_id: str | None = None
    closed_at: datetime | None = None
    open_summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    close_summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    unresolved_items: list[str] = Field(default_factory=list)
    handoff_required: bool = False
    notes: str | None = None


class OperatorActionRecord(BaseModel):
    """Query-friendly operator action audit record."""

    action_id: str
    created_at: datetime
    operator: str
    operator_id: str | None = None
    operator_display_name: str | None = None
    operator_role: OperatorRole | None = None
    auth_session_id: str | None = None
    approval_signature_id: str | None = None
    action_type: OperatorActionType
    result: OperatorActionResult
    mode: str
    target_type: str | None = None
    target_id: str | None = None
    linked_checklist_id: str | None = None
    linked_authorization_id: str | None = None
    linked_session_id: str | None = None
    reason: str | None = None
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class HandoverIssue(BaseModel):
    """One unresolved issue carried across sessions."""

    record_id: str
    severity: str
    status: str
    label: str
    reason: str


class HandoverExposure(BaseModel):
    """One open order or position that the next operator must acknowledge."""

    kind: str
    symbol: str
    identifier: str
    status: str
    reason: str


class HandoverRecord(BaseModel):
    """Structured handover package linking two supervised sessions."""

    handover_id: str
    source_session_id: str
    target_session_id: str | None = None
    source_operator: str
    target_operator: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    accepted_at: datetime | None = None
    expires_at: datetime | None = None
    status: HandoverStatus
    summary: str
    linked_checklist_id: str | None = None
    linked_checklist_status: ChecklistStatus | None = None
    health_snapshot_created_at: datetime | None = None
    unresolved_incidents: list[HandoverIssue] = Field(default_factory=list)
    unresolved_alerts: list[HandoverIssue] = Field(default_factory=list)
    unresolved_reconciliation_anomalies: list[HandoverIssue] = Field(default_factory=list)
    open_positions_orders: list[HandoverExposure] = Field(default_factory=list)
    live_authorization_state: LiveAuthorizationStatus | None = None
    degraded_mode: bool = False
    kill_switch_active: bool = False
    pending_manual_actions: list[str] = Field(default_factory=list)
    blocked_items: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    acceptance_signature_id: str | None = None
    notes: str | None = None
    refusal_reason: str | None = None
    summary_payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class OperatorWorkflowContext(BaseModel):
    """Operational state required to evaluate operator workflows."""

    snapshot: BrokerHealthSnapshot | None = None
    account_state: BrokerAccountState | None = None
    incidents: list[BrokerIncident] = Field(default_factory=list)
    alerts: list[OperationalAlert] = Field(default_factory=list)
    anomalies: list[ReconciliationAnomaly] = Field(default_factory=list)
    controls: OperatorControlState
    broker_orders: list[ExecutionOrder] = Field(default_factory=list)
    latest_campaign: SoakCampaign | None = None
    latest_audit_verification: AuditVerificationRun | None = None
    latest_recovery_validation: RecoveryValidationResult | None = None


class SessionOpenResult(BaseModel):
    """Result of a session-open attempt."""

    checklist: PreSessionChecklist
    session: TradingSessionRecord | None = None
    actions: list[OperatorActionRecord] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class SessionCloseResult(BaseModel):
    """Result of a session-close workflow."""

    session: TradingSessionRecord
    actions: list[OperatorActionRecord] = Field(default_factory=list)


class HandoverReviewResult(BaseModel):
    """Result of creating, accepting, or refusing a handover."""

    handover: HandoverRecord | None = None
    actions: list[OperatorActionRecord] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class ContinuityCheckResult(BaseModel):
    """Carry-over continuity issues between supervised sessions."""

    checked_at: datetime
    latest_session_id: str | None = None
    latest_handover_id: str | None = None
    latest_handover_status: HandoverStatus | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    carry_over_items: list[str] = Field(default_factory=list)
    summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


def latest_campaign_with_readiness(campaigns: list[SoakCampaign]) -> SoakCampaign | None:
    """Return the newest campaign with a readiness assessment, if any."""

    ready = [campaign for campaign in campaigns if campaign.readiness is not None]
    if not ready:
        return None
    return max(ready, key=lambda campaign: campaign.started_at)


def evaluate_pre_session_checklist(
    settings: AppSettings,
    operator: str,
    context: OperatorWorkflowContext,
    *,
    now: datetime | None = None,
) -> PreSessionChecklist:
    """Build a structured pre-session checklist from current operational state."""

    timestamp = now or datetime.now(timezone.utc)
    snapshot = context.snapshot
    mode = settings.execution.mode
    broker = snapshot.broker if snapshot is not None else settings.broker.provider
    items = [
        _execution_mode_item(settings),
        _broker_connectivity_item(mode, snapshot),
        _account_sync_item(mode, snapshot, settings, timestamp),
        _position_sync_item(mode, snapshot, settings, timestamp),
        _reconciliation_item(mode, snapshot, context.anomalies, settings, timestamp),
        _incident_item(context.incidents, settings),
        _alert_item(context.alerts, settings),
        _degraded_mode_item(snapshot, context.controls),
        _kill_switch_item(snapshot),
        _data_quality_item(context.alerts),
        _spread_sanity_item(context.alerts),
        _guardrail_item(settings),
        _monitoring_item(settings),
        _campaign_readiness_item(mode, context.latest_campaign, settings),
    ]
    required_items = set(settings.operator_workflow.required_checklist_items)
    blockers = [
        f"{item.label}: {item.reason}"
        for item in items
        if item.status == ChecklistStatus.FAIL and item.item_key in required_items
    ]
    warnings = [
        f"{item.label}: {item.reason}"
        for item in items
        if item.status == ChecklistStatus.WARNING or (item.status == ChecklistStatus.FAIL and item.item_key not in required_items)
    ]
    overall = ChecklistStatus.FAIL if blockers else ChecklistStatus.WARNING if warnings else ChecklistStatus.PASS
    open_incidents = [incident for incident in context.incidents if incident.status == BrokerIncidentStatus.OPEN]
    active_alerts = [alert for alert in context.alerts if alert.status != AlertStatus.RESOLVED]
    return PreSessionChecklist(
        checklist_id=str(uuid.uuid4()),
        created_at=timestamp,
        operator=operator,
        mode=mode,
        broker=broker,
        status=overall,
        items=items,
        blockers=blockers,
        warnings=warnings,
        linked_campaign_id=context.latest_campaign.campaign_id if context.latest_campaign else None,
        linked_campaign_readiness=context.latest_campaign.readiness if context.latest_campaign else None,
        summary={
            "health_status": snapshot.health_status if snapshot else ("paper_mode" if mode == "paper" else "unknown"),
            "open_incidents": len(open_incidents),
            "active_alerts": len(active_alerts),
            "open_broker_orders": sum(1 for order in context.broker_orders if order.is_open),
            "open_positions": snapshot.open_positions if snapshot else 0,
            "pending_orders": snapshot.pending_orders if snapshot else 0,
            "campaign_readiness": context.latest_campaign.readiness.value if context.latest_campaign and context.latest_campaign.readiness else None,
        },
    )


def acknowledge_checklist(
    checklist: PreSessionChecklist,
    operator: str,
    *,
    auth_context: AuthenticatedOperatorContext | None = None,
    now: datetime | None = None,
) -> tuple[PreSessionChecklist, OperatorActionRecord]:
    """Acknowledge checklist review and create the audit record."""

    timestamp = now or datetime.now(timezone.utc)
    updated = checklist.model_copy(update={"acknowledged": True, "acknowledged_at": timestamp, "operator": operator})
    action = _action(
        operator=operator,
        action_type=OperatorActionType.CHECKLIST_ACKNOWLEDGED,
        mode=checklist.mode,
        result=OperatorActionResult.COMPLETED,
        target_type="checklist",
        target_id=checklist.checklist_id,
        linked_checklist_id=checklist.checklist_id,
        reason=f"operator acknowledged checklist with status {checklist.status.value}",
        created_at=timestamp,
        auth_context=auth_context,
        payload={
            "checklist_status": checklist.status.value,
            "blockers": len(checklist.blockers),
            "warnings": len(checklist.warnings),
        },
    )
    return updated, action


def authorization_effective_status(
    authorization: LiveAuthorizationRecord | None,
    *,
    now: datetime | None = None,
) -> LiveAuthorizationStatus | None:
    """Return the effective authorization status, including expiry."""

    if authorization is None:
        return None
    if authorization.status == LiveAuthorizationStatus.GRANTED and authorization.expires_at is not None:
        timestamp = now or datetime.now(timezone.utc)
        if authorization.expires_at <= timestamp:
            return LiveAuthorizationStatus.EXPIRED
    return authorization.status


def live_authorization_block_reasons(
    settings: AppSettings,
    checklist: PreSessionChecklist | None,
    authorization: LiveAuthorizationRecord | None,
    context: OperatorWorkflowContext,
    *,
    sessions: list[TradingSessionRecord] | None = None,
    handovers: list[HandoverRecord] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Return explicit reasons that broker-live submission should remain blocked."""

    if settings.execution.mode != "broker_live":
        return []
    timestamp = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if checklist is None:
        reasons.append("no pre-session checklist is available")
    else:
        if settings.operator_workflow.require_checklist_acknowledgement_for_live_authorization and not checklist.acknowledged:
            reasons.append("latest checklist has not been acknowledged")
        if checklist.status == ChecklistStatus.FAIL:
            reasons.extend(f"checklist blocker: {reason}" for reason in checklist.blockers)
    effective = authorization_effective_status(authorization, now=timestamp)
    if authorization is None:
        reasons.append("no live authorization has been recorded")
    elif effective == LiveAuthorizationStatus.EXPIRED:
        reasons.append("latest live authorization has expired")
    elif effective != LiveAuthorizationStatus.GRANTED:
        reasons.append("latest live authorization is not granted")
    if settings.operator_workflow.require_campaign_readiness_for_live_authorization:
        campaign = context.latest_campaign
        if campaign is None or campaign.readiness is None:
            reasons.append("no qualifying soak campaign readiness is available")
        elif _readiness_rank(campaign.readiness) < _required_readiness_rank(settings):
            reasons.append(
                f"campaign readiness {campaign.readiness.value} is below required {settings.operator_workflow.minimum_readiness_for_live_authorization}"
            )
    if settings.audit_integrity.block_sensitive_actions_on_verification_failure:
        verification = context.latest_audit_verification
        if verification is None:
            reasons.append("no audit integrity verification is available")
        elif verification.status != AuditVerificationStatus.PASSED:
            reasons.append("latest audit integrity verification did not pass")
        elif (timestamp - verification.verified_at).total_seconds() > settings.audit_integrity.verification_max_age_hours * 3600.0:
            reasons.append("latest audit integrity verification is stale")
    reasons.extend(_recovery_validation_blockers(settings, context.latest_recovery_validation))
    snapshot = context.snapshot
    if snapshot is not None:
        readiness = assess_resume_readiness(snapshot, context.incidents, context.alerts, context.anomalies, context.controls, settings)
        if readiness.status == ResumeReadinessStatus.BLOCKED_PENDING_MANUAL_REVIEW:
            reasons.extend(f"resume blocked: {reason}" for reason in readiness.reasons)
    if sessions or handovers:
        continuity = evaluate_inter_session_continuity(
            settings,
            context,
            sessions or [],
            handovers or [],
            live_authorization=authorization,
            require_for="live_authorization",
            now=timestamp,
        )
        reasons.extend(continuity.blockers)
    return _dedupe(reasons)


def authorize_live(
    settings: AppSettings,
    operator: str,
    checklist: PreSessionChecklist,
    context: OperatorWorkflowContext,
    *,
    acknowledged: bool,
    comment: str | None = None,
    secondary_operator: str | None = None,
    sessions: list[TradingSessionRecord] | None = None,
    handovers: list[HandoverRecord] | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    approval_signature: ApprovalSignature | None = None,
    now: datetime | None = None,
) -> tuple[LiveAuthorizationRecord, list[OperatorActionRecord]]:
    """Create a granted or denied manual live-authorization record."""

    timestamp = now or datetime.now(timezone.utc)
    reasons = live_authorization_prerequisites(
        settings,
        checklist,
        context,
        acknowledged=acknowledged,
        secondary_operator=secondary_operator,
        sessions=sessions,
        handovers=handovers,
        now=timestamp,
    )
    warnings = list(checklist.warnings)
    status = LiveAuthorizationStatus.DENIED if reasons else LiveAuthorizationStatus.GRANTED
    expires_at = None
    if status == LiveAuthorizationStatus.GRANTED:
        expires_at = timestamp + timedelta(minutes=settings.operator_workflow.authorization_expiry_minutes)
    record = LiveAuthorizationRecord(
        authorization_id=str(uuid.uuid4()),
        created_at=timestamp,
        operator=operator,
        operator_id=auth_context.identity.operator_id if auth_context else None,
        operator_role=auth_context.identity.role if auth_context else None,
        auth_session_id=auth_context.auth_session.auth_session_id if auth_context else None,
        approval_signature_id=approval_signature.approval_id if approval_signature else None,
        secondary_operator=secondary_operator,
        mode=settings.execution.mode,
        broker=context.snapshot.broker if context.snapshot else settings.broker.provider,
        status=status,
        linked_checklist_id=checklist.checklist_id,
        linked_campaign_id=context.latest_campaign.campaign_id if context.latest_campaign else None,
        checklist_status=checklist.status,
        campaign_readiness=context.latest_campaign.readiness if context.latest_campaign else None,
        acknowledged=acknowledged,
        expires_at=expires_at,
        reasons=reasons,
        warnings=warnings,
        comment=comment,
        summary={
            "health_status": context.snapshot.health_status if context.snapshot else "unknown",
            "active_alerts": len([alert for alert in context.alerts if alert.status != AlertStatus.RESOLVED]),
            "open_incidents": len([incident for incident in context.incidents if incident.status == BrokerIncidentStatus.OPEN]),
            "checklist_acknowledged": checklist.acknowledged,
            "dual_confirmation_required": settings.operator_workflow.dual_confirmation_required,
        },
    )
    action = _action(
        operator=operator,
        action_type=OperatorActionType.LIVE_AUTHORIZATION_GRANTED if status == LiveAuthorizationStatus.GRANTED else OperatorActionType.LIVE_AUTHORIZATION_DENIED,
        mode=settings.execution.mode,
        result=OperatorActionResult.COMPLETED if status == LiveAuthorizationStatus.GRANTED else OperatorActionResult.DENIED,
        target_type="live_authorization",
        target_id=record.authorization_id,
        linked_checklist_id=checklist.checklist_id,
        linked_authorization_id=record.authorization_id,
        reason="; ".join(reasons) if reasons else "operator granted live authorization",
        created_at=timestamp,
        auth_context=auth_context,
        approval_signature_id=approval_signature.approval_id if approval_signature else None,
        payload={
            "expires_at": expires_at.isoformat() if expires_at else None,
            "campaign_readiness": record.campaign_readiness.value if record.campaign_readiness else None,
            "warnings": len(warnings),
        },
    )
    return record, [action]


def live_authorization_prerequisites(
    settings: AppSettings,
    checklist: PreSessionChecklist,
    context: OperatorWorkflowContext,
    *,
    acknowledged: bool,
    secondary_operator: str | None = None,
    sessions: list[TradingSessionRecord] | None = None,
    handovers: list[HandoverRecord] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Return blocking prerequisites for granting broker-live authorization."""

    reasons: list[str] = []
    if settings.execution.mode != "broker_live":
        reasons.append("execution mode is not broker_live")
    if settings.broker.provider == "mock":
        reasons.append("broker_live authorization cannot use the mock broker provider")
    if not settings.execution_capabilities.broker_live_enabled or not settings.broker.live_enabled:
        reasons.append("broker_live config gates are not enabled")
    if checklist.status == ChecklistStatus.FAIL:
        reasons.extend(f"checklist blocker: {reason}" for reason in checklist.blockers)
    if settings.operator_workflow.require_checklist_acknowledgement_for_live_authorization and not checklist.acknowledged:
        reasons.append("latest checklist has not been acknowledged")
    if not acknowledged:
        reasons.append("explicit operator acknowledgement is required for live authorization")
    if settings.operator_workflow.dual_confirmation_required:
        if not secondary_operator:
            reasons.append("dual confirmation requires a secondary operator")
        elif secondary_operator == checklist.operator:
            reasons.append("secondary operator must differ from the checklist operator")
    reasons.extend(_campaign_readiness_blockers(settings, context.latest_campaign))
    if settings.audit_integrity.block_sensitive_actions_on_verification_failure:
        verification = context.latest_audit_verification
        if verification is None:
            reasons.append("no audit integrity verification is available")
        elif verification.status != AuditVerificationStatus.PASSED:
            reasons.append("latest audit integrity verification did not pass")
        elif now is not None and (now - verification.verified_at).total_seconds() > settings.audit_integrity.verification_max_age_hours * 3600.0:
            reasons.append("latest audit integrity verification is stale")
    reasons.extend(_recovery_validation_blockers(settings, context.latest_recovery_validation))
    snapshot = context.snapshot
    if snapshot is None:
        reasons.append("no broker health snapshot is available")
    else:
        readiness = assess_resume_readiness(snapshot, context.incidents, context.alerts, context.anomalies, context.controls, settings)
        if readiness.status == ResumeReadinessStatus.BLOCKED_PENDING_MANUAL_REVIEW:
            reasons.extend(f"resume blocked: {reason}" for reason in readiness.reasons)
    if sessions or handovers:
        continuity = evaluate_inter_session_continuity(
            settings,
            context,
            sessions or [],
            handovers or [],
            live_authorization=None,
            require_for="live_authorization",
            now=now,
        )
        reasons.extend(continuity.blockers)
    return _dedupe(reasons)


def _recovery_validation_blockers(
    settings: AppSettings,
    validation: RecoveryValidationResult | None,
) -> list[str]:
    if not settings.backup_recovery.block_sensitive_actions_until_recovery_validation or validation is None:
        return []
    if not validation.sensitive_actions_blocked:
        return []
    reasons = [
        f"service continuity mode is {validation.mode.value}",
        f"recovery validation status is {validation.status.value}",
    ]
    reasons.extend(f"recovery blocker: {reason}" for reason in validation.blockers)
    if validation.status == RecoveryValidationStatus.PENDING or validation.mode == ContinuityMode.POST_RESTORE_VALIDATION:
        reasons.append("post-restore validation has not completed")
    return _dedupe(reasons)


def handover_effective_status(
    settings: AppSettings,
    handover: HandoverRecord,
    *,
    now: datetime | None = None,
) -> HandoverStatus:
    """Return the effective handover status, including expiry for pending records."""

    if handover.status != HandoverStatus.PENDING or handover.expires_at is None:
        return handover.status
    timestamp = now or datetime.now(timezone.utc)
    if handover.expires_at <= timestamp:
        return HandoverStatus.EXPIRED
    return handover.status


def latest_handover_record(handovers: list[HandoverRecord]) -> HandoverRecord | None:
    """Return the newest handover record if one exists."""

    if not handovers:
        return None
    return max(handovers, key=lambda record: record.created_at)


def evaluate_inter_session_continuity(
    settings: AppSettings,
    context: OperatorWorkflowContext,
    sessions: list[TradingSessionRecord],
    handovers: list[HandoverRecord],
    *,
    live_authorization: LiveAuthorizationRecord | None = None,
    require_for: str = "session_open",
    now: datetime | None = None,
) -> ContinuityCheckResult:
    """Evaluate unresolved carry-over state between supervised sessions."""

    timestamp = now or datetime.now(timezone.utc)
    latest_session = max(sessions, key=lambda record: record.opened_at) if sessions else None
    latest_handover = latest_handover_record(handovers)
    effective_handover_status = (
        handover_effective_status(settings, latest_handover, now=timestamp) if latest_handover is not None else None
    )
    blockers: list[str] = []
    warnings: list[str] = []
    carry_over_items: list[str] = []
    open_incidents = [incident for incident in context.incidents if incident.status == BrokerIncidentStatus.OPEN]
    active_alerts = [alert for alert in context.alerts if alert.status != AlertStatus.RESOLVED]
    severe_incidents = [
        incident
        for incident in open_incidents
        if _severity_rank_value(incident.severity.value) >= _severity_rank_value(settings.operator_workflow.mandatory_acknowledgement_min_severity)
    ]
    severe_alerts = [
        alert
        for alert in active_alerts
        if _severity_rank_value(alert.severity.value) >= _severity_rank_value(settings.operator_workflow.mandatory_acknowledgement_min_severity)
    ]
    severe_anomalies = [
        anomaly
        for anomaly in context.anomalies
        if _severity_rank_value(anomaly.severity) >= _severity_rank_value(settings.operator_workflow.mandatory_acknowledgement_min_severity)
    ]
    open_orders = [order for order in context.broker_orders if order.is_open]
    carry_over_items.extend(f"incident:{incident.category.value}" for incident in open_incidents)
    carry_over_items.extend(f"alert:{alert.category.value}" for alert in active_alerts)
    carry_over_items.extend(f"anomaly:{anomaly.anomaly_type.value}" for anomaly in context.anomalies)
    carry_over_items.extend(f"open_order:{order.request.symbol}:{order.order_id}" for order in open_orders)

    if latest_session is not None and latest_session.closed_at is None:
        blockers.append("previous supervised session remains open and must be explicitly closed before a new handover or session starts")
    if latest_session is not None and latest_session.handoff_required and latest_handover is None:
        blockers.append("previous session requires handover but no handover record has been created")
    if latest_handover is not None and effective_handover_status in {HandoverStatus.PENDING, HandoverStatus.REFUSED, HandoverStatus.EXPIRED}:
        if require_for == "session_open" and settings.operator_workflow.require_handover_acceptance_before_session_open:
            blockers.append("latest handover must be accepted before opening a new supervised session")
        if require_for == "live_authorization" and settings.operator_workflow.require_handover_acceptance_before_live_authorization:
            blockers.append("latest handover must be accepted before live authorization or broker-live submission")
        if effective_handover_status == HandoverStatus.REFUSED:
            blockers.append("latest handover was refused and responsibility transfer remains unresolved")
        if effective_handover_status == HandoverStatus.EXPIRED:
            blockers.append("latest handover has expired and must be recreated or re-reviewed")
    if open_orders and effective_handover_status != HandoverStatus.ACCEPTED:
        blockers.append("open positions or broker orders have not been acknowledged through an accepted handover")
    if effective_handover_status == HandoverStatus.ACCEPTED and open_orders:
        warnings.append(f"{len(open_orders)} open position/order item(s) remain active after handover acceptance")
    if latest_handover is not None and effective_handover_status == HandoverStatus.ACCEPTED and settings.execution.mode != "paper":
        reference_time = latest_handover.accepted_at or latest_handover.created_at
        snapshot = context.snapshot
        if snapshot is None:
            blockers.append("no broker health snapshot is available after the latest accepted handover")
        else:
            if _normalize_time(snapshot.created_at) <= reference_time:
                blockers.append("broker health snapshot has not been refreshed since the latest accepted handover")
            reconciliation_at = snapshot.last_successful_reconciliation_at
            if reconciliation_at is None or _normalize_time(reconciliation_at) <= reference_time:
                blockers.append("reconciliation has not been refreshed since the latest accepted handover")
    if live_authorization is not None:
        transition_time = _latest_continuity_transition_time(latest_session, latest_handover)
        if transition_time is not None and live_authorization.created_at <= transition_time:
            message = "existing live authorization predates the latest session transition and must be re-reviewed"
            if require_for == "live_authorization":
                blockers.append(message)
            else:
                warnings.append(message)
    if severe_incidents:
        warnings.append(f"{len(severe_incidents)} severe incident(s) remain in carry-over state")
    if severe_alerts:
        warnings.append(f"{len(severe_alerts)} severe alert(s) remain in carry-over state")
    if severe_anomalies:
        warnings.append(f"{len(severe_anomalies)} severe reconciliation anomaly/anomalies remain in carry-over state")

    return ContinuityCheckResult(
        checked_at=timestamp,
        latest_session_id=latest_session.session_id if latest_session else None,
        latest_handover_id=latest_handover.handover_id if latest_handover else None,
        latest_handover_status=effective_handover_status,
        blockers=_dedupe(blockers),
        warnings=_dedupe(warnings),
        carry_over_items=_dedupe(carry_over_items),
        summary={
            "open_incidents": len(open_incidents),
            "active_alerts": len(active_alerts),
            "open_orders": len(open_orders),
            "open_positions": context.snapshot.open_positions if context.snapshot else 0,
            "pending_handovers": sum(
                1
                for handover in handovers
                if handover_effective_status(settings, handover, now=timestamp) in {HandoverStatus.PENDING, HandoverStatus.REFUSED, HandoverStatus.EXPIRED}
            ),
        },
    )


def create_handover(
    settings: AppSettings,
    source_session: TradingSessionRecord,
    context: OperatorWorkflowContext,
    *,
    checklist: PreSessionChecklist | None = None,
    live_authorization: LiveAuthorizationRecord | None = None,
    target_session_id: str | None = None,
    target_operator: str | None = None,
    summary: str | None = None,
    notes: str | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    now: datetime | None = None,
) -> tuple[HandoverRecord, list[OperatorActionRecord]]:
    """Create a structured handover package from the current operational state."""

    timestamp = now or datetime.now(timezone.utc)
    snapshot = context.snapshot
    open_incidents = [incident for incident in context.incidents if incident.status == BrokerIncidentStatus.OPEN]
    active_alerts = [alert for alert in context.alerts if alert.status != AlertStatus.RESOLVED]
    open_orders = [order for order in context.broker_orders if order.is_open]
    readiness = None
    if snapshot is not None:
        readiness = assess_resume_readiness(snapshot, context.incidents, context.alerts, context.anomalies, context.controls, settings)
    blocked_items = _dedupe(
        [
            *(f"incident:{incident.category.value}" for incident in open_incidents if _severity_rank_value(incident.severity.value) >= _severity_rank_value(settings.operator_workflow.mandatory_acknowledgement_min_severity)),
            *(f"alert:{alert.category.value}" for alert in active_alerts if _severity_rank_value(alert.severity.value) >= _severity_rank_value(settings.operator_workflow.mandatory_acknowledgement_min_severity)),
            *(f"anomaly:{anomaly.anomaly_type.value}" for anomaly in context.anomalies if _severity_rank_value(anomaly.severity) >= _severity_rank_value(settings.operator_workflow.mandatory_acknowledgement_min_severity)),
            *(f"open_order:{order.request.symbol}:{order.order_id}" for order in open_orders),
            *(f"resume:{reason}" for reason in (readiness.reasons if readiness is not None else [])),
        ]
    )
    recommended_next_steps = _dedupe(
        [
            *(readiness.required_actions if readiness is not None else []),
            "review unresolved alerts, incidents, and reconciliation anomalies before sensitive actions resume",
            "rerun broker health and reconciliation checks before any broker-live authorization",
        ]
    )
    pending_manual_actions = list(readiness.required_actions) if readiness is not None else []
    exposures = [
        HandoverExposure(
            kind="order",
            symbol=order.request.symbol,
            identifier=order.order_id,
            status=order.status.value,
            reason="open broker or paper order remains active at handover time",
        )
        for order in open_orders
    ]
    if snapshot is not None and snapshot.open_positions > 0 and not exposures:
        exposures.append(
            HandoverExposure(
                kind="position_summary",
                symbol="portfolio",
                identifier="open_positions",
                status="open",
                reason=f"{snapshot.open_positions} position(s) remain open at handover time",
            )
        )
    auto_summary = summary or (
        f"Session {source_session.session_id} handover with {len(open_incidents)} incident(s), "
        f"{len(active_alerts)} alert(s), {len(context.anomalies)} reconciliation anomaly/anomalies, "
        f"and {len(exposures)} open position/order item(s)."
    )
    expires_at = timestamp + timedelta(hours=settings.operator_workflow.handover_expiry_hours)
    handover = HandoverRecord(
        handover_id=str(uuid.uuid4()),
        source_session_id=source_session.session_id,
        target_session_id=target_session_id,
        source_operator=source_session.operator,
        target_operator=target_operator,
        created_at=timestamp,
        expires_at=expires_at,
        status=HandoverStatus.PENDING,
        summary=auto_summary,
        linked_checklist_id=checklist.checklist_id if checklist else source_session.linked_checklist_id,
        linked_checklist_status=checklist.status if checklist else None,
        health_snapshot_created_at=snapshot.created_at if snapshot else None,
        unresolved_incidents=[_handover_issue_from_incident(incident) for incident in open_incidents],
        unresolved_alerts=[_handover_issue_from_alert(alert) for alert in active_alerts],
        unresolved_reconciliation_anomalies=[_handover_issue_from_anomaly(anomaly) for anomaly in context.anomalies],
        open_positions_orders=exposures,
        live_authorization_state=authorization_effective_status(live_authorization, now=timestamp),
        degraded_mode=context.controls.degraded_mode or bool(snapshot and snapshot.degraded_flags),
        kill_switch_active=snapshot.kill_switch_active if snapshot else False,
        pending_manual_actions=pending_manual_actions,
        blocked_items=blocked_items,
        recommended_next_steps=recommended_next_steps,
        notes=notes or source_session.notes,
        summary_payload={
            "open_incidents": len(open_incidents),
            "active_alerts": len(active_alerts),
            "open_anomalies": len(context.anomalies),
            "open_positions": snapshot.open_positions if snapshot else 0,
            "pending_orders": snapshot.pending_orders if snapshot else 0,
            "blocked_items": len(blocked_items),
        },
    )
    action = _action(
        operator=source_session.operator,
        action_type=OperatorActionType.HANDOVER_CREATED,
        mode=source_session.mode,
        result=OperatorActionResult.COMPLETED,
        target_type="handover",
        target_id=handover.handover_id,
        linked_checklist_id=handover.linked_checklist_id,
        linked_authorization_id=source_session.linked_authorization_id,
        linked_session_id=source_session.session_id,
        reason="structured handover package created from supervised session state",
        created_at=timestamp,
        auth_context=auth_context,
        payload={
            "blocked_items": len(handover.blocked_items),
            "pending_manual_actions": len(handover.pending_manual_actions),
            "open_exposures": len(handover.open_positions_orders),
        },
    )
    return handover, [action]


def accept_handover(
    settings: AppSettings,
    handover: HandoverRecord,
    operator: str,
    *,
    acknowledged: bool,
    target_session_id: str | None = None,
    comment: str | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    approval_signature: ApprovalSignature | None = None,
    now: datetime | None = None,
) -> HandoverReviewResult:
    """Accept a handover after explicit acknowledgement of carry-over items."""

    timestamp = now or datetime.now(timezone.utc)
    effective = handover_effective_status(settings, handover, now=timestamp)
    blockers: list[str] = []
    if effective == HandoverStatus.ACCEPTED:
        blockers.append("handover has already been accepted")
    if effective == HandoverStatus.REFUSED:
        blockers.append("handover was refused and must be recreated before it can be accepted")
    if effective == HandoverStatus.EXPIRED:
        blockers.append("handover has expired and requires a refreshed review package")
    if not acknowledged:
        blockers.append("explicit acknowledgement of handover warnings and blockers is required")
    if blockers:
        action = _action(
            operator=operator,
            action_type=OperatorActionType.HANDOVER_ACCEPTED,
            mode="operator_review",
            result=OperatorActionResult.DENIED,
            target_type="handover",
            target_id=handover.handover_id,
            linked_checklist_id=handover.linked_checklist_id,
            linked_session_id=handover.source_session_id,
            reason="; ".join(blockers),
            created_at=timestamp,
            auth_context=auth_context,
            approval_signature_id=approval_signature.approval_id if approval_signature else None,
            payload={"handover_status": effective.value},
        )
        return HandoverReviewResult(handover=handover, actions=[action], blocked_reasons=blockers)
    updated = handover.model_copy(
        update={
            "status": HandoverStatus.ACCEPTED,
            "reviewed_at": timestamp,
            "accepted_at": timestamp,
            "target_operator": operator,
            "target_session_id": target_session_id or handover.target_session_id,
            "acceptance_signature_id": approval_signature.approval_id if approval_signature else handover.acceptance_signature_id,
            "notes": comment or handover.notes,
            "refusal_reason": None,
        }
    )
    action = _action(
        operator=operator,
        action_type=OperatorActionType.HANDOVER_ACCEPTED,
        mode="operator_review",
        result=OperatorActionResult.COMPLETED,
        target_type="handover",
        target_id=updated.handover_id,
        linked_checklist_id=updated.linked_checklist_id,
        linked_session_id=updated.source_session_id,
        reason="operator accepted carry-over responsibility for the latest handover",
        created_at=timestamp,
        auth_context=auth_context,
        approval_signature_id=approval_signature.approval_id if approval_signature else None,
        payload={"blocked_items": len(updated.blocked_items)},
    )
    return HandoverReviewResult(handover=updated, actions=[action], blocked_reasons=[])


def refuse_handover(
    handover: HandoverRecord,
    operator: str,
    *,
    refusal_reason: str,
    comment: str | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    now: datetime | None = None,
) -> HandoverReviewResult:
    """Refuse a handover and persist the refusal reason."""

    timestamp = now or datetime.now(timezone.utc)
    blockers: list[str] = []
    if not refusal_reason.strip():
        blockers.append("a refusal reason is required to refuse a handover")
    if handover.status == HandoverStatus.ACCEPTED:
        blockers.append("an accepted handover cannot be refused retroactively")
    if blockers:
        action = _action(
            operator=operator,
            action_type=OperatorActionType.HANDOVER_REFUSED,
            mode="operator_review",
            result=OperatorActionResult.DENIED,
            target_type="handover",
            target_id=handover.handover_id,
            linked_checklist_id=handover.linked_checklist_id,
            linked_session_id=handover.source_session_id,
            reason="; ".join(blockers),
            created_at=timestamp,
            auth_context=auth_context,
        )
        return HandoverReviewResult(handover=handover, actions=[action], blocked_reasons=blockers)
    updated = handover.model_copy(
        update={
            "status": HandoverStatus.REFUSED,
            "reviewed_at": timestamp,
            "target_operator": operator,
            "refusal_reason": refusal_reason.strip(),
            "notes": comment or handover.notes,
        }
    )
    action = _action(
        operator=operator,
        action_type=OperatorActionType.HANDOVER_REFUSED,
        mode="operator_review",
        result=OperatorActionResult.COMPLETED,
        target_type="handover",
        target_id=updated.handover_id,
        linked_checklist_id=updated.linked_checklist_id,
        linked_session_id=updated.source_session_id,
        reason=updated.refusal_reason,
        created_at=timestamp,
        auth_context=auth_context,
        payload={"blocked_items": len(updated.blocked_items)},
    )
    return HandoverReviewResult(handover=updated, actions=[action], blocked_reasons=[])


def open_trading_session(
    settings: AppSettings,
    operator: str,
    checklist: PreSessionChecklist,
    context: OperatorWorkflowContext,
    *,
    confirmed: bool,
    existing_session: TradingSessionRecord | None = None,
    live_authorization: LiveAuthorizationRecord | None = None,
    sessions: list[TradingSessionRecord] | None = None,
    handovers: list[HandoverRecord] | None = None,
    comment: str | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    now: datetime | None = None,
) -> SessionOpenResult:
    """Create an operator-reviewed trading session when prerequisites are satisfied."""

    timestamp = now or datetime.now(timezone.utc)
    blockers: list[str] = []
    if existing_session is not None and existing_session.closed_at is None:
        blockers.append("a trading session is already open")
    if checklist.status == ChecklistStatus.FAIL:
        blockers.extend(checklist.blockers)
    if settings.operator_workflow.require_checklist_acknowledgement_for_session_open and not checklist.acknowledged:
        blockers.append("latest checklist must be acknowledged before opening a session")
    if not confirmed:
        blockers.append("explicit operator confirmation is required before opening a session")
    continuity = evaluate_inter_session_continuity(
        settings,
        context,
        sessions or ([] if existing_session is None else [existing_session]),
        handovers or [],
        live_authorization=live_authorization,
        require_for="session_open",
        now=timestamp,
    )
    blockers.extend(continuity.blockers)
    if settings.execution.mode == "broker_live":
        blockers.extend(
            live_authorization_block_reasons(
                settings,
                checklist,
                live_authorization,
                context,
                sessions=sessions,
                handovers=handovers,
                now=timestamp,
            )
        )
    blockers = _dedupe(blockers)
    if blockers:
        action = _action(
            operator=operator,
            action_type=OperatorActionType.SESSION_OPEN_BLOCKED,
            mode=settings.execution.mode,
            result=OperatorActionResult.DENIED,
            target_type="session",
            linked_checklist_id=checklist.checklist_id,
            reason="; ".join(blockers),
            created_at=timestamp,
            auth_context=auth_context,
            payload={"blocker_count": len(blockers)},
        )
        return SessionOpenResult(checklist=checklist, actions=[action], blocked_reasons=blockers)
    snapshot = context.snapshot
    active_authorization_id = None
    if live_authorization is not None and authorization_effective_status(live_authorization, now=timestamp) == LiveAuthorizationStatus.GRANTED:
        active_authorization_id = live_authorization.authorization_id
    session = TradingSessionRecord(
        session_id=str(uuid.uuid4()),
        opened_at=timestamp,
        operator=operator,
        mode=settings.execution.mode,
        broker=snapshot.broker if snapshot else settings.broker.provider,
        status=TradingSessionStatus.OPEN,
        linked_checklist_id=checklist.checklist_id,
        linked_authorization_id=active_authorization_id,
        open_summary={
            "health_status": snapshot.health_status if snapshot else ("paper_mode" if settings.execution.mode == "paper" else "unknown"),
            "active_alerts": len([alert for alert in context.alerts if alert.status != AlertStatus.RESOLVED]),
            "open_incidents": len([incident for incident in context.incidents if incident.status == BrokerIncidentStatus.OPEN]),
            "open_positions": snapshot.open_positions if snapshot else 0,
            "pending_orders": snapshot.pending_orders if snapshot else 0,
            "kill_switch_active": snapshot.kill_switch_active if snapshot else False,
            "degraded_mode": context.controls.degraded_mode or bool(snapshot and snapshot.degraded_flags),
            "live_capability_enabled": snapshot.live_capability_enabled if snapshot else False,
            "authorized_live": active_authorization_id is not None,
            "continuity_blockers": len(continuity.blockers),
            "continuity_warnings": len(continuity.warnings),
        },
        notes=comment,
    )
    action = _action(
        operator=operator,
        action_type=OperatorActionType.SESSION_OPENED,
        mode=settings.execution.mode,
        result=OperatorActionResult.COMPLETED,
        target_type="session",
        target_id=session.session_id,
        linked_checklist_id=checklist.checklist_id,
        linked_authorization_id=session.linked_authorization_id,
        linked_session_id=session.session_id,
        reason="operator confirmed and opened supervised session",
        created_at=timestamp,
        auth_context=auth_context,
        payload={
            "warnings": len(checklist.warnings),
            "active_alerts": session.open_summary["active_alerts"],
            "open_incidents": session.open_summary["open_incidents"],
        },
    )
    return SessionOpenResult(checklist=checklist, session=session, actions=[action], blocked_reasons=[])


def close_trading_session(
    session: TradingSessionRecord,
    operator: str,
    context: OperatorWorkflowContext,
    *,
    all_orders: list[ExecutionOrder] | None = None,
    comment: str | None = None,
    handoff_required: bool | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    now: datetime | None = None,
) -> SessionCloseResult:
    """Close an open session and record whether unresolved issues require handoff."""

    timestamp = now or datetime.now(timezone.utc)
    orders = all_orders or context.broker_orders
    alerts_during = [alert for alert in context.alerts if alert.opened_at >= session.opened_at]
    incidents_during = [incident for incident in context.incidents if incident.opened_at >= session.opened_at]
    broker_actions_taken = sum(
        1
        for order in orders
        for transition in order.broker_transitions
        if transition.occurred_at >= session.opened_at
    )
    unresolved_alerts = [
        alert
        for alert in context.alerts
        if alert.status != AlertStatus.RESOLVED and alert.severity in {AlertSeverity.HIGH, AlertSeverity.CRITICAL}
    ]
    unresolved_incidents = [
        incident
        for incident in context.incidents
        if incident.status == BrokerIncidentStatus.OPEN and incident.severity in {BrokerIncidentSeverity.HIGH, BrokerIncidentSeverity.CRITICAL}
    ]
    unresolved_anomalies = [anomaly for anomaly in context.anomalies if anomaly.severity in {"high", "critical"}]
    open_orders_remaining = [order for order in context.broker_orders if order.is_open]
    snapshot = context.snapshot
    unresolved_items = _dedupe(
        [
            *(f"alert:{alert.category.value}" for alert in unresolved_alerts),
            *(f"incident:{incident.category.value}" for incident in unresolved_incidents),
            *(f"anomaly:{anomaly.anomaly_type.value}" for anomaly in unresolved_anomalies),
            *(f"open_order:{order.request.symbol}:{order.order_id}" for order in open_orders_remaining),
        ]
    )
    requires_handoff = handoff_required if handoff_required is not None else bool(unresolved_items)
    closed = session.model_copy(
        update={
            "closed_at": timestamp,
            "status": TradingSessionStatus.HANDOFF_REQUIRED if requires_handoff else TradingSessionStatus.CLOSED,
            "handoff_required": requires_handoff,
            "unresolved_items": unresolved_items,
            "close_summary": {
                "alerts_during_session": len(alerts_during),
                "incidents_during_session": len(incidents_during),
                "broker_actions_taken": broker_actions_taken,
                "unresolved_anomalies": len(unresolved_anomalies),
                "open_orders_remaining": len(open_orders_remaining),
                "open_positions_remaining": snapshot.open_positions if snapshot else 0,
                "pending_orders_remaining": snapshot.pending_orders if snapshot else 0,
                "clean_close": not requires_handoff,
            },
            "notes": comment or session.notes,
        }
    )
    actions = [
        _action(
            operator=operator,
            action_type=OperatorActionType.SESSION_CLOSED,
            mode=session.mode,
            result=OperatorActionResult.COMPLETED,
            target_type="session",
            target_id=session.session_id,
            linked_checklist_id=session.linked_checklist_id,
            linked_authorization_id=session.linked_authorization_id,
            linked_session_id=session.session_id,
            reason="operator closed supervised session",
            created_at=timestamp,
            auth_context=auth_context,
            payload={
                "alerts_during_session": len(alerts_during),
                "incidents_during_session": len(incidents_during),
                "broker_actions_taken": broker_actions_taken,
                "handoff_required": requires_handoff,
            },
        )
    ]
    if requires_handoff:
        actions.append(
            _action(
                operator=operator,
                action_type=OperatorActionType.HANDOFF_REQUIRED,
                mode=session.mode,
                result=OperatorActionResult.COMPLETED,
                target_type="session",
                target_id=session.session_id,
                linked_checklist_id=session.linked_checklist_id,
                linked_authorization_id=session.linked_authorization_id,
                linked_session_id=session.session_id,
                reason="session closed with unresolved items that require handoff",
                created_at=timestamp,
                auth_context=auth_context,
                payload={"unresolved_items": len(unresolved_items)},
            )
        )
    return SessionCloseResult(session=closed, actions=actions)


def record_operator_action(
    operator: str,
    action_type: OperatorActionType,
    mode: str,
    *,
    result: OperatorActionResult = OperatorActionResult.COMPLETED,
    target_type: str | None = None,
    target_id: str | None = None,
    linked_checklist_id: str | None = None,
    linked_authorization_id: str | None = None,
    linked_session_id: str | None = None,
    reason: str | None = None,
    payload: dict[str, str | float | int | bool | None] | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    approval_signature_id: str | None = None,
    now: datetime | None = None,
) -> OperatorActionRecord:
    """Create a standalone operator action record."""

    return _action(
        operator=operator,
        action_type=action_type,
        mode=mode,
        result=result,
        target_type=target_type,
        target_id=target_id,
        linked_checklist_id=linked_checklist_id,
        linked_authorization_id=linked_authorization_id,
        linked_session_id=linked_session_id,
        reason=reason,
        payload=payload,
        auth_context=auth_context,
        approval_signature_id=approval_signature_id,
        created_at=now or datetime.now(timezone.utc),
    )


def summarize_live_submission_blockers(
    settings: AppSettings,
    context: OperatorWorkflowContext,
    checklist: PreSessionChecklist | None,
    authorization: LiveAuthorizationRecord | None,
    *,
    sessions: list[TradingSessionRecord] | None = None,
    handovers: list[HandoverRecord] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Return current blockers preventing broker-live submission."""

    return live_authorization_block_reasons(
        settings,
        checklist,
        authorization,
        context,
        sessions=sessions,
        handovers=handovers,
        now=now,
    )


def _execution_mode_item(settings: AppSettings) -> ChecklistItemResult:
    if settings.execution.mode == "broker_live":
        if settings.broker.provider == "mock":
            return ChecklistItemResult(
                item_key=ChecklistItemKey.EXECUTION_MODE,
                label="Execution mode",
                status=ChecklistStatus.FAIL,
                reason="broker_live mode cannot use the mock broker provider",
                details={"mode": settings.execution.mode, "provider": settings.broker.provider},
            )
        if settings.execution_capabilities.broker_live_enabled and settings.broker.live_enabled:
            return ChecklistItemResult(
                item_key=ChecklistItemKey.EXECUTION_MODE,
                label="Execution mode",
                status=ChecklistStatus.PASS,
                reason="broker_live mode is explicitly enabled by config gates",
                details={"mode": settings.execution.mode, "provider": settings.broker.provider},
            )
        return ChecklistItemResult(
            item_key=ChecklistItemKey.EXECUTION_MODE,
            label="Execution mode",
            status=ChecklistStatus.FAIL,
            reason="broker_live mode is selected but live config gates are not fully enabled",
            details={"mode": settings.execution.mode, "provider": settings.broker.provider},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.EXECUTION_MODE,
        label="Execution mode",
        status=ChecklistStatus.PASS,
        reason=f"{settings.execution.mode} mode is selected",
        details={"mode": settings.execution.mode},
    )


def _broker_connectivity_item(mode: str, snapshot: BrokerHealthSnapshot | None) -> ChecklistItemResult:
    if mode == "paper":
        return ChecklistItemResult(
            item_key=ChecklistItemKey.BROKER_CONNECTIVITY,
            label="Broker connectivity",
            status=ChecklistStatus.PASS,
            reason="paper mode does not require broker connectivity",
        )
    if snapshot is None:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.BROKER_CONNECTIVITY,
            label="Broker connectivity",
            status=ChecklistStatus.FAIL,
            reason="no broker health snapshot is available",
        )
    if snapshot.connected:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.BROKER_CONNECTIVITY,
            label="Broker connectivity",
            status=ChecklistStatus.PASS,
            reason="broker connectivity is healthy",
            details={"health_status": snapshot.health_status},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.BROKER_CONNECTIVITY,
        label="Broker connectivity",
        status=ChecklistStatus.FAIL,
        reason=snapshot.last_error or "broker connectivity is unavailable",
        details={"health_status": snapshot.health_status},
    )


def _account_sync_item(
    mode: str,
    snapshot: BrokerHealthSnapshot | None,
    settings: AppSettings,
    now: datetime,
) -> ChecklistItemResult:
    if mode == "paper":
        return ChecklistItemResult(
            item_key=ChecklistItemKey.ACCOUNT_SYNC_FRESHNESS,
            label="Account sync freshness",
            status=ChecklistStatus.PASS,
            reason="paper mode does not require broker account sync",
        )
    if snapshot is None or snapshot.last_successful_account_sync_at is None:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.ACCOUNT_SYNC_FRESHNESS,
            label="Account sync freshness",
            status=ChecklistStatus.FAIL,
            reason="no successful account sync is recorded",
        )
    age = max(0.0, (now - _normalize_time(snapshot.last_successful_account_sync_at)).total_seconds())
    if age > settings.broker_safety.max_account_state_age_seconds:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.ACCOUNT_SYNC_FRESHNESS,
            label="Account sync freshness",
            status=ChecklistStatus.FAIL,
            reason=f"last account sync is stale at {age:.1f}s",
            details={"age_seconds": round(age, 2)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.ACCOUNT_SYNC_FRESHNESS,
        label="Account sync freshness",
        status=ChecklistStatus.PASS,
        reason=f"last account sync is fresh at {age:.1f}s",
        details={"age_seconds": round(age, 2)},
    )


def _position_sync_item(
    mode: str,
    snapshot: BrokerHealthSnapshot | None,
    settings: AppSettings,
    now: datetime,
) -> ChecklistItemResult:
    if mode == "paper":
        return ChecklistItemResult(
            item_key=ChecklistItemKey.POSITION_SYNC_FRESHNESS,
            label="Position sync freshness",
            status=ChecklistStatus.PASS,
            reason="paper mode does not require broker position sync",
        )
    if snapshot is None or snapshot.last_successful_position_sync_at is None:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.POSITION_SYNC_FRESHNESS,
            label="Position sync freshness",
            status=ChecklistStatus.FAIL,
            reason="no successful position sync is recorded",
        )
    age = max(0.0, (now - _normalize_time(snapshot.last_successful_position_sync_at)).total_seconds())
    fail_threshold = settings.monitoring.stale_position_alert_seconds
    if age > fail_threshold:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.POSITION_SYNC_FRESHNESS,
            label="Position sync freshness",
            status=ChecklistStatus.FAIL,
            reason=f"last position sync is stale at {age:.1f}s",
            details={"age_seconds": round(age, 2)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.POSITION_SYNC_FRESHNESS,
        label="Position sync freshness",
        status=ChecklistStatus.PASS,
        reason=f"last position sync is fresh at {age:.1f}s",
        details={"age_seconds": round(age, 2)},
    )


def _reconciliation_item(
    mode: str,
    snapshot: BrokerHealthSnapshot | None,
    anomalies: list[ReconciliationAnomaly],
    settings: AppSettings,
    now: datetime,
) -> ChecklistItemResult:
    if mode == "paper":
        return ChecklistItemResult(
            item_key=ChecklistItemKey.RECONCILIATION_FRESHNESS,
            label="Reconciliation freshness",
            status=ChecklistStatus.PASS,
            reason="paper mode does not require broker reconciliation",
        )
    severe = [anomaly for anomaly in anomalies if anomaly.severity in {"high", "critical"}]
    if severe:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.RECONCILIATION_FRESHNESS,
            label="Reconciliation freshness",
            status=ChecklistStatus.FAIL,
            reason=f"{len(severe)} severe reconciliation anomaly/anomalies remain open",
            details={"severe_anomalies": len(severe)},
        )
    if snapshot is None or snapshot.last_successful_reconciliation_at is None:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.RECONCILIATION_FRESHNESS,
            label="Reconciliation freshness",
            status=ChecklistStatus.FAIL,
            reason="no successful reconciliation timestamp is recorded",
        )
    age = max(0.0, (now - _normalize_time(snapshot.last_successful_reconciliation_at)).total_seconds())
    if age > settings.monitoring.stale_reconciliation_alert_seconds:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.RECONCILIATION_FRESHNESS,
            label="Reconciliation freshness",
            status=ChecklistStatus.FAIL,
            reason=f"last reconciliation is stale at {age:.1f}s",
            details={"age_seconds": round(age, 2)},
        )
    if anomalies:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.RECONCILIATION_FRESHNESS,
            label="Reconciliation freshness",
            status=ChecklistStatus.WARNING,
            reason=f"{len(anomalies)} reconciliation anomaly/anomalies remain under review",
            details={"anomalies": len(anomalies)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.RECONCILIATION_FRESHNESS,
        label="Reconciliation freshness",
        status=ChecklistStatus.PASS,
        reason=f"last reconciliation is fresh at {age:.1f}s",
        details={"age_seconds": round(age, 2)},
    )


def _incident_item(incidents: list[BrokerIncident], settings: AppSettings) -> ChecklistItemResult:
    open_incidents = [incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]
    severe = [incident for incident in open_incidents if incident.severity in {BrokerIncidentSeverity.HIGH, BrokerIncidentSeverity.CRITICAL}]
    if len(severe) >= settings.operator_workflow.fail_severe_incident_count:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.UNRESOLVED_INCIDENTS,
            label="Unresolved incidents",
            status=ChecklistStatus.FAIL,
            reason=f"{len(severe)} severe incident(s) remain unresolved",
            details={"open_incidents": len(open_incidents), "severe_incidents": len(severe)},
        )
    if len(open_incidents) >= settings.operator_workflow.warning_open_incident_count:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.UNRESOLVED_INCIDENTS,
            label="Unresolved incidents",
            status=ChecklistStatus.WARNING,
            reason=f"{len(open_incidents)} non-severe incident(s) remain open",
            details={"open_incidents": len(open_incidents)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.UNRESOLVED_INCIDENTS,
        label="Unresolved incidents",
        status=ChecklistStatus.PASS,
        reason="no unresolved incidents remain",
    )


def _alert_item(alerts: list[OperationalAlert], settings: AppSettings) -> ChecklistItemResult:
    active = [alert for alert in alerts if alert.status != AlertStatus.RESOLVED]
    severe = [alert for alert in active if alert.severity in {AlertSeverity.HIGH, AlertSeverity.CRITICAL}]
    warning_count = [alert for alert in active if alert.severity == AlertSeverity.WARNING]
    if len(severe) >= settings.operator_workflow.fail_active_severe_alert_count:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.ACTIVE_SEVERE_ALERTS,
            label="Active severe alerts",
            status=ChecklistStatus.FAIL,
            reason=f"{len(severe)} high/critical alert(s) remain active",
            details={"active_alerts": len(active), "severe_alerts": len(severe)},
        )
    if len(warning_count) >= settings.operator_workflow.warning_active_alert_count:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.ACTIVE_SEVERE_ALERTS,
            label="Active severe alerts",
            status=ChecklistStatus.WARNING,
            reason=f"{len(warning_count)} warning alert(s) remain active",
            details={"active_alerts": len(active), "warning_alerts": len(warning_count)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.ACTIVE_SEVERE_ALERTS,
        label="Active severe alerts",
        status=ChecklistStatus.PASS,
        reason="no active severe alerts remain",
    )


def _degraded_mode_item(snapshot: BrokerHealthSnapshot | None, controls: OperatorControlState) -> ChecklistItemResult:
    flags = list(snapshot.degraded_flags) if snapshot else []
    if controls.maintenance_mode:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.DEGRADED_MODE_STATE,
            label="Degraded mode state",
            status=ChecklistStatus.FAIL,
            reason="maintenance mode is active",
            details={"maintenance_mode": True},
        )
    if controls.degraded_mode or flags:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.DEGRADED_MODE_STATE,
            label="Degraded mode state",
            status=ChecklistStatus.WARNING,
            reason="degraded mode or degraded broker flags remain active",
            details={"degraded_mode": controls.degraded_mode, "flags": ",".join(flags)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.DEGRADED_MODE_STATE,
        label="Degraded mode state",
        status=ChecklistStatus.PASS,
        reason="no degraded operating mode is active",
    )


def _kill_switch_item(snapshot: BrokerHealthSnapshot | None) -> ChecklistItemResult:
    if snapshot is not None and snapshot.kill_switch_active:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.KILL_SWITCH_STATE,
            label="Kill switch state",
            status=ChecklistStatus.FAIL,
            reason="kill switch is active",
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.KILL_SWITCH_STATE,
        label="Kill switch state",
        status=ChecklistStatus.PASS,
        reason="kill switch is not active",
    )


def _data_quality_item(alerts: list[OperationalAlert]) -> ChecklistItemResult:
    relevant = [alert for alert in alerts if alert.category == AlertCategory.DEGRADED_DATA_QUALITY and alert.status != AlertStatus.RESOLVED]
    severe = [alert for alert in relevant if alert.severity in {AlertSeverity.HIGH, AlertSeverity.CRITICAL}]
    if severe:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.DATA_QUALITY_STATUS,
            label="Data quality status",
            status=ChecklistStatus.FAIL,
            reason="degraded data quality alert remains active",
            details={"alerts": len(relevant)},
        )
    if relevant:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.DATA_QUALITY_STATUS,
            label="Data quality status",
            status=ChecklistStatus.WARNING,
            reason="data quality warning remains active",
            details={"alerts": len(relevant)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.DATA_QUALITY_STATUS,
        label="Data quality status",
        status=ChecklistStatus.PASS,
        reason="no active data quality alerts remain",
    )


def _spread_sanity_item(alerts: list[OperationalAlert]) -> ChecklistItemResult:
    relevant = [alert for alert in alerts if alert.category == AlertCategory.ABNORMAL_SPREAD and alert.status != AlertStatus.RESOLVED]
    severe = [alert for alert in relevant if alert.severity in {AlertSeverity.HIGH, AlertSeverity.CRITICAL}]
    if severe:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.SPREAD_SANITY,
            label="Spread sanity",
            status=ChecklistStatus.FAIL,
            reason="abnormal spread alert remains active",
            details={"alerts": len(relevant)},
        )
    if relevant:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.SPREAD_SANITY,
            label="Spread sanity",
            status=ChecklistStatus.WARNING,
            reason="spread warning remains active",
            details={"alerts": len(relevant)},
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.SPREAD_SANITY,
        label="Spread sanity",
        status=ChecklistStatus.PASS,
        reason="no abnormal spread alert is active",
    )


def _guardrail_item(settings: AppSettings) -> ChecklistItemResult:
    if settings.portfolio_risk.enabled and settings.pre_live_validation.enabled:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.GUARDRAIL_CONFIGURATION,
            label="Guardrail configuration",
            status=ChecklistStatus.PASS,
            reason="portfolio and pre-live guardrails are loaded and enabled",
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.GUARDRAIL_CONFIGURATION,
        label="Guardrail configuration",
        status=ChecklistStatus.WARNING,
        reason="one or more guardrail layers are disabled by config",
        details={
            "portfolio_risk_enabled": settings.portfolio_risk.enabled,
            "pre_live_validation_enabled": settings.pre_live_validation.enabled,
        },
    )


def _monitoring_item(settings: AppSettings) -> ChecklistItemResult:
    if settings.monitoring.enabled and settings.monitoring.metrics_export_enabled:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.MONITORING_EXPORTER_HEALTH,
            label="Monitoring/exporter health",
            status=ChecklistStatus.PASS,
            reason="monitoring and metrics export are enabled",
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.MONITORING_EXPORTER_HEALTH,
        label="Monitoring/exporter health",
        status=ChecklistStatus.WARNING,
        reason="monitoring or metrics export is disabled",
        details={
            "monitoring_enabled": settings.monitoring.enabled,
            "metrics_export_enabled": settings.monitoring.metrics_export_enabled,
        },
    )


def _campaign_readiness_item(mode: str, campaign: SoakCampaign | None, settings: AppSettings) -> ChecklistItemResult:
    if mode != "broker_live":
        return ChecklistItemResult(
            item_key=ChecklistItemKey.CAMPAIGN_READINESS,
            label="Campaign readiness",
            status=ChecklistStatus.PASS,
            reason="campaign readiness is not required for the current mode",
        )
    blockers = _campaign_readiness_blockers(settings, campaign)
    if blockers:
        return ChecklistItemResult(
            item_key=ChecklistItemKey.CAMPAIGN_READINESS,
            label="Campaign readiness",
            status=ChecklistStatus.FAIL,
            reason="; ".join(blockers),
        )
    return ChecklistItemResult(
        item_key=ChecklistItemKey.CAMPAIGN_READINESS,
        label="Campaign readiness",
        status=ChecklistStatus.PASS,
        reason=f"campaign readiness {campaign.readiness.value if campaign and campaign.readiness else 'unknown'} meets the minimum threshold",
    )


def _campaign_readiness_blockers(settings: AppSettings, campaign: SoakCampaign | None) -> list[str]:
    if not settings.operator_workflow.require_campaign_readiness_for_live_authorization:
        return []
    if campaign is None or campaign.readiness is None:
        return ["no finalized soak campaign readiness is available"]
    if _readiness_rank(campaign.readiness) < _required_readiness_rank(settings):
        return [
            f"campaign readiness {campaign.readiness.value} is below required {settings.operator_workflow.minimum_readiness_for_live_authorization}"
        ]
    return []


def _required_readiness_rank(settings: AppSettings) -> int:
    return {
        "limited_ready": 1,
        "supervised_ready": 2,
    }[settings.operator_workflow.minimum_readiness_for_live_authorization]


def _readiness_rank(readiness: SoakCampaignReadiness) -> int:
    return {
        SoakCampaignReadiness.NOT_READY: 0,
        SoakCampaignReadiness.LIMITED_READY: 1,
        SoakCampaignReadiness.SUPERVISED_READY: 2,
    }[readiness]


def _latest_continuity_transition_time(
    latest_session: TradingSessionRecord | None,
    latest_handover: HandoverRecord | None,
) -> datetime | None:
    values = [
        latest_session.closed_at if latest_session is not None else None,
        latest_handover.accepted_at if latest_handover is not None else None,
        latest_handover.created_at if latest_handover is not None else None,
    ]
    normalized = [_normalize_time(value) for value in values if value is not None]
    if not normalized:
        return None
    return max(normalized)


def _handover_issue_from_incident(incident: BrokerIncident) -> HandoverIssue:
    return HandoverIssue(
        record_id=incident.incident_id,
        severity=incident.severity.value,
        status=incident.status.value,
        label=incident.category.value,
        reason=incident.reason,
    )


def _handover_issue_from_alert(alert: OperationalAlert) -> HandoverIssue:
    return HandoverIssue(
        record_id=alert.alert_id,
        severity=alert.severity.value,
        status=alert.status.value,
        label=alert.category.value,
        reason=alert.reason,
    )


def _handover_issue_from_anomaly(anomaly: ReconciliationAnomaly) -> HandoverIssue:
    return HandoverIssue(
        record_id=anomaly.anomaly_id,
        severity=anomaly.severity,
        status="open",
        label=anomaly.anomaly_type.value,
        reason=anomaly.reason,
    )


def _severity_rank_value(value: str) -> int:
    return {
        "info": 0,
        "low": 0,
        "warning": 1,
        "medium": 1,
        "high": 2,
        "critical": 3,
    }.get(value, 0)


def _action(
    *,
    operator: str,
    action_type: OperatorActionType,
    mode: str,
    result: OperatorActionResult,
    created_at: datetime,
    target_type: str | None = None,
    target_id: str | None = None,
    linked_checklist_id: str | None = None,
    linked_authorization_id: str | None = None,
    linked_session_id: str | None = None,
    reason: str | None = None,
    payload: dict[str, str | float | int | bool | None] | None = None,
    auth_context: AuthenticatedOperatorContext | None = None,
    approval_signature_id: str | None = None,
) -> OperatorActionRecord:
    return OperatorActionRecord(
        action_id=str(uuid.uuid4()),
        created_at=created_at,
        operator=operator,
        operator_id=auth_context.identity.operator_id if auth_context else None,
        operator_display_name=auth_context.identity.display_name if auth_context else None,
        operator_role=auth_context.identity.role if auth_context else None,
        auth_session_id=auth_context.auth_session.auth_session_id if auth_context else None,
        approval_signature_id=approval_signature_id,
        action_type=action_type,
        result=result,
        mode=mode,
        target_type=target_type,
        target_id=target_id,
        linked_checklist_id=linked_checklist_id,
        linked_authorization_id=linked_authorization_id,
        linked_session_id=linked_session_id,
        reason=reason,
        payload=payload or {},
    )


def _normalize_time(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
