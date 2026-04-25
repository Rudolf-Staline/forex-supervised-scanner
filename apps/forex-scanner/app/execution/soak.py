"""Long-duration supervised soak validation models and metrics."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.config.settings import AppSettings
from app.execution.operations import (
    AlertStatus,
    BrokerHealthSnapshot,
    BrokerIncident,
    BrokerIncidentSeverity,
    BrokerIncidentStatus,
    OperationalAlert,
    OperationalMetric,
    OperatorControlState,
    ResumeReadiness,
)
from app.execution.reconciliation import ReconciliationAnomaly


class SoakRunStatus(str, Enum):
    """Lifecycle for a persisted soak-validation run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SoakReadinessResult(str, Enum):
    """Operator-facing soak readiness result."""

    PASS = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL = "fail"


class SoakCampaignStatus(str, Enum):
    """Lifecycle state for a multi-session soak campaign."""

    RUNNING = "running"
    STOPPED = "stopped"
    FINALIZED = "finalized"
    FAILED = "failed"


class SoakCampaignReadiness(str, Enum):
    """Conservative campaign-level supervised-readiness rating."""

    NOT_READY = "not_ready"
    LIMITED_READY = "limited_ready"
    SUPERVISED_READY = "supervised_ready"


class SoakAnomalyCategory(str, Enum):
    """Anomaly categories detected over extended supervised runs."""

    HEALTH_FLAP = "health_flap"
    BROKER_DISCONNECT = "broker_disconnect"
    STALE_ACCOUNT_STATE = "stale_account_state"
    STALE_RECONCILIATION = "stale_reconciliation"
    BROKER_REJECT = "broker_reject"
    UNRESOLVED_ALERT = "unresolved_alert"
    UNRESOLVED_INCIDENT = "unresolved_incident"
    MANUAL_INTERVENTION = "manual_intervention"
    GUARDRAIL_TRIGGER = "guardrail_trigger"
    RETRY_EXHAUSTED = "retry_exhausted"
    DEGRADED_PERIOD = "degraded_period"


class SoakAnomaly(BaseModel):
    """Aggregated anomaly discovered during a soak run."""

    anomaly_id: str
    run_id: str
    detected_at: datetime
    category: SoakAnomalyCategory
    severity: str
    sample_index: int | None = None
    count: int = Field(default=1, ge=1)
    reason: str
    recommendation: str
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class SoakCampaign(BaseModel):
    """Multi-session soak campaign metadata and aggregate state."""

    campaign_id: str
    name: str
    started_at: datetime
    ended_at: datetime | None = None
    target_duration_seconds: float = Field(gt=0.0)
    mode: str
    broker: str
    status: SoakCampaignStatus
    run_ids: list[str] = Field(default_factory=list)
    readiness: SoakCampaignReadiness | None = None
    samples: int = Field(default=0, ge=0)
    operator_notes: str | None = None
    summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class SoakRun(BaseModel):
    """Persisted top-level soak run metadata."""

    run_id: str
    started_at: datetime
    ended_at: datetime | None = None
    mode: str
    broker: str
    status: SoakRunStatus
    duration_seconds: float = Field(ge=0.0)
    interval_seconds: float = Field(ge=0.0)
    readiness: SoakReadinessResult | None = None
    samples: int = Field(default=0, ge=0)
    summary: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class SoakSample(BaseModel):
    """One timestamped operational sample in a soak run."""

    sample_id: str
    run_id: str
    sample_index: int = Field(ge=1)
    sampled_at: datetime
    mode: str
    broker: str
    connected: bool
    can_trade: bool
    health_status: str
    account_sync_fresh: bool
    position_sync_fresh: bool
    reconciliation_fresh: bool
    open_incidents: int = Field(ge=0)
    blocking_incidents: int = Field(ge=0)
    active_alerts: int = Field(ge=0)
    resolved_alerts: int = Field(ge=0)
    retry_exhausted: int = Field(ge=0)
    broker_rejects: int = Field(ge=0)
    stale_state_detections: int = Field(ge=0)
    live_guardrail_triggers: int = Field(ge=0)
    manual_intervention_required: int = Field(ge=0)
    reconciliation_anomalies: int = Field(ge=0)
    blocking_reconciliation_anomalies: int = Field(ge=0)
    degraded_mode: bool
    degraded_flags: list[str] = Field(default_factory=list)
    kill_switch_active: bool
    recovery_invoked: bool
    resume_readiness: str | None = None
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class SoakReliabilityMetrics(BaseModel):
    """Aggregated reliability metrics for one soak run."""

    run_id: str
    generated_at: datetime
    samples: int = Field(ge=0)
    connectivity_success_rate_pct: float
    reconciliation_success_rate_pct: float
    account_sync_success_rate_pct: float
    position_sync_success_rate_pct: float
    retry_exhausted_count: int = Field(ge=0)
    retry_exhausted_rate_pct: float
    broker_reject_count: int = Field(ge=0)
    stale_state_detection_count: int = Field(ge=0)
    manual_intervention_count: int = Field(ge=0)
    live_guardrail_trigger_count: int = Field(ge=0)
    health_flap_count: int = Field(ge=0)
    broker_unavailable_pct: float
    degraded_mode_pct: float
    healthy_state_pct: float
    mean_unhealthy_interval_seconds: float | None = None
    mean_recovery_seconds: float | None = None
    unresolved_incidents_end: int = Field(ge=0)
    unresolved_severe_incidents_end: int = Field(ge=0)
    incident_count_by_category: dict[str, int] = Field(default_factory=dict)
    alert_count_by_category: dict[str, int] = Field(default_factory=dict)


