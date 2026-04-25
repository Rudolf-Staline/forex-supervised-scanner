"""Broker operational health, incident, and restart-recovery helpers."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
import os

from pydantic import BaseModel, Field

from app.config.settings import AppSettings
from app.execution.models import BrokerAccountState, BrokerOrderState, ExecutionOrder, TradeEvent, TradeEventType
from app.execution.reconciliation import ReconciliationAnomaly, ReconciliationAnomalyType, ReconciliationReport, reconcile_broker_state

LOGGER = logging.getLogger(__name__)


class BrokerIncidentCategory(str, Enum):
    """Operational categories that can block supervised broker execution."""

    BROKER_UNAVAILABLE = "broker_unavailable"
    MT5_TERMINAL_NOT_REACHABLE = "mt5_terminal_not_reachable"
    ACCOUNT_STATE_STALE = "account_state_stale"
    REPEATED_SUBMIT_FAILURES = "repeated_submit_failures"
    RECONCILIATION_ANOMALY = "reconciliation_anomaly"
    REPEATED_REJECTS = "repeated_rejects"
    PARTIAL_DESYNC = "partial_local_broker_desync"
    RESTART_UNFINISHED_STATE = "restart_unfinished_state"
    UNKNOWN_BROKER_STATE = "unknown_broker_state"
    CONNECTIVITY_UNSTABLE = "connectivity_unstable"
    LIVE_GUARDRAIL_BLOCK = "live_guardrail_block"
    STALE_BROKER_STATE = "stale_broker_state"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"


class BrokerIncidentSeverity(str, Enum):
    """Operator-facing incident severity."""

    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class BrokerIncidentStatus(str, Enum):
    """Incident lifecycle state."""

    OPEN = "open"
    CLOSED = "closed"


class AlertSeverity(str, Enum):
    """Local operator alert severity."""

    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class AlertCategory(str, Enum):
    """Supported local alert categories."""

    BROKER_DOWN = "broker_down"
    STALE_ACCOUNT_STATE = "stale_account_state"
    STALE_POSITION_STATE = "stale_position_state"
    STALE_RECONCILIATION = "stale_reconciliation"
    REPEATED_BROKER_REJECTS = "repeated_broker_rejects"
    RETRIES_EXHAUSTED = "retries_exhausted"
    SEVERE_RECONCILIATION_MISMATCH = "severe_reconciliation_mismatch"
    KILL_SWITCH_ACTIVE = "kill_switch_active"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"
    DEGRADED_MODE = "degraded_mode"
    GUARDRAIL_TRIGGER_SPIKE = "guardrail_trigger_spike"
    LIVE_SUBMISSION_FAILURES = "live_submission_failures"
    ABNORMAL_SPREAD = "abnormal_spread"
    DEGRADED_DATA_QUALITY = "degraded_data_quality"
    OPERATIONAL_INCIDENT = "operational_incident"


class AlertStatus(str, Enum):
    """Alert lifecycle state."""

    ACTIVE = "active"
    SUPPRESSED = "suppressed"
    RESOLVED = "resolved"


class ResumeReadinessStatus(str, Enum):
    """Operator-facing resume-live readiness state."""

    SAFE_TO_RESUME = "safe_to_resume"
    DEGRADED_BUT_SAFE = "degraded_but_safe"
    BLOCKED_PENDING_MANUAL_REVIEW = "blocked_pending_manual_review"


class BrokerHealthSnapshot(BaseModel):
    """Structured broker-health snapshot persisted for operator review."""

    snapshot_id: str
    created_at: datetime
    broker: str
    mode: str
    connected: bool
    can_trade: bool
    health_status: str
    degraded_flags: list[str] = Field(default_factory=list)
    last_error: str | None = None
    error_category: str | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    open_positions: int = Field(default=0, ge=0)
    pending_orders: int = Field(default=0, ge=0)
    last_successful_account_sync_at: datetime | None = None
    last_successful_position_sync_at: datetime | None = None
    last_successful_reconciliation_at: datetime | None = None
    kill_switch_active: bool = False
    live_capability_enabled: bool = False
    active_incidents: int = Field(default=0, ge=0)
    open_reconciliation_anomalies: int = Field(default=0, ge=0)
    last_successful_broker_action_at: datetime | None = None
    last_failed_broker_action_at: datetime | None = None
    blocking_incidents: int = Field(default=0, ge=0)
    manual_intervention_required: bool = False
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class BrokerIncident(BaseModel):
    """One operational incident requiring review or automatic blocking."""

    incident_id: str
    opened_at: datetime
    updated_at: datetime | None = None
    category: BrokerIncidentCategory
    severity: BrokerIncidentSeverity
    status: BrokerIncidentStatus = BrokerIncidentStatus.OPEN
    reason: str
    recommendation: str
    symbol: str | None = None
    order_id: str | None = None
    broker_order_id: str | None = None
    closed_at: datetime | None = None
    resolved_at: datetime | None = None
    linked_alert_ids: list[str] = Field(default_factory=list)
    linked_anomaly_ids: list[str] = Field(default_factory=list)
    linked_journal_event_ids: list[str] = Field(default_factory=list)
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)

    @property
    def blocks_execution(self) -> bool:
        """Return true when this incident should stop new broker submissions."""

        return self.status == BrokerIncidentStatus.OPEN and self.severity in {
            BrokerIncidentSeverity.HIGH,
            BrokerIncidentSeverity.CRITICAL,
        }


class BrokerRecoveryResult(BaseModel):
    """Result of a startup/recovery synchronization pass."""

    account_state: BrokerAccountState
    snapshot: BrokerHealthSnapshot
    incidents: list[BrokerIncident]
    reconciliation_report: ReconciliationReport
    updated_orders: list[ExecutionOrder]
    events: list[TradeEvent]


class BrokerIncidentResolution(BaseModel):
    """Closed incidents and audit events produced by a recovery pass."""

    closed_incidents: list[BrokerIncident] = Field(default_factory=list)
    events: list[TradeEvent] = Field(default_factory=list)


class OperationalMetric(BaseModel):
    """One persisted operational metric sample."""

    metric_id: str
    recorded_at: datetime
    name: str
    value: float
    status: str
    broker: str
    mode: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class OperationalAlert(BaseModel):
    """Local structured alert for supervised operator review."""

    alert_id: str
    category: AlertCategory
    severity: AlertSeverity
    status: AlertStatus
    opened_at: datetime
    updated_at: datetime
    reason: str
    recommendation: str
    dedupe_key: str
    suppression_until: datetime | None = None
    resolved_at: datetime | None = None
    linked_incident_ids: list[str] = Field(default_factory=list)
    linked_anomaly_ids: list[str] = Field(default_factory=list)
    linked_order_ids: list[str] = Field(default_factory=list)
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class OperatorControlState(BaseModel):
    """Persisted operator controls for broker submissions and live readiness."""

    control_id: str = "default"
    updated_at: datetime
    updated_by: str = "operator"
    broker_submissions_enabled: bool = True
    live_submissions_enabled: bool = False
    maintenance_mode: bool = False
    degraded_mode: bool = False
    acknowledged_incident_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class ResumeReadiness(BaseModel):
    """Current decision on whether broker submissions can safely resume."""

    status: ResumeReadinessStatus
    checked_at: datetime
    reasons: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    broker_mode: str
    broker: str
    unresolved_incidents: int = Field(default=0, ge=0)
    active_alerts: int = Field(default=0, ge=0)
    severe_anomalies: int = Field(default=0, ge=0)


class ReliabilitySummary(BaseModel):
    """Aggregated operational reliability metrics over persisted history."""

    generated_at: datetime
    samples: int = Field(ge=0)
    broker_uptime_pct: float
    health_success_pct: float
    reconciliation_reliability_pct: float
    account_sync_reliability_pct: float
    position_sync_reliability_pct: float
    order_submission_success_rate_pct: float
    rejection_rate_pct: float
    retry_exhaustion_rate_pct: float
    incident_rate_per_sample: float
    guardrail_trigger_rate_per_sample: float
    recovery_success_pct: float
    mean_time_to_detect_minutes: float | None = None
    mean_time_to_resolve_minutes: float | None = None


def build_broker_health_snapshot(
    account_state: BrokerAccountState,
    orders: list[ExecutionOrder],
    anomalies: list[ReconciliationAnomaly],
    settings: AppSettings,
    *,
    now: datetime | None = None,
    last_reconciliation_at: datetime | None = None,
) -> BrokerHealthSnapshot:
    """Create a broker-health snapshot from account, order, and anomaly state."""

    timestamp = now or datetime.now(timezone.utc)
    flags = _degraded_flags(account_state, orders, anomalies, settings, timestamp)
    manual = any(_has_state(order, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED) for order in orders)
    blocking = sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"})
    account_synced_at = account_state.retrieved_at if account_state.connected and account_state.balance is not None else None
    position_synced_at = account_state.retrieved_at if account_state.connected else None
    success_action, failed_action = _last_broker_action_times(orders)
    kill_switch = os.getenv(settings.broker.kill_switch_env, "").strip().lower() in {"1", "true", "yes", "on"}
    return BrokerHealthSnapshot(
        snapshot_id=str(uuid.uuid4()),
        created_at=timestamp,
        broker=account_state.broker,
        mode=account_state.mode,
        connected=account_state.connected,
        can_trade=account_state.can_trade,
        health_status=_health_status(account_state, flags, manual),
        degraded_flags=flags,
        last_error=account_state.last_error,
        error_category=account_state.error_category.value if account_state.error_category else None,
        consecutive_failures=account_state.consecutive_failures,
        open_positions=account_state.open_positions,
        pending_orders=account_state.pending_orders,
        last_successful_account_sync_at=account_synced_at,
        last_successful_position_sync_at=position_synced_at,
        last_successful_reconciliation_at=last_reconciliation_at,
        kill_switch_active=kill_switch,
        live_capability_enabled=settings.execution_capabilities.broker_live_enabled and settings.broker.live_enabled,
        active_incidents=blocking,
        open_reconciliation_anomalies=len(anomalies),
        last_successful_broker_action_at=success_action,
        last_failed_broker_action_at=failed_action,
        blocking_incidents=blocking,
        manual_intervention_required=manual,
        payload={
            "max_account_age_seconds": settings.broker_safety.max_account_state_age_seconds,
            "max_connectivity_failures": settings.broker_safety.max_connectivity_failures,
            "orders_tracked": len(orders),
            "anomalies": len(anomalies),
            "execution_mode": settings.execution.mode,
            "kill_switch_env": settings.broker.kill_switch_env,
        },
    )


def classify_broker_incidents(
    account_state: BrokerAccountState,
    orders: list[ExecutionOrder],
    anomalies: list[ReconciliationAnomaly],
    settings: AppSettings,
    *,
    now: datetime | None = None,
) -> list[BrokerIncident]:
    """Classify current operational broker issues into actionable incidents."""

    timestamp = now or datetime.now(timezone.utc)
    incidents: list[BrokerIncident] = []
    if not account_state.connected:
        category = BrokerIncidentCategory.MT5_TERMINAL_NOT_REACHABLE if account_state.broker == "mt5" else BrokerIncidentCategory.BROKER_UNAVAILABLE
        incidents.append(
            _incident(
                category,
                BrokerIncidentSeverity.HIGH,
                account_state.last_error or "broker connectivity is unavailable",
                "Verify terminal/session connectivity, credentials, and broker availability before retrying.",
                timestamp,
                payload={"broker": account_state.broker, "mode": account_state.mode},
            )
        )
    if account_state.retrieved_at and _account_age_seconds(account_state.retrieved_at, timestamp) > settings.broker_safety.max_account_state_age_seconds:
        incidents.append(
            _incident(
                BrokerIncidentCategory.ACCOUNT_STATE_STALE,
                BrokerIncidentSeverity.HIGH,
                "broker account state is stale",
                "Run broker_check or recovery sync and block submissions until a fresh account snapshot is available.",
                timestamp,
                payload={"retrieved_at": account_state.retrieved_at.isoformat()},
            )
        )
    if account_state.consecutive_failures > settings.broker_safety.max_connectivity_failures:
        incidents.append(
            _incident(
                BrokerIncidentCategory.CONNECTIVITY_UNSTABLE,
                BrokerIncidentSeverity.HIGH,
                "broker connectivity failure count exceeds configured tolerance",
                "Pause broker submissions, inspect terminal logs, then run a fresh health check.",
                timestamp,
                payload={"consecutive_failures": account_state.consecutive_failures},
            )
        )
    rejected_streak = _recent_reject_streak(orders)
    if rejected_streak >= settings.broker_safety.max_repeated_rejects:
        incidents.append(
            _incident(
                BrokerIncidentCategory.REPEATED_REJECTS,
                BrokerIncidentSeverity.HIGH,
                "repeated broker rejects reached the configured cap",
                "Inspect rejection payloads and broker symbol/volume constraints before submitting more orders.",
                timestamp,
                payload={"rejected_streak": rejected_streak},
            )
        )
    submit_failures = sum(1 for order in orders if _has_state(order, BrokerOrderState.RETRY_EXHAUSTED))
    if submit_failures:
        incidents.append(
            _incident(
                BrokerIncidentCategory.REPEATED_SUBMIT_FAILURES,
                BrokerIncidentSeverity.HIGH,
                "one or more broker operations exhausted retries",
                "Confirm broker-side state manually to avoid duplicate orders before retrying.",
                timestamp,
                payload={"retry_exhausted_orders": submit_failures},
            )
        )
    for order in orders:
        if _has_state(order, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED):
            incidents.append(
                _incident(
                    BrokerIncidentCategory.MANUAL_INTERVENTION_REQUIRED,
                    BrokerIncidentSeverity.CRITICAL,
                    "broker order requires manual intervention",
                    "Review the broker terminal and reconcile local state before any further live submission.",
                    timestamp,
                    symbol=order.request.symbol,
                    order_id=order.order_id,
                    broker_order_id=order.broker_order_id,
                )
            )
    for anomaly in anomalies:
        incidents.append(_incident_from_anomaly(anomaly, timestamp))
    unfinished = [order for order in orders if order.is_open]
    if unfinished and settings.execution.mode in {"broker_sandbox", "broker_live"}:
        incidents.append(
            _incident(
                BrokerIncidentCategory.RESTART_UNFINISHED_STATE,
                BrokerIncidentSeverity.WARNING,
                "restart/recovery found unfinished broker orders",
                "Run reconciliation before allowing new submissions and verify every open local order has matching broker state.",
                timestamp,
                payload={"unfinished_orders": len(unfinished)},
            )
        )
    return incidents


def operational_events_from_snapshot_and_incidents(
    snapshot: BrokerHealthSnapshot,
    incidents: list[BrokerIncident],
) -> list[TradeEvent]:
    """Convert health degradation and incidents into the shared event trail."""

    events: list[TradeEvent] = [
        TradeEvent(
            event_id=str(uuid.uuid4()),
            trade_id="broker-operational",
            event_type=TradeEventType.BROKER_STARTUP_RESYNC,
            occurred_at=snapshot.created_at,
            symbol="BROKER",
            status=snapshot.health_status,
            reason="broker operational snapshot recorded",
            payload={"snapshot_id": snapshot.snapshot_id, "degraded_flags": ",".join(snapshot.degraded_flags)},
        )
    ]
    events.append(
        TradeEvent(
            event_id=str(uuid.uuid4()),
            trade_id="broker-operational",
            event_type=TradeEventType.BROKER_RECOVERY_ACTION,
            occurred_at=snapshot.created_at,
            symbol="BROKER",
            status=snapshot.health_status,
            reason="startup recovery evaluated account, local orders, reconciliation, and incidents",
            payload={"snapshot_id": snapshot.snapshot_id, "incident_count": len(incidents)},
        )
    )
    if snapshot.degraded_flags:
        events.append(
            TradeEvent(
                event_id=str(uuid.uuid4()),
                trade_id="broker-operational",
                event_type=TradeEventType.BROKER_HEALTH_DEGRADED,
                occurred_at=snapshot.created_at,
                symbol="BROKER",
                status=snapshot.health_status,
                reason=", ".join(snapshot.degraded_flags),
                payload={"snapshot_id": snapshot.snapshot_id},
            )
        )
    if snapshot.consecutive_failures > 0:
        events.append(
            TradeEvent(
                event_id=str(uuid.uuid4()),
                trade_id="broker-operational",
                event_type=TradeEventType.BROKER_RECONNECT_ATTEMPTED,
                occurred_at=snapshot.created_at,
                symbol="BROKER",
                status=snapshot.health_status,
                reason="broker connection/account sync retry path was exercised",
                payload={"consecutive_failures": snapshot.consecutive_failures, "snapshot_id": snapshot.snapshot_id},
            )
        )
    for incident in incidents:
        events.append(
            TradeEvent(
                event_id=str(uuid.uuid4()),
                trade_id=incident.order_id or "broker-operational",
                event_type=TradeEventType.BROKER_INCIDENT_OPENED,
                occurred_at=incident.opened_at,
                symbol=incident.symbol or "BROKER",
                status=incident.severity.value,
                reason=incident.reason,
                payload={
                    "incident_id": incident.incident_id,
                    "category": incident.category.value,
                    "recommendation": incident.recommendation,
                    "blocks_execution": incident.blocks_execution,
                },
            )
        )
        if incident.blocks_execution:
            events.append(
                TradeEvent(
                    event_id=str(uuid.uuid4()),
                    trade_id=incident.order_id or "broker-operational",
                    event_type=TradeEventType.BROKER_EXECUTION_BLOCKED_OPERATIONAL,
                    occurred_at=incident.opened_at,
                    symbol=incident.symbol or "BROKER",
                    status=incident.severity.value,
                    reason=incident.reason,
                    payload={"incident_id": incident.incident_id, "category": incident.category.value},
                )
            )
    return events


def resolve_recovered_incidents(
    previous_incidents: list[BrokerIncident],
    current_incidents: list[BrokerIncident],
    *,
    now: datetime | None = None,
) -> BrokerIncidentResolution:
    """Close previously open incidents that no longer appear in current recovery state."""

    timestamp = now or datetime.now(timezone.utc)
    current_keys = {_incident_key(incident) for incident in current_incidents if incident.status == BrokerIncidentStatus.OPEN}
    closed: list[BrokerIncident] = []
    events: list[TradeEvent] = []
    for incident in previous_incidents:
        if incident.status != BrokerIncidentStatus.OPEN or _incident_key(incident) in current_keys:
            continue
        resolved = incident.model_copy(update={"status": BrokerIncidentStatus.CLOSED, "updated_at": timestamp, "closed_at": timestamp, "resolved_at": timestamp})
        closed.append(resolved)
        events.append(
            TradeEvent(
                event_id=str(uuid.uuid4()),
                trade_id=resolved.order_id or "broker-operational",
                event_type=TradeEventType.BROKER_INCIDENT_CLOSED,
                occurred_at=timestamp,
                symbol=resolved.symbol or "BROKER",
                status=resolved.severity.value,
                reason=f"incident resolved by recovery: {resolved.reason}",
                payload={"incident_id": resolved.incident_id, "category": resolved.category.value},
            )
        )
    return BrokerIncidentResolution(closed_incidents=closed, events=events)


def merge_operational_incidents(
    previous_incidents: list[BrokerIncident],
    current_incidents: list[BrokerIncident],
    *,
    now: datetime | None = None,
) -> list[BrokerIncident]:
    """Reuse and update existing open incidents when the same condition persists."""

    timestamp = now or datetime.now(timezone.utc)
    previous_by_key = {_incident_key(incident): incident for incident in previous_incidents if incident.status == BrokerIncidentStatus.OPEN}
    merged: list[BrokerIncident] = []
    for incident in current_incidents:
        previous = previous_by_key.get(_incident_key(incident))
        if previous is None:
            merged.append(incident)
            continue
        merged.append(
            incident.model_copy(
                update={
                    "incident_id": previous.incident_id,
                    "opened_at": previous.opened_at,
                    "updated_at": timestamp,
                    "linked_alert_ids": previous.linked_alert_ids,
                    "linked_anomaly_ids": sorted(set([*previous.linked_anomaly_ids, *incident.linked_anomaly_ids])),
                    "linked_journal_event_ids": previous.linked_journal_event_ids,
                }
            )
        )
    return merged


def build_operational_metrics(
    snapshot: BrokerHealthSnapshot,
    incidents: list[BrokerIncident],
    anomalies: list[ReconciliationAnomaly],
    orders: list[ExecutionOrder],
) -> list[OperationalMetric]:
    """Build queryable metric samples from current broker operational state."""

    severe_anomalies = [anomaly for anomaly in anomalies if anomaly.severity in {"high", "critical"}]
    open_incidents = [incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]
    blocking_incidents = [incident for incident in open_incidents if incident.blocks_execution]
    retry_exhausted = sum(1 for order in orders if _has_state(order, BrokerOrderState.RETRY_EXHAUSTED))
    broker_rejects = sum(1 for order in orders if _has_state(order, BrokerOrderState.REJECTED))
    submit_attempts = sum(1 for order in orders if _has_state(order, BrokerOrderState.SUBMIT_REQUESTED))
    live_guardrails = sum(1 for order in orders for event in order.events if event.event_type == TradeEventType.LIVE_GUARDRAIL_TRIGGERED)
    manual_events = sum(1 for order in orders if _has_state(order, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED))
    stale_detections = sum(1 for flag in snapshot.degraded_flags if "stale" in flag) + sum(1 for anomaly in anomalies if anomaly.anomaly_type == ReconciliationAnomalyType.STALE_BROKER_SNAPSHOT)
    metric_values = {
        "broker_connected": 1.0 if snapshot.connected else 0.0,
        "broker_can_trade": 1.0 if snapshot.can_trade else 0.0,
        "health_check_ok": 1.0 if snapshot.health_status == "healthy" else 0.0,
        "reconciliation_anomalies": float(len(anomalies)),
        "blocking_reconciliation_anomalies": float(len(severe_anomalies)),
        "account_sync_success": 1.0 if snapshot.last_successful_account_sync_at else 0.0,
        "position_sync_success": 1.0 if snapshot.last_successful_position_sync_at else 0.0,
        "order_submission_attempts": float(submit_attempts),
        "broker_rejects": float(broker_rejects),
        "retry_exhausted": float(retry_exhausted),
        "open_incidents": float(len(open_incidents)),
        "blocking_incidents": float(len(blocking_incidents)),
        "live_guardrail_triggers": float(live_guardrails),
        "stale_state_detections": float(stale_detections),
        "manual_intervention_required": float(manual_events),
        "restart_recovery_events": 1.0,
    }
    metrics = [
        OperationalMetric(
            metric_id=str(uuid.uuid4()),
            recorded_at=snapshot.created_at,
            name=name,
            value=value,
            status=snapshot.health_status,
            broker=snapshot.broker,
            mode=snapshot.mode,
            dimensions={"health_status": snapshot.health_status},
            payload={"snapshot_id": snapshot.snapshot_id},
        )
        for name, value in metric_values.items()
    ]
    metrics.extend(_incident_metrics(snapshot, incidents))
    return metrics


def generate_operational_alerts(
    snapshot: BrokerHealthSnapshot,
    incidents: list[BrokerIncident],
    anomalies: list[ReconciliationAnomaly],
    orders: list[ExecutionOrder],
    settings: AppSettings,
    previous_alerts: list[OperationalAlert] | None = None,
) -> list[OperationalAlert]:
    """Generate deduplicated local alerts from current operational state."""

    if not settings.monitoring.enabled:
        return []
    previous_alerts = previous_alerts or []
    candidates: list[OperationalAlert] = []
    if not snapshot.connected:
        candidates.append(_alert(AlertCategory.BROKER_DOWN, AlertSeverity.HIGH, "broker connectivity is unavailable", "Restore broker connectivity and rerun recovery before submissions.", snapshot))
    if "account_state_stale" in snapshot.degraded_flags:
        candidates.append(_alert(AlertCategory.STALE_ACCOUNT_STATE, AlertSeverity.HIGH, "broker account state is stale", "Refresh account state and verify terminal/account health.", snapshot))
    if _reconciliation_age_seconds(snapshot, snapshot.created_at) > settings.monitoring.stale_reconciliation_alert_seconds:
        candidates.append(_alert(AlertCategory.STALE_RECONCILIATION, AlertSeverity.WARNING, "broker reconciliation is stale", "Run broker_recovery before new broker submissions.", snapshot))
    if _recent_reject_streak(orders) >= settings.monitoring.repeated_reject_alert_threshold:
        candidates.append(_alert(AlertCategory.REPEATED_BROKER_REJECTS, AlertSeverity.HIGH, "repeated broker rejects reached alert threshold", "Inspect broker rejection payloads and symbol/volume settings.", snapshot))
    if sum(1 for order in orders if _has_state(order, BrokerOrderState.RETRY_EXHAUSTED)) >= settings.monitoring.retry_exhausted_alert_threshold:
        candidates.append(_alert(AlertCategory.RETRIES_EXHAUSTED, AlertSeverity.HIGH, "broker retry exhaustion detected", "Manually verify broker state before retrying.", snapshot))
    severe_anomalies = [anomaly for anomaly in anomalies if anomaly.severity in {"high", "critical"}]
    if len(severe_anomalies) >= settings.monitoring.severe_anomaly_alert_threshold:
        candidates.append(
            _alert(
                AlertCategory.SEVERE_RECONCILIATION_MISMATCH,
                AlertSeverity.CRITICAL,
                "severe reconciliation mismatch detected",
                "Stop broker submissions and inspect reconciliation anomaly report.",
                snapshot,
                linked_anomaly_ids=[anomaly.anomaly_id for anomaly in severe_anomalies],
            )
        )
    if snapshot.kill_switch_active:
        candidates.append(_alert(AlertCategory.KILL_SWITCH_ACTIVE, AlertSeverity.CRITICAL, "broker kill switch is active", "Keep submissions blocked until operator clears the kill switch after review.", snapshot))
    if snapshot.manual_intervention_required:
        candidates.append(_alert(AlertCategory.MANUAL_INTERVENTION_REQUIRED, AlertSeverity.CRITICAL, "manual broker intervention is required", "Review broker terminal and reconcile local state before continuing.", snapshot))
    for incident in incidents:
        if incident.blocks_execution:
            candidates.append(
                _alert(
                    AlertCategory.OPERATIONAL_INCIDENT,
                    AlertSeverity.CRITICAL if incident.severity == BrokerIncidentSeverity.CRITICAL else AlertSeverity.HIGH,
                    incident.reason,
                    incident.recommendation,
                    snapshot,
                    linked_incident_ids=[incident.incident_id],
                    linked_order_ids=[incident.order_id] if incident.order_id else [],
                )
            )
    alerts = [_apply_alert_aging(_dedupe_alert(candidate, previous_alerts, settings), settings) for candidate in candidates]
    for alert in alerts:
        LOGGER.warning(
            "operational alert generated",
            extra={
                "alert_id": alert.alert_id,
                "severity": alert.severity.value,
                "category": alert.category.value,
                "execution_mode": snapshot.mode,
                "broker_adapter": snapshot.broker,
                "operator_action_required": alert.severity in {AlertSeverity.HIGH, AlertSeverity.CRITICAL},
            },
        )
    return alerts


def operator_control_block_reasons(mode: str, controls: OperatorControlState) -> list[str]:
    """Return explicit operator-control reasons that should block broker submission."""

    reasons: list[str] = []
    if controls.maintenance_mode:
        reasons.append("operator maintenance mode is active")
    if not controls.broker_submissions_enabled:
        reasons.append("operator disabled broker submissions")
    if mode == "broker_live" and not controls.live_submissions_enabled:
        reasons.append("operator has not enabled live submissions")
    return reasons


def assess_resume_readiness(
    snapshot: BrokerHealthSnapshot,
    incidents: list[BrokerIncident],
    alerts: list[OperationalAlert],
    anomalies: list[ReconciliationAnomaly],
    controls: OperatorControlState,
    settings: AppSettings,
) -> ResumeReadiness:
    """Assess whether supervised broker operation can safely resume."""

    reasons: list[str] = []
    required_actions: list[str] = []
    reasons.extend(operator_control_block_reasons(snapshot.mode, controls))
    active_alerts = [alert for alert in alerts if alert.status != AlertStatus.RESOLVED]
    unresolved_incidents = [incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]
    severe_incidents = [incident for incident in unresolved_incidents if incident.blocks_execution]
    severe_anomalies = [anomaly for anomaly in anomalies if anomaly.severity in {"high", "critical"}]
    if snapshot.kill_switch_active:
        reasons.append("broker kill switch is active")
        required_actions.append("clear kill switch only after operator review")
    if not snapshot.connected:
        reasons.append("broker is not connected")
        required_actions.append("restore broker connectivity and rerun recovery")
    if snapshot.health_status in {"unavailable", "manual_intervention_required"}:
        reasons.append(f"broker health is {snapshot.health_status}")
    if severe_incidents:
        reasons.append(f"{len(severe_incidents)} severe incident(s) are unresolved")
        required_actions.append("resolve or acknowledge all severe incidents")
    if any(alert.severity in {AlertSeverity.HIGH, AlertSeverity.CRITICAL} and alert.status != AlertStatus.RESOLVED for alert in active_alerts):
        reasons.append("high or critical alerts remain active")
        required_actions.append("inspect alert_summary and incident_report")
    if severe_anomalies:
        reasons.append(f"{len(severe_anomalies)} severe reconciliation anomaly/anomalies are open")
        required_actions.append("run reconciliation and resolve broker/local state mismatch")
    if snapshot.mode == "broker_live" and not (settings.execution_capabilities.broker_live_enabled and settings.broker.live_enabled):
        reasons.append("broker_live config gates are not enabled")
    if reasons:
        status = ResumeReadinessStatus.BLOCKED_PENDING_MANUAL_REVIEW
    elif snapshot.degraded_flags or controls.degraded_mode:
        status = ResumeReadinessStatus.DEGRADED_BUT_SAFE
        if controls.degraded_mode:
            required_actions.append("continue reduced-risk supervised monitoring while degraded mode is active")
    else:
        status = ResumeReadinessStatus.SAFE_TO_RESUME
    return ResumeReadiness(
        status=status,
        checked_at=datetime.now(timezone.utc),
        reasons=_dedupe_strings(reasons),
        required_actions=_dedupe_strings(required_actions),
        broker_mode=snapshot.mode,
        broker=snapshot.broker,
        unresolved_incidents=len(unresolved_incidents),
        active_alerts=len(active_alerts),
        severe_anomalies=len(severe_anomalies),
    )


def build_reliability_summary(
    snapshots: list[BrokerHealthSnapshot],
    metrics: list[OperationalMetric],
    alerts: list[OperationalAlert],
    incidents: list[BrokerIncident],
    orders: list[ExecutionOrder],
    anomalies: list[ReconciliationAnomaly],
) -> ReliabilitySummary:
    """Aggregate long-term reliability metrics from persisted operations history."""

    sample_count = len(snapshots)
    submitted = sum(1 for order in orders if _has_state(order, BrokerOrderState.SUBMITTED))
    acknowledged = sum(1 for order in orders if _has_state(order, BrokerOrderState.ACKNOWLEDGED))
    rejected = sum(1 for order in orders if _has_state(order, BrokerOrderState.REJECTED))
    retry_exhausted = sum(metric.value for metric in metrics if metric.name == "retry_exhausted")
    guardrails = sum(metric.value for metric in metrics if metric.name == "live_guardrail_triggers")
    recovery_events = sum(metric.value for metric in metrics if metric.name == "restart_recovery_events")
    healthy_recoveries = sum(1 for snapshot in snapshots if snapshot.health_status == "healthy")
    return ReliabilitySummary(
        generated_at=datetime.now(timezone.utc),
        samples=sample_count,
        broker_uptime_pct=_pct(sum(1 for snapshot in snapshots if snapshot.connected), sample_count),
        health_success_pct=_pct(sum(1 for snapshot in snapshots if snapshot.health_status == "healthy"), sample_count),
        reconciliation_reliability_pct=_pct(sum(1 for snapshot in snapshots if snapshot.open_reconciliation_anomalies == 0), sample_count),
        account_sync_reliability_pct=_pct(sum(1 for snapshot in snapshots if snapshot.last_successful_account_sync_at is not None), sample_count),
        position_sync_reliability_pct=_pct(sum(1 for snapshot in snapshots if snapshot.last_successful_position_sync_at is not None), sample_count),
        order_submission_success_rate_pct=_pct(acknowledged, submitted),
        rejection_rate_pct=_pct(rejected, submitted),
        retry_exhaustion_rate_pct=_pct(int(retry_exhausted), max(1, len(metrics))),
        incident_rate_per_sample=round(len([incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]) / max(1, sample_count), 4),
        guardrail_trigger_rate_per_sample=round(guardrails / max(1, sample_count), 4),
        recovery_success_pct=_pct(healthy_recoveries, int(recovery_events)),
        mean_time_to_detect_minutes=_mean_time_to_detect_minutes(alerts, incidents),
        mean_time_to_resolve_minutes=_mean_time_to_resolve_minutes(incidents),
    )


def resolve_operational_alerts(
    previous_alerts: list[OperationalAlert],
    current_alerts: list[OperationalAlert],
    *,
    now: datetime | None = None,
) -> list[OperationalAlert]:
    """Resolve active alerts that are absent from the current alert set."""

    timestamp = now or datetime.now(timezone.utc)
    current_keys = {alert.dedupe_key for alert in current_alerts if alert.status != AlertStatus.RESOLVED}
    resolved: list[OperationalAlert] = []
    for alert in previous_alerts:
        if alert.status == AlertStatus.RESOLVED or alert.dedupe_key in current_keys:
            continue
        resolved.append(alert.model_copy(update={"status": AlertStatus.RESOLVED, "updated_at": timestamp, "resolved_at": timestamp}))
    return resolved


def run_startup_recovery(
    settings: AppSettings,
    adapter: object,
    local_orders: list[ExecutionOrder],
) -> BrokerRecoveryResult:
    """Run a safe startup broker sync before new submissions are allowed."""

    account = adapter.query_account_state()
    broker_orders = []
    broker_positions = []
    unreachable_anomaly: ReconciliationAnomaly | None = None
    if account.connected:
        try:
            broker_orders = adapter.broker_order_snapshots()
            broker_positions = adapter.broker_position_snapshots()
        except Exception as exc:
            unreachable_anomaly = ReconciliationAnomaly(
                anomaly_id=str(uuid.uuid4()),
                detected_at=datetime.now(timezone.utc),
                anomaly_type=ReconciliationAnomalyType.BROKER_UNREACHABLE,
                severity="critical",
                reason=str(exc),
            )
    report, updated_orders = reconcile_broker_state(local_orders, broker_orders, broker_positions)
    if unreachable_anomaly is not None:
        report = report.model_copy(update={"anomalies": [*report.anomalies, unreachable_anomaly]})
    snapshot = build_broker_health_snapshot(account, updated_orders, report.anomalies, settings, last_reconciliation_at=report.created_at)
    incidents = classify_broker_incidents(account, updated_orders, report.anomalies, settings)
    events = operational_events_from_snapshot_and_incidents(snapshot, incidents)
    LOGGER.info(
        "broker startup recovery completed",
        extra={
            "execution_mode": settings.execution.mode,
            "broker_adapter": account.broker,
            "reconciliation_status": "blocking" if report.has_blocking_anomalies else "ok",
            "incident_count": len(incidents),
            "operator_action_required": any(incident.blocks_execution for incident in incidents),
        },
    )
    return BrokerRecoveryResult(account_state=account, snapshot=snapshot, incidents=incidents, reconciliation_report=report, updated_orders=updated_orders, events=events)


def _incident_from_anomaly(anomaly: ReconciliationAnomaly, timestamp: datetime) -> BrokerIncident:
    severity = BrokerIncidentSeverity.CRITICAL if anomaly.severity == "critical" else BrokerIncidentSeverity.HIGH if anomaly.severity == "high" else BrokerIncidentSeverity.WARNING
    category = BrokerIncidentCategory.RECONCILIATION_ANOMALY
    if anomaly.anomaly_type == ReconciliationAnomalyType.BROKER_UNREACHABLE:
        category = BrokerIncidentCategory.BROKER_UNAVAILABLE
    elif anomaly.anomaly_type in {ReconciliationAnomalyType.PARTIAL_FILL_DIFFERENCE, ReconciliationAnomalyType.STOP_TARGET_MISMATCH, ReconciliationAnomalyType.MANUAL_BROKER_SIDE_CHANGE}:
        category = BrokerIncidentCategory.PARTIAL_DESYNC
    elif anomaly.anomaly_type == ReconciliationAnomalyType.STALE_BROKER_SNAPSHOT:
        category = BrokerIncidentCategory.STALE_BROKER_STATE
    elif anomaly.anomaly_type in {ReconciliationAnomalyType.BROKER_ORDER_MISSING_INTERNALLY, ReconciliationAnomalyType.BROKER_POSITION_MISSING_INTERNALLY}:
        category = BrokerIncidentCategory.UNKNOWN_BROKER_STATE
    return _incident(
        category,
        severity,
        anomaly.reason,
        _recommendation(category),
        timestamp,
        symbol=anomaly.symbol,
        order_id=anomaly.internal_order_id,
        broker_order_id=anomaly.broker_order_id,
        linked_anomaly_ids=[anomaly.anomaly_id],
        payload={"anomaly_type": anomaly.anomaly_type.value, "anomaly_id": anomaly.anomaly_id},
    )


def _degraded_flags(
    account_state: BrokerAccountState,
    orders: list[ExecutionOrder],
    anomalies: list[ReconciliationAnomaly],
    settings: AppSettings,
    now: datetime,
) -> list[str]:
    flags: list[str] = []
    if not account_state.connected:
        flags.append("broker_unavailable")
    if account_state.connected and not account_state.can_trade:
        flags.append("account_not_tradable")
    if account_state.retrieved_at and _account_age_seconds(account_state.retrieved_at, now) > settings.broker_safety.max_account_state_age_seconds:
        flags.append("account_state_stale")
    if account_state.consecutive_failures > settings.broker_safety.max_connectivity_failures:
        flags.append("connectivity_unstable")
    if any(anomaly.severity in {"high", "critical"} for anomaly in anomalies):
        flags.append("blocking_reconciliation_anomaly")
    if os.getenv(settings.broker.kill_switch_env, "").strip().lower() in {"1", "true", "yes", "on"}:
        flags.append("kill_switch_active")
    if any(_has_state(order, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED) for order in orders):
        flags.append("manual_intervention_required")
    if any(order.is_open for order in orders):
        flags.append("unfinished_local_broker_state")
    return sorted(set(flags))


def _health_status(account_state: BrokerAccountState, flags: list[str], manual_intervention: bool) -> str:
    if manual_intervention:
        return "manual_intervention_required"
    if not account_state.connected:
        return "unavailable"
    if flags:
        return "degraded"
    if account_state.can_trade:
        return "healthy"
    return "not_tradable"


def _incident(
    category: BrokerIncidentCategory,
    severity: BrokerIncidentSeverity,
    reason: str,
    recommendation: str,
    opened_at: datetime,
    *,
    symbol: str | None = None,
    order_id: str | None = None,
    broker_order_id: str | None = None,
    linked_anomaly_ids: list[str] | None = None,
    payload: dict[str, str | float | int | bool | None] | None = None,
) -> BrokerIncident:
    return BrokerIncident(
        incident_id=str(uuid.uuid4()),
        opened_at=opened_at,
        updated_at=opened_at,
        category=category,
        severity=severity,
        reason=reason,
        recommendation=recommendation,
        symbol=symbol,
        order_id=order_id,
        broker_order_id=broker_order_id,
        linked_anomaly_ids=linked_anomaly_ids or [],
        payload=payload or {},
    )


def _recommendation(category: BrokerIncidentCategory) -> str:
    recommendations = {
        BrokerIncidentCategory.BROKER_UNAVAILABLE: "Restore broker connectivity and rerun broker_check before submitting.",
        BrokerIncidentCategory.MT5_TERMINAL_NOT_REACHABLE: "Open MT5, verify login/session, then rerun broker_check.",
        BrokerIncidentCategory.ACCOUNT_STATE_STALE: "Refresh account state and block submissions until the snapshot is fresh.",
        BrokerIncidentCategory.REPEATED_SUBMIT_FAILURES: "Review broker terminal state manually before retrying any order.",
        BrokerIncidentCategory.RECONCILIATION_ANOMALY: "Run startup recovery and inspect the reconciliation report.",
        BrokerIncidentCategory.REPEATED_REJECTS: "Inspect broker rejection reasons and symbol/volume constraints.",
        BrokerIncidentCategory.PARTIAL_DESYNC: "Reconcile stops, targets, fills, and manual broker-side changes.",
        BrokerIncidentCategory.RESTART_UNFINISHED_STATE: "Verify open/pending broker state after restart before new submissions.",
        BrokerIncidentCategory.UNKNOWN_BROKER_STATE: "Identify broker-side state that is not tracked locally before proceeding.",
        BrokerIncidentCategory.CONNECTIVITY_UNSTABLE: "Pause broker submissions until connectivity stabilizes.",
        BrokerIncidentCategory.LIVE_GUARDRAIL_BLOCK: "Review the guardrail reason before operator override.",
        BrokerIncidentCategory.STALE_BROKER_STATE: "Refresh broker orders and positions before relying on local state.",
        BrokerIncidentCategory.MANUAL_INTERVENTION_REQUIRED: "Manually verify terminal/broker state and reconcile local records.",
    }
    return recommendations[category]


def _recent_reject_streak(orders: list[ExecutionOrder]) -> int:
    streak = 0
    for order in sorted(orders, key=lambda item: item.created_at, reverse=True):
        if _has_state(order, BrokerOrderState.REJECTED):
            streak += 1
            continue
        break
    return streak


def _incident_metrics(snapshot: BrokerHealthSnapshot, incidents: list[BrokerIncident]) -> list[OperationalMetric]:
    counts: dict[str, int] = {}
    for incident in incidents:
        counts[incident.category.value] = counts.get(incident.category.value, 0) + 1
    return [
        OperationalMetric(
            metric_id=str(uuid.uuid4()),
            recorded_at=snapshot.created_at,
            name="incident_count",
            value=float(count),
            status=snapshot.health_status,
            broker=snapshot.broker,
            mode=snapshot.mode,
            dimensions={"category": category},
            payload={"snapshot_id": snapshot.snapshot_id},
        )
        for category, count in sorted(counts.items())
    ]


def _alert(
    category: AlertCategory,
    severity: AlertSeverity,
    reason: str,
    recommendation: str,
    snapshot: BrokerHealthSnapshot,
    *,
    linked_incident_ids: list[str] | None = None,
    linked_anomaly_ids: list[str] | None = None,
    linked_order_ids: list[str] | None = None,
) -> OperationalAlert:
    dedupe_key = f"{snapshot.mode}:{snapshot.broker}:{category.value}"
    return OperationalAlert(
        alert_id=str(uuid.uuid4()),
        category=category,
        severity=severity,
        status=AlertStatus.ACTIVE,
        opened_at=snapshot.created_at,
        updated_at=snapshot.created_at,
        reason=reason,
        recommendation=recommendation,
        dedupe_key=dedupe_key,
        linked_incident_ids=linked_incident_ids or [],
        linked_anomaly_ids=linked_anomaly_ids or [],
        linked_order_ids=linked_order_ids or [],
        payload={"snapshot_id": snapshot.snapshot_id, "health_status": snapshot.health_status},
    )


def _dedupe_alert(alert: OperationalAlert, previous_alerts: list[OperationalAlert], settings: AppSettings) -> OperationalAlert:
    latest = next((item for item in reversed(previous_alerts) if item.dedupe_key == alert.dedupe_key and item.status != AlertStatus.RESOLVED), None)
    suppression_delta = _alert_suppression_delta(settings)
    if latest is None:
        return alert.model_copy(update={"suppression_until": alert.opened_at + suppression_delta})
    suppression_until = latest.suppression_until or latest.opened_at + suppression_delta
    base_update = {
        "alert_id": latest.alert_id,
        "opened_at": latest.opened_at,
        "updated_at": alert.opened_at,
        "suppression_until": suppression_until,
        "linked_incident_ids": _dedupe_strings([*latest.linked_incident_ids, *alert.linked_incident_ids]),
        "linked_anomaly_ids": _dedupe_strings([*latest.linked_anomaly_ids, *alert.linked_anomaly_ids]),
        "linked_order_ids": _dedupe_strings([*latest.linked_order_ids, *alert.linked_order_ids]),
    }
    if alert.opened_at <= suppression_until:
        return alert.model_copy(update={**base_update, "status": AlertStatus.SUPPRESSED})
    return alert.model_copy(update={**base_update, "status": AlertStatus.ACTIVE, "suppression_until": alert.opened_at + suppression_delta})


def _apply_alert_aging(alert: OperationalAlert, settings: AppSettings) -> OperationalAlert:
    age_minutes = max(0.0, ((alert.updated_at or alert.opened_at) - alert.opened_at).total_seconds() / 60.0)
    if alert.status == AlertStatus.RESOLVED:
        return alert
    if age_minutes >= settings.monitoring.alert_critical_age_minutes and alert.severity != AlertSeverity.CRITICAL:
        return alert.model_copy(update={"severity": AlertSeverity.CRITICAL, "reason": f"{alert.reason}; escalated after {age_minutes:.1f} minutes unresolved"})
    if age_minutes >= settings.monitoring.alert_escalation_minutes and alert.severity == AlertSeverity.WARNING:
        return alert.model_copy(update={"severity": AlertSeverity.HIGH, "reason": f"{alert.reason}; escalated after {age_minutes:.1f} minutes unresolved"})
    return alert


def _alert_suppression_delta(settings: AppSettings) -> timedelta:
    return timedelta(minutes=settings.monitoring.alert_suppression_minutes)


def _reconciliation_age_seconds(snapshot: BrokerHealthSnapshot, now: datetime) -> float:
    if snapshot.last_successful_reconciliation_at is None:
        return float("inf")
    normalized = snapshot.last_successful_reconciliation_at if snapshot.last_successful_reconciliation_at.tzinfo else snapshot.last_successful_reconciliation_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - normalized).total_seconds())


def _last_broker_action_times(orders: list[ExecutionOrder]) -> tuple[datetime | None, datetime | None]:
    success_states = {BrokerOrderState.SUBMITTED, BrokerOrderState.ACKNOWLEDGED, BrokerOrderState.FILLED, BrokerOrderState.MODIFIED, BrokerOrderState.CANCELLED, BrokerOrderState.CLOSED}
    failure_states = {BrokerOrderState.REJECTED, BrokerOrderState.RETRY_EXHAUSTED, BrokerOrderState.BROKER_UNREACHABLE, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED, BrokerOrderState.RECONCILIATION_MISMATCH}
    success_times: list[datetime] = []
    failure_times: list[datetime] = []
    for order in orders:
        for transition in order.broker_transitions:
            if transition.state in success_states:
                success_times.append(transition.occurred_at)
            if transition.state in failure_states:
                failure_times.append(transition.occurred_at)
    return (max(success_times) if success_times else None, max(failure_times) if failure_times else None)


def _pct(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator) * 100.0, 2)


def _mean_time_to_detect_minutes(alerts: list[OperationalAlert], incidents: list[BrokerIncident]) -> float | None:
    deltas: list[float] = []
    alerts_by_incident = {incident_id: alert for alert in alerts for incident_id in alert.linked_incident_ids}
    for incident in incidents:
        alert = alerts_by_incident.get(incident.incident_id)
        if alert is None:
            continue
        deltas.append(max(0.0, (alert.opened_at - incident.opened_at).total_seconds() / 60.0))
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 2)


def _mean_time_to_resolve_minutes(incidents: list[BrokerIncident]) -> float | None:
    deltas = [
        max(0.0, ((incident.resolved_at or incident.closed_at) - incident.opened_at).total_seconds() / 60.0)
        for incident in incidents
        if incident.resolved_at or incident.closed_at
    ]
    if not deltas:
        return None
    return round(sum(deltas) / len(deltas), 2)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _has_state(order: ExecutionOrder, state: BrokerOrderState) -> bool:
    return any(transition.state == state for transition in order.broker_transitions)


def _account_age_seconds(retrieved_at: datetime, now: datetime) -> float:
    normalized = retrieved_at if retrieved_at.tzinfo else retrieved_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - normalized).total_seconds())


def _incident_key(incident: BrokerIncident) -> tuple[str, str | None, str | None, str | None]:
    return (incident.category.value, incident.symbol, incident.order_id, incident.broker_order_id)