class SoakReadinessAssessment(BaseModel):
    """Conservative readiness result for a completed soak run."""

    run_id: str
    assessed_at: datetime
    result: SoakReadinessResult
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: SoakReliabilityMetrics


class SoakCampaignReliabilityMetrics(BaseModel):
    """Aggregate operational metrics across a multi-session soak campaign."""

    campaign_id: str
    generated_at: datetime
    target_duration_seconds: float
    observed_duration_seconds: float
    run_count: int = Field(ge=0)
    samples: int = Field(ge=0)
    health_samples: int = Field(ge=0)
    healthy_samples: int = Field(ge=0)
    degraded_samples: int = Field(ge=0)
    broker_unavailable_samples: int = Field(ge=0)
    account_stale_samples: int = Field(ge=0)
    position_stale_samples: int = Field(ge=0)
    reconciliation_failure_samples: int = Field(ge=0)
    health_success_pct: float
    degraded_mode_pct: float
    broker_unavailable_pct: float
    account_sync_success_pct: float
    position_sync_success_pct: float
    reconciliation_success_pct: float
    retry_exhausted_count: int = Field(ge=0)
    broker_reject_count: int = Field(ge=0)
    stale_state_detection_count: int = Field(ge=0)
    live_guardrail_trigger_count: int = Field(ge=0)
    manual_intervention_count: int = Field(ge=0)
    restart_recovery_events: int = Field(ge=0)
    alert_count_by_severity: dict[str, int] = Field(default_factory=dict)
    alert_count_by_category: dict[str, int] = Field(default_factory=dict)
    incident_count_by_severity: dict[str, int] = Field(default_factory=dict)
    incident_count_by_category: dict[str, int] = Field(default_factory=dict)
    unresolved_alerts_end: int = Field(ge=0)
    unresolved_incidents_end: int = Field(ge=0)
    unresolved_severe_incidents_end: int = Field(ge=0)
    downtime_intervals: int = Field(ge=0)
    stale_sync_intervals: int = Field(ge=0)
    reconciliation_failure_intervals: int = Field(ge=0)
    alert_burden_per_day: float
    incident_burden_per_day: float


class SoakCampaignRecurringIssue(BaseModel):
    """Recurring or clustered operational issue across a campaign."""

    category: str
    count: int = Field(ge=1)
    severity: str
    first_seen: datetime
    last_seen: datetime
    trend: str
    cluster: str
    recommendation: str


class SoakCampaignReadinessAssessment(BaseModel):
    """Conservative operator-facing readiness recommendation for a campaign."""

    campaign_id: str
    assessed_at: datetime
    rating: SoakCampaignReadiness
    key_reasons: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)
    suggested_rerun_duration_hours: float | None = None
    metrics: SoakCampaignReliabilityMetrics


def validate_soak_mode(mode: str, settings: AppSettings, *, allow_live: bool = False) -> None:
    """Fail closed when a soak mode is not explicitly permitted."""

    if mode not in settings.soak.allowed_modes:
        raise ValueError(f"soak mode {mode!r} is not allowed by config")
    if mode == "broker_live":
        if not allow_live:
            raise ValueError("broker_live soak checks require --allow-live")
        if not settings.soak.allow_broker_live_checks:
            raise ValueError("broker_live soak checks require soak.allow_broker_live_checks=true")
        if not settings.execution_capabilities.broker_live_enabled or not settings.broker.live_enabled:
            raise ValueError("broker_live soak checks require live capability and broker.live_enabled gates")


def create_soak_run(mode: str, broker: str, duration_seconds: float, interval_seconds: float) -> SoakRun:
    """Create a running soak-run record."""

    return SoakRun(
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
        mode=mode,
        broker=broker,
        status=SoakRunStatus.RUNNING,
        duration_seconds=duration_seconds,
        interval_seconds=interval_seconds,
    )


def create_soak_campaign(
    name: str,
    mode: str,
    broker: str,
    target_duration_seconds: float,
    *,
    now: datetime | None = None,
    campaign_id: str | None = None,
    operator_notes: str | None = None,
) -> SoakCampaign:
    """Create a running multi-session soak campaign."""

    if not name.strip():
        raise ValueError("campaign name cannot be empty")
    return SoakCampaign(
        campaign_id=campaign_id or str(uuid.uuid4()),
        name=name.strip(),
        started_at=now or datetime.now(timezone.utc),
        target_duration_seconds=target_duration_seconds,
        mode=mode,
        broker=broker,
        status=SoakCampaignStatus.RUNNING,
        operator_notes=operator_notes,
    )


def attach_run_to_campaign(campaign: SoakCampaign, run: SoakRun) -> SoakCampaign:
    """Return campaign state with a soak run attached once."""

    run_ids = [*campaign.run_ids]
    if run.run_id not in run_ids:
        run_ids.append(run.run_id)
    return campaign.model_copy(update={"run_ids": run_ids})


def stop_soak_campaign(campaign: SoakCampaign, *, now: datetime | None = None, reason: str | None = None) -> SoakCampaign:
    """Stop a campaign without assigning final readiness."""

    summary = dict(campaign.summary)
    if reason:
        summary["stop_reason"] = reason
    return campaign.model_copy(update={"status": SoakCampaignStatus.STOPPED, "ended_at": now or datetime.now(timezone.utc), "summary": summary})


def build_soak_sample(
    run_id: str,
    sample_index: int,
    snapshot: BrokerHealthSnapshot,
    incidents: list[BrokerIncident],
    alerts: list[OperationalAlert],
    metrics: list[OperationalMetric],
    anomalies: list[ReconciliationAnomaly],
    controls: OperatorControlState,
    readiness: ResumeReadiness | None,
    *,
    recovery_invoked: bool = True,
) -> SoakSample:
    """Build one compact soak sample from persisted operational primitives."""

    open_incidents = [incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]
    active_alerts = [alert for alert in alerts if alert.status == AlertStatus.ACTIVE]
    resolved_alerts = [alert for alert in alerts if alert.status == AlertStatus.RESOLVED]
    return SoakSample(
        sample_id=str(uuid.uuid4()),
        run_id=run_id,
        sample_index=sample_index,
        sampled_at=snapshot.created_at,
        mode=snapshot.mode,
        broker=snapshot.broker,
        connected=snapshot.connected,
        can_trade=snapshot.can_trade,
        health_status=snapshot.health_status,
        account_sync_fresh=snapshot.last_successful_account_sync_at is not None,
        position_sync_fresh=snapshot.last_successful_position_sync_at is not None,
        reconciliation_fresh=snapshot.last_successful_reconciliation_at is not None,
        open_incidents=len(open_incidents),
        blocking_incidents=sum(1 for incident in open_incidents if incident.blocks_execution),
        active_alerts=len(active_alerts),
        resolved_alerts=len(resolved_alerts),
        retry_exhausted=int(_metric_value(metrics, "retry_exhausted")),
        broker_rejects=int(_metric_value(metrics, "broker_rejects")),
        stale_state_detections=int(_metric_value(metrics, "stale_state_detections")),
        live_guardrail_triggers=int(_metric_value(metrics, "live_guardrail_triggers")),
        manual_intervention_required=int(_metric_value(metrics, "manual_intervention_required")),
        reconciliation_anomalies=len(anomalies),
        blocking_reconciliation_anomalies=sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"}),
        degraded_mode=controls.degraded_mode or snapshot.health_status in {"degraded", "unavailable", "manual_intervention_required"} or bool(snapshot.degraded_flags),
        degraded_flags=snapshot.degraded_flags,
        kill_switch_active=snapshot.kill_switch_active,
        recovery_invoked=recovery_invoked,
        resume_readiness=readiness.status.value if readiness else None,
        payload={
            "snapshot_id": snapshot.snapshot_id,
            "operator_maintenance": controls.maintenance_mode,
            "broker_submissions_enabled": controls.broker_submissions_enabled,
        },
    )


def compute_soak_reliability(
    run_id: str,
    samples: list[SoakSample],
    incidents: list[BrokerIncident],
    alerts: list[OperationalAlert],
) -> SoakReliabilityMetrics:
    """Compute long-run reliability metrics from soak samples."""

    sample_count = len(samples)
    health_flaps = _health_flaps(samples)
    unhealthy_intervals = _interval_durations(samples, lambda sample: sample.health_status != "healthy")
    recovery_intervals = _recovery_durations(samples)
    unresolved = [incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]
    severe = [incident for incident in unresolved if incident.severity in {BrokerIncidentSeverity.HIGH, BrokerIncidentSeverity.CRITICAL}]
    return SoakReliabilityMetrics(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc),
        samples=sample_count,
        connectivity_success_rate_pct=_pct(sum(1 for sample in samples if sample.connected), sample_count),
        reconciliation_success_rate_pct=_pct(sum(1 for sample in samples if sample.reconciliation_anomalies == 0 and sample.reconciliation_fresh), sample_count),
        account_sync_success_rate_pct=_pct(sum(1 for sample in samples if sample.account_sync_fresh), sample_count),
        position_sync_success_rate_pct=_pct(sum(1 for sample in samples if sample.position_sync_fresh), sample_count),
        retry_exhausted_count=sum(sample.retry_exhausted for sample in samples),
        retry_exhausted_rate_pct=_pct(sum(1 for sample in samples if sample.retry_exhausted > 0), sample_count),
        broker_reject_count=sum(sample.broker_rejects for sample in samples),
        stale_state_detection_count=sum(sample.stale_state_detections for sample in samples),
        manual_intervention_count=sum(sample.manual_intervention_required for sample in samples),
        live_guardrail_trigger_count=sum(sample.live_guardrail_triggers for sample in samples),
        health_flap_count=health_flaps,
        broker_unavailable_pct=_pct(sum(1 for sample in samples if not sample.connected), sample_count),
        degraded_mode_pct=_pct(sum(1 for sample in samples if sample.degraded_mode), sample_count),
        healthy_state_pct=_pct(sum(1 for sample in samples if sample.health_status == "healthy"), sample_count),
        mean_unhealthy_interval_seconds=_mean(unhealthy_intervals),
        mean_recovery_seconds=_mean(recovery_intervals),
        unresolved_incidents_end=len(unresolved),
        unresolved_severe_incidents_end=len(severe),
        incident_count_by_category=_count_by_category(incidents),
        alert_count_by_category=_count_by_category(alerts),
    )


def detect_soak_anomalies(
    run_id: str,
    samples: list[SoakSample],
    incidents: list[BrokerIncident],
    alerts: list[OperationalAlert],
    metrics: SoakReliabilityMetrics,
    settings: AppSettings,
) -> list[SoakAnomaly]:
    """Detect operator-relevant anomalies from a completed soak run."""

    detected_at = datetime.now(timezone.utc)
    anomalies: list[SoakAnomaly] = []
    repeated = settings.soak.repeated_anomaly_sample_threshold
    if metrics.health_flap_count >= settings.soak.warning_max_health_flaps:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.HEALTH_FLAP, "high" if metrics.health_flap_count >= settings.soak.fail_max_health_flaps else "warning", metrics.health_flap_count, "broker health changed state repeatedly", "Inspect connectivity, terminal logs, and recovery report before continuing."))
    disconnected = sum(1 for sample in samples if not sample.connected)
    if disconnected >= repeated:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.BROKER_DISCONNECT, "high", disconnected, "broker was disconnected in repeated samples", "Do not resume broker submissions until connectivity remains stable."))
    stale_account = sum(1 for sample in samples if not sample.account_sync_fresh or "account_state_stale" in sample.degraded_flags)
    if stale_account >= repeated:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.STALE_ACCOUNT_STATE, "high", stale_account, "account state was stale or unavailable repeatedly", "Run recovery and verify account sync freshness before submissions."))
    stale_reconciliation = sum(1 for sample in samples if not sample.reconciliation_fresh)
    if stale_reconciliation >= repeated:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.STALE_RECONCILIATION, "warning", stale_reconciliation, "reconciliation freshness was missing repeatedly", "Run reconciliation and inspect anomaly reports."))
    if metrics.broker_reject_count > 0:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.BROKER_REJECT, "high", metrics.broker_reject_count, "broker rejects occurred during soak validation", "Inspect rejection report and symbol/volume constraints."))
    if metrics.retry_exhausted_count > 0:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.RETRY_EXHAUSTED, "high", metrics.retry_exhausted_count, "one or more broker operations exhausted retries", "Verify broker state manually before retrying."))
    if metrics.manual_intervention_count > 0:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.MANUAL_INTERVENTION, "critical", metrics.manual_intervention_count, "manual intervention was required during soak validation", "Stop broker submissions until the operator reconciles broker and local state."))
    if metrics.live_guardrail_trigger_count > 0:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.GUARDRAIL_TRIGGER, "warning", metrics.live_guardrail_trigger_count, "live guardrails triggered during soak validation", "Review guardrail reasons before resuming supervised broker work."))
    if metrics.degraded_mode_pct >= settings.soak.warning_max_degraded_pct:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.DEGRADED_PERIOD, "warning", int(metrics.degraded_mode_pct), "soak spent too much time degraded", "Continue observation or resolve degraded flags before live-supervised use."))
    stale_incidents = [
        incident
        for incident in incidents
        if incident.status == BrokerIncidentStatus.OPEN
        and (detected_at - (incident.opened_at if incident.opened_at.tzinfo else incident.opened_at.replace(tzinfo=timezone.utc))).total_seconds() / 60.0
        >= settings.soak.unresolved_incident_minutes_warning
    ]
    if stale_incidents:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.UNRESOLVED_INCIDENT, "high", len(stale_incidents), "incidents remained unresolved beyond the configured warning window", "Resolve or acknowledge incidents before any resume-live decision."))
    active_alerts = sum(1 for alert in alerts if alert.status == AlertStatus.ACTIVE)
    if active_alerts >= repeated:
        anomalies.append(_soak_anomaly(run_id, detected_at, SoakAnomalyCategory.UNRESOLVED_ALERT, "warning", active_alerts, "active alerts persisted during soak validation", "Review alert_summary and resolve operational alerts before increasing broker exposure."))
    return anomalies


def assess_soak_readiness(
    run_id: str,
    metrics: SoakReliabilityMetrics,
    anomalies: list[SoakAnomaly],
    settings: AppSettings,
) -> SoakReadinessAssessment:
    """Classify soak readiness as PASS, PASS_WITH_WARNINGS, or FAIL."""

    fail_reasons: list[str] = []
    warnings: list[str] = []
    if metrics.unresolved_severe_incidents_end > settings.soak.fail_max_unresolved_severe_incidents:
        fail_reasons.append("unresolved high/critical incidents remain at end of run")
    if metrics.broker_unavailable_pct > settings.soak.fail_max_broker_unavailable_pct:
        fail_reasons.append(f"broker unavailable {metrics.broker_unavailable_pct}% exceeds fail threshold")
    elif metrics.broker_unavailable_pct > settings.soak.warning_max_broker_unavailable_pct:
        warnings.append(f"broker unavailable {metrics.broker_unavailable_pct}% exceeds warning threshold")
    reconciliation_failure_pct = round(100.0 - metrics.reconciliation_success_rate_pct, 2)
    if reconciliation_failure_pct > settings.soak.fail_max_reconciliation_failure_pct:
        fail_reasons.append(f"reconciliation failure {reconciliation_failure_pct}% exceeds fail threshold")
    elif reconciliation_failure_pct > settings.soak.warning_max_reconciliation_failure_pct:
        warnings.append(f"reconciliation failure {reconciliation_failure_pct}% exceeds warning threshold")
    if metrics.retry_exhausted_count > settings.soak.fail_max_retry_exhausted_count:
        fail_reasons.append("retry exhaustion occurred")
    if metrics.stale_state_detection_count > settings.soak.fail_max_stale_state_detections:
        fail_reasons.append("stale state detections exceeded fail threshold")
    if metrics.manual_intervention_count > settings.soak.fail_max_manual_intervention_count:
        fail_reasons.append("manual intervention was required")
    total_incidents = sum(metrics.incident_count_by_category.values())
    if total_incidents > settings.soak.fail_max_total_incidents:
        fail_reasons.append("incident count exceeded fail threshold")
    elif total_incidents > settings.soak.warning_max_total_incidents:
        warnings.append("incident count exceeded warning threshold")
    if metrics.health_flap_count >= settings.soak.fail_max_health_flaps:
        fail_reasons.append("health flap count exceeded fail threshold")
    elif metrics.health_flap_count >= settings.soak.warning_max_health_flaps:
        warnings.append("health flap count exceeded warning threshold")
    if any(anomaly.severity == "critical" for anomaly in anomalies):
        fail_reasons.append("critical soak anomaly detected")
    elif anomalies:
        warnings.append("soak anomalies require operator review")
    result = SoakReadinessResult.FAIL if fail_reasons else SoakReadinessResult.PASS_WITH_WARNINGS if warnings else SoakReadinessResult.PASS
    return SoakReadinessAssessment(
        run_id=run_id,
        assessed_at=datetime.now(timezone.utc),
        result=result,
        reasons=_dedupe(fail_reasons),
        warnings=_dedupe(warnings),
        metrics=metrics,
    )


def complete_soak_run(run: SoakRun, assessment: SoakReadinessAssessment, samples: list[SoakSample], *, status: SoakRunStatus = SoakRunStatus.COMPLETED) -> SoakRun:
    """Return a finished soak run carrying readiness and compact summary fields."""

    return run.model_copy(
        update={
            "ended_at": datetime.now(timezone.utc),
            "status": status,
            "readiness": assessment.result,
            "samples": len(samples),
            "summary": {
                "connectivity_success_rate_pct": assessment.metrics.connectivity_success_rate_pct,
                "reconciliation_success_rate_pct": assessment.metrics.reconciliation_success_rate_pct,
                "healthy_state_pct": assessment.metrics.healthy_state_pct,
                "unresolved_incidents_end": assessment.metrics.unresolved_incidents_end,
                "readiness": assessment.result.value,
            },
        }
    )


def aggregate_campaign_reliability(
    campaign: SoakCampaign,
    runs: list[SoakRun],
    samples: list[SoakSample],
    incidents: list[BrokerIncident],
    alerts: list[OperationalAlert],
    metrics: list[OperationalMetric],
) -> SoakCampaignReliabilityMetrics:
    """Aggregate campaign-level reliability across attached runs and samples."""

    sorted_samples = sorted(samples, key=lambda sample: sample.sampled_at)
    sample_count = len(sorted_samples)
    observed_duration = _campaign_observed_seconds(campaign, sorted_samples)
    unresolved_alerts = [alert for alert in alerts if alert.status != AlertStatus.RESOLVED]
    unresolved_incidents = [incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]
    severe_unresolved = [
        incident
        for incident in unresolved_incidents
        if incident.severity in {BrokerIncidentSeverity.HIGH, BrokerIncidentSeverity.CRITICAL}
    ]
    retry_exhausted = sum(sample.retry_exhausted for sample in sorted_samples)
    broker_rejects = sum(sample.broker_rejects for sample in sorted_samples)
    stale_state = sum(sample.stale_state_detections for sample in sorted_samples)
    guardrails = sum(sample.live_guardrail_triggers for sample in sorted_samples)
    manual = sum(sample.manual_intervention_required for sample in sorted_samples)
    restart_events = int(sum(metric.value for metric in metrics if metric.name == "restart_recovery_events"))
    if restart_events == 0:
        restart_events = sum(1 for sample in sorted_samples if sample.recovery_invoked)
    return SoakCampaignReliabilityMetrics(
        campaign_id=campaign.campaign_id,
        generated_at=datetime.now(timezone.utc),
        target_duration_seconds=campaign.target_duration_seconds,
        observed_duration_seconds=observed_duration,
        run_count=len(runs),
        samples=sample_count,
        health_samples=sample_count,
        healthy_samples=sum(1 for sample in sorted_samples if sample.health_status == "healthy"),
        degraded_samples=sum(1 for sample in sorted_samples if sample.degraded_mode),
        broker_unavailable_samples=sum(1 for sample in sorted_samples if not sample.connected),
        account_stale_samples=sum(1 for sample in sorted_samples if not sample.account_sync_fresh),
        position_stale_samples=sum(1 for sample in sorted_samples if not sample.position_sync_fresh),
        reconciliation_failure_samples=sum(1 for sample in sorted_samples if not sample.reconciliation_fresh or sample.reconciliation_anomalies > 0),
        health_success_pct=_pct(sum(1 for sample in sorted_samples if sample.health_status == "healthy"), sample_count),
        degraded_mode_pct=_pct(sum(1 for sample in sorted_samples if sample.degraded_mode), sample_count),
        broker_unavailable_pct=_pct(sum(1 for sample in sorted_samples if not sample.connected), sample_count),
        account_sync_success_pct=_pct(sum(1 for sample in sorted_samples if sample.account_sync_fresh), sample_count),
        position_sync_success_pct=_pct(sum(1 for sample in sorted_samples if sample.position_sync_fresh), sample_count),
        reconciliation_success_pct=_pct(sum(1 for sample in sorted_samples if sample.reconciliation_fresh and sample.reconciliation_anomalies == 0), sample_count),
        retry_exhausted_count=retry_exhausted,
        broker_reject_count=broker_rejects,
        stale_state_detection_count=stale_state,
        live_guardrail_trigger_count=guardrails,
        manual_intervention_count=manual,
        restart_recovery_events=restart_events,
        alert_count_by_severity=_count_by_attr(alerts, "severity"),
        alert_count_by_category=_count_by_category(alerts),
        incident_count_by_severity=_count_by_attr(incidents, "severity"),
        incident_count_by_category=_count_by_category(incidents),
        unresolved_alerts_end=len(unresolved_alerts),
        unresolved_incidents_end=len(unresolved_incidents),
        unresolved_severe_incidents_end=len(severe_unresolved),
        downtime_intervals=_interval_count(sorted_samples, lambda sample: not sample.connected),
        stale_sync_intervals=_interval_count(sorted_samples, lambda sample: not sample.account_sync_fresh or not sample.position_sync_fresh),
        reconciliation_failure_intervals=_interval_count(sorted_samples, lambda sample: not sample.reconciliation_fresh or sample.reconciliation_anomalies > 0),
        alert_burden_per_day=_rate_per_day(len(alerts), observed_duration),
        incident_burden_per_day=_rate_per_day(len(incidents), observed_duration),
    )


def analyze_campaign_recurrence(
    samples: list[SoakSample],
    incidents: list[BrokerIncident],
    alerts: list[OperationalAlert],
    anomalies: list[SoakAnomaly],
    *,
    min_count: int = 2,
) -> list[SoakCampaignRecurringIssue]:
    """Detect recurring, worsening, or clustered campaign failure modes."""

    events: dict[str, list[tuple[datetime, str]]] = {}
    for sample in samples:
        _add_issue(events, "broker_disconnect", sample.sampled_at, "broker_checks") if not sample.connected else None
        _add_issue(events, "stale_account_state", sample.sampled_at, "account_sync") if not sample.account_sync_fresh else None
        _add_issue(events, "stale_position_state", sample.sampled_at, "position_sync") if not sample.position_sync_fresh else None
        _add_issue(events, "reconciliation_failure", sample.sampled_at, "reconciliation") if not sample.reconciliation_fresh or sample.reconciliation_anomalies > 0 else None
        _add_issue(events, "retry_exhausted", sample.sampled_at, "broker_checks") if sample.retry_exhausted > 0 else None
        _add_issue(events, "broker_reject", sample.sampled_at, "broker_checks") if sample.broker_rejects > 0 else None
        _add_issue(events, "guardrail_trigger", sample.sampled_at, "guardrails") if sample.live_guardrail_triggers > 0 else None
        _add_issue(events, "manual_intervention", sample.sampled_at, "operator_review") if sample.manual_intervention_required > 0 else None
        _add_issue(events, "degraded_mode", sample.sampled_at, "restart/recovery" if sample.recovery_invoked else "health") if sample.degraded_mode else None
    for incident in incidents:
        _add_issue(events, f"incident:{incident.category.value}", incident.opened_at, "incidents")
    for alert in alerts:
        _add_issue(events, f"alert:{alert.category.value}", alert.opened_at, "alerts")
    for anomaly in anomalies:
        _add_issue(events, f"soak_anomaly:{anomaly.category.value}", anomaly.detected_at, "soak_analysis")

    recurring: list[SoakCampaignRecurringIssue] = []
    for category, occurrences in sorted(events.items()):
        if len(occurrences) < min_count:
            continue
        timestamps = sorted(timestamp for timestamp, _cluster in occurrences)
        clusters = [cluster for _timestamp, cluster in occurrences]
        recurring.append(
            SoakCampaignRecurringIssue(
                category=category,
                count=len(occurrences),
                severity=_recurrence_severity(category, len(occurrences)),
                first_seen=timestamps[0],
                last_seen=timestamps[-1],
                trend=_recurrence_trend(timestamps),
                cluster=_dominant_cluster(clusters),
                recommendation=_recurrence_recommendation(category),
            )
        )
    return recurring


def assess_campaign_readiness(
    campaign: SoakCampaign,
    metrics: SoakCampaignReliabilityMetrics,
    recurring_issues: list[SoakCampaignRecurringIssue],
    settings: AppSettings,
) -> SoakCampaignReadinessAssessment:
    """Return conservative campaign readiness for operator review."""

    blocking: list[str] = []
    warnings: list[str] = []
    reasons: list[str] = []
    observed_hours = metrics.observed_duration_seconds / 3600.0
    reconciliation_failure_pct = round(100.0 - metrics.reconciliation_success_pct, 2)
    if metrics.samples == 0:
        blocking.append("campaign has no samples")
    if observed_hours < settings.soak.campaign_min_limited_hours:
        blocking.append(f"observed duration {observed_hours:.2f}h is below limited-readiness minimum")
    elif observed_hours < settings.soak.campaign_min_supervised_hours:
        warnings.append(f"observed duration {observed_hours:.2f}h is below supervised-readiness minimum")
    if metrics.unresolved_severe_incidents_end > settings.soak.campaign_not_ready_max_unresolved_severe_incidents:
        blocking.append("unresolved high/critical incidents remain at campaign end")
    if metrics.broker_unavailable_pct > settings.soak.campaign_not_ready_broker_unavailable_pct:
        blocking.append(f"broker unavailable {metrics.broker_unavailable_pct}% exceeds not-ready threshold")
    elif metrics.broker_unavailable_pct > settings.soak.campaign_limited_broker_unavailable_pct:
        warnings.append(f"broker unavailable {metrics.broker_unavailable_pct}% exceeds limited-readiness threshold")
    if reconciliation_failure_pct > settings.soak.campaign_not_ready_reconciliation_failure_pct:
        blocking.append(f"reconciliation failure {reconciliation_failure_pct}% exceeds not-ready threshold")
    elif reconciliation_failure_pct > settings.soak.campaign_limited_reconciliation_failure_pct:
        warnings.append(f"reconciliation failure {reconciliation_failure_pct}% exceeds limited-readiness threshold")
    if metrics.degraded_mode_pct > settings.soak.campaign_not_ready_degraded_pct:
        blocking.append(f"degraded mode {metrics.degraded_mode_pct}% exceeds not-ready threshold")
    elif metrics.degraded_mode_pct > settings.soak.campaign_limited_degraded_pct:
        warnings.append(f"degraded mode {metrics.degraded_mode_pct}% exceeds limited-readiness threshold")
    if metrics.retry_exhausted_count > settings.soak.campaign_not_ready_max_retry_exhausted:
        blocking.append("retry exhaustion occurred during campaign")
    if metrics.manual_intervention_count > settings.soak.campaign_not_ready_max_manual_intervention:
        blocking.append("manual intervention was required during campaign")
    if metrics.alert_burden_per_day > settings.soak.campaign_not_ready_alert_burden_per_day:
        blocking.append("alert burden per day exceeds not-ready threshold")
    elif metrics.alert_burden_per_day > settings.soak.campaign_limited_alert_burden_per_day:
        warnings.append("alert burden per day exceeds limited-readiness threshold")
    severe_recurring = [issue for issue in recurring_issues if issue.severity in {"high", "critical"}]
    if any(issue.severity == "critical" for issue in severe_recurring):
        blocking.append("critical recurring operational issue detected")
    elif severe_recurring:
        warnings.append("recurring high-severity operational issues require review")

    if blocking:
        rating = SoakCampaignReadiness.NOT_READY
        reasons.append("blocking operational readiness issues were found")
    elif warnings:
        rating = SoakCampaignReadiness.LIMITED_READY
        reasons.append("campaign is usable for continued supervised evaluation with limitations")
    else:
        rating = SoakCampaignReadiness.SUPERVISED_READY
        reasons.append("campaign stayed within configured readiness thresholds")

    next_actions = _campaign_next_actions(rating, blocking, warnings)
    suggested = settings.soak.campaign_suggested_rerun_hours if rating != SoakCampaignReadiness.SUPERVISED_READY else None
    return SoakCampaignReadinessAssessment(
        campaign_id=campaign.campaign_id,
        assessed_at=datetime.now(timezone.utc),
        rating=rating,
        key_reasons=_dedupe(reasons),
        blocking_issues=_dedupe(blocking),
        warnings=_dedupe(warnings),
        recommended_next_actions=next_actions,
        suggested_rerun_duration_hours=suggested,
        metrics=metrics,
    )


def finalize_soak_campaign(
    campaign: SoakCampaign,
    assessment: SoakCampaignReadinessAssessment,
    samples: list[SoakSample],
    *,
    now: datetime | None = None,
    status: SoakCampaignStatus = SoakCampaignStatus.FINALIZED,
) -> SoakCampaign:
    """Return finalized campaign state with readiness and compact summary."""

    return campaign.model_copy(
        update={
            "ended_at": now or datetime.now(timezone.utc),
            "status": status,
            "readiness": assessment.rating,
            "samples": len(samples),
            "summary": {
                "readiness": assessment.rating.value,
                "observed_duration_hours": round(assessment.metrics.observed_duration_seconds / 3600.0, 2),
                "health_success_pct": assessment.metrics.health_success_pct,
                "reconciliation_success_pct": assessment.metrics.reconciliation_success_pct,
                "degraded_mode_pct": assessment.metrics.degraded_mode_pct,
                "unresolved_incidents_end": assessment.metrics.unresolved_incidents_end,
            },
        }
    )


def _soak_anomaly(
    run_id: str,
    detected_at: datetime,
    category: SoakAnomalyCategory,
    severity: str,
    count: int,
    reason: str,
    recommendation: str,
) -> SoakAnomaly:
    return SoakAnomaly(
        anomaly_id=str(uuid.uuid4()),
        run_id=run_id,
        detected_at=detected_at,
        category=category,
        severity=severity,
        count=max(1, count),
        reason=reason,
        recommendation=recommendation,
    )


def _metric_value(metrics: list[OperationalMetric], name: str) -> float:
    return sum(metric.value for metric in metrics if metric.name == name)


def _health_flaps(samples: list[SoakSample]) -> int:
    if len(samples) < 2:
        return 0
    flaps = 0
    previous = _health_bucket(samples[0])
    for sample in samples[1:]:
        current = _health_bucket(sample)
        if current != previous:
            flaps += 1
        previous = current
    return flaps


def _health_bucket(sample: SoakSample) -> str:
    return "healthy" if sample.connected and sample.health_status == "healthy" else "unhealthy"


def _interval_durations(samples: list[SoakSample], predicate: Callable[[SoakSample], bool]) -> list[float]:
    durations: list[float] = []
    start: datetime | None = None
    previous: datetime | None = None
    for sample in samples:
        active = predicate(sample)
        if active and start is None:
            start = sample.sampled_at
        if not active and start is not None:
            durations.append(max(0.0, ((previous or sample.sampled_at) - start).total_seconds()))
            start = None
        previous = sample.sampled_at
    if start is not None and previous is not None:
        durations.append(max(0.0, (previous - start).total_seconds()))
    return durations


def _recovery_durations(samples: list[SoakSample]) -> list[float]:
    durations: list[float] = []
    unhealthy_since: datetime | None = None
    for sample in samples:
        healthy = sample.connected and sample.health_status == "healthy"
        if not healthy and unhealthy_since is None:
            unhealthy_since = sample.sampled_at
        if healthy and unhealthy_since is not None:
            durations.append(max(0.0, (sample.sampled_at - unhealthy_since).total_seconds()))
            unhealthy_since = None
    return durations


def _count_by_category(items: list[BrokerIncident] | list[OperationalAlert]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = item.category.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _count_by_attr(items: list[BrokerIncident] | list[OperationalAlert], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = getattr(item, attr)
        key = value.value if hasattr(value, "value") else str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _pct(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator) * 100.0, 2)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _campaign_observed_seconds(campaign: SoakCampaign, samples: list[SoakSample]) -> float:
    if samples:
        return max(0.0, (samples[-1].sampled_at - samples[0].sampled_at).total_seconds())
    end = campaign.ended_at or datetime.now(timezone.utc)
    return max(0.0, (end - campaign.started_at).total_seconds())


def _interval_count(samples: list[SoakSample], predicate: Callable[[SoakSample], bool]) -> int:
    count = 0
    in_interval = False
    for sample in samples:
        active = predicate(sample)
        if active and not in_interval:
            count += 1
            in_interval = True
        elif not active:
            in_interval = False
    return count


def _rate_per_day(count: int, observed_seconds: float) -> float:
    if observed_seconds <= 0.0:
        return float(count)
    return round(count / max(observed_seconds / 86400.0, 1.0 / 24.0), 2)


def _add_issue(events: dict[str, list[tuple[datetime, str]]], category: str, timestamp: datetime, cluster: str) -> None:
    events.setdefault(category, []).append((timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc), cluster))


def _recurrence_severity(category: str, count: int) -> str:
    if "manual_intervention" in category or "retry_exhausted" in category:
        return "critical"
    if "broker_disconnect" in category or "reconciliation" in category or count >= 5:
        return "high"
    return "warning"


def _recurrence_trend(timestamps: list[datetime]) -> str:
    if len(timestamps) <= 1:
        return "isolated"
    midpoint = timestamps[0] + (timestamps[-1] - timestamps[0]) / 2
    first_half = sum(1 for timestamp in timestamps if timestamp <= midpoint)
    second_half = len(timestamps) - first_half
    if second_half > first_half:
        return "worsening"
    return "recurring"


def _dominant_cluster(clusters: list[str]) -> str:
    counts: dict[str, int] = {}
    for cluster in clusters:
        counts[cluster] = counts.get(cluster, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0] if counts else "unknown"


def _recurrence_recommendation(category: str) -> str:
    if "reconciliation" in category:
        return "Inspect reconciliation anomalies and broker/local state before increasing scope."
    if "broker_disconnect" in category or "stale" in category:
        return "Extend sandbox observation and verify broker terminal/account sync stability."
    if "retry_exhausted" in category or "broker_reject" in category:
        return "Review broker rejects, retry exhaustion, symbol permissions, and order constraints."
    if "manual_intervention" in category:
        return "Stop broker submissions until operator manually reconciles state."
    return "Review campaign timeline and runbook guidance before resuming supervised broker work."


def _campaign_next_actions(rating: SoakCampaignReadiness, blocking: list[str], warnings: list[str]) -> list[str]:
    if rating == SoakCampaignReadiness.NOT_READY:
        return [
            "Do not proceed to serious supervised broker-live checks.",
            "Resolve blocking issues and rerun a broker_sandbox campaign.",
            "Review unresolved incidents, active alerts, reconciliation reports, and runbooks.",
        ]
    if rating == SoakCampaignReadiness.LIMITED_READY:
        return [
            "Continue supervised validation with reduced scope.",
            "Extend campaign duration before considering broker-live supervised checks.",
            "Review warnings and confirm operator controls remain conservative.",
        ]
    return [
        "Review full campaign report manually before any broker-live supervised work.",
        "Keep broker_live disabled until separate operator approval and live gates are intentionally enabled.",
        "Run a fresh recovery and reconciliation check before submitting anything.",
    ]
