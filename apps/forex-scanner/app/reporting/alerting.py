"""Alert-rule evaluation, routing, and operator summaries."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from app.config.settings import AppSettings
from app.execution.models import BrokerOrderState, ExecutionOrder
from app.execution.operations import AlertCategory, AlertSeverity, AlertStatus, BrokerHealthSnapshot, BrokerIncident, BrokerIncidentStatus, OperationalAlert, OperationalMetric, OperatorControlState
from app.execution.reconciliation import ReconciliationAnomaly


class AlertRuleDefinition(BaseModel):
    """One operator alert rule driven by exported operational metrics."""

    name: str
    category: AlertCategory
    severity: AlertSeverity
    condition: str
    threshold: float
    guidance: str
    runbook: str
    suppression_minutes: float


class AlertRuleEvaluation(BaseModel):
    """Result of evaluating one alert rule."""

    rule: AlertRuleDefinition
    triggered: bool
    value: float
    checked_at: datetime
    linked_incident_ids: list[str] = Field(default_factory=list)
    linked_anomaly_ids: list[str] = Field(default_factory=list)


class AlertEvaluationBundle(BaseModel):
    """All alerts and resolved notifications produced by one rule pass."""

    evaluations: list[AlertRuleEvaluation] = Field(default_factory=list)
    triggered_alerts: list[OperationalAlert] = Field(default_factory=list)
    resolved_alerts: list[OperationalAlert] = Field(default_factory=list)


class AlertDeliveryRecord(BaseModel):
    """Structured local audit record for alert delivery attempts."""

    delivery_id: str
    alert_id: str
    routed_at: datetime
    route: str
    status: str
    attempt: int = Field(default=1, ge=1)
    reason: str | None = None
    endpoint: str | None = None
    payload: dict[str, str | float | int | bool | None] = Field(default_factory=dict)


class AlertRoutingResult(BaseModel):
    """Alert routing result across local and webhook sinks."""

    records: list[AlertDeliveryRecord] = Field(default_factory=list)

    @property
    def failures(self) -> list[AlertDeliveryRecord]:
        """Return failed delivery records."""

        return [record for record in self.records if record.status == "failed"]


def default_alert_rules(settings: AppSettings) -> list[AlertRuleDefinition]:
    """Return built-in operational alert rules."""

    suppression = settings.monitoring.alert_suppression_minutes
    return [
        AlertRuleDefinition(name="broker_unavailable_too_long", category=AlertCategory.BROKER_DOWN, severity=AlertSeverity.HIGH, condition="consecutive broker unavailable samples exceed threshold", threshold=settings.monitoring.broker_unavailable_alert_samples, guidance="Restore broker connectivity, rerun recovery, and keep broker submissions paused until stable.", runbook="docs/runbooks/broker_operations.md#broker-unavailable", suppression_minutes=suppression),
        AlertRuleDefinition(name="stale_account_sync", category=AlertCategory.STALE_ACCOUNT_STATE, severity=AlertSeverity.HIGH, condition="account sync is missing or older than configured seconds", threshold=settings.monitoring.stale_account_alert_seconds, guidance="Refresh account state and verify terminal/account health before submissions.", runbook="docs/runbooks/broker_operations.md#stale-account-state", suppression_minutes=suppression),
        AlertRuleDefinition(name="stale_position_sync", category=AlertCategory.STALE_POSITION_STATE, severity=AlertSeverity.HIGH, condition="position sync is missing or older than configured seconds", threshold=settings.monitoring.stale_position_alert_seconds, guidance="Refresh broker positions and inspect open positions before submissions.", runbook="docs/runbooks/broker_operations.md#startup-recovery-after-restart", suppression_minutes=suppression),
        AlertRuleDefinition(name="stale_reconciliation", category=AlertCategory.STALE_RECONCILIATION, severity=AlertSeverity.HIGH, condition="reconciliation sync is missing or older than configured seconds", threshold=settings.monitoring.stale_reconciliation_alert_seconds, guidance="Run broker recovery and inspect reconciliation outputs.", runbook="docs/runbooks/broker_operations.md#reconciliation-mismatch", suppression_minutes=suppression),
        AlertRuleDefinition(name="severe_reconciliation_anomalies_active", category=AlertCategory.SEVERE_RECONCILIATION_MISMATCH, severity=AlertSeverity.CRITICAL, condition="active high/critical reconciliation anomaly count exceeds threshold", threshold=settings.monitoring.severe_anomaly_alert_threshold, guidance="Stop broker submissions and inspect reconciliation anomalies.", runbook="docs/runbooks/broker_operations.md#reconciliation-mismatch", suppression_minutes=suppression),
        AlertRuleDefinition(name="repeated_broker_rejects", category=AlertCategory.REPEATED_BROKER_REJECTS, severity=AlertSeverity.HIGH, condition="broker reject count exceeds threshold", threshold=settings.monitoring.repeated_reject_alert_threshold, guidance="Inspect rejection payloads, symbol permissions, and volume/stop constraints.", runbook="docs/runbooks/broker_operations.md#repeated-broker-rejects", suppression_minutes=suppression),
        AlertRuleDefinition(name="repeated_retry_exhaustion", category=AlertCategory.RETRIES_EXHAUSTED, severity=AlertSeverity.HIGH, condition="retry exhaustion count exceeds threshold", threshold=settings.monitoring.retry_exhausted_alert_threshold, guidance="Verify broker state manually before retrying broker operations.", runbook="docs/runbooks/broker_operations.md#broker-unavailable", suppression_minutes=suppression),
        AlertRuleDefinition(name="manual_intervention_required_active", category=AlertCategory.MANUAL_INTERVENTION_REQUIRED, severity=AlertSeverity.CRITICAL, condition="manual intervention count is active", threshold=1.0, guidance="Review broker terminal and reconcile local state before any further broker work.", runbook="docs/runbooks/broker_operations.md#manual-intervention-required", suppression_minutes=suppression),
        AlertRuleDefinition(name="kill_switch_activated", category=AlertCategory.KILL_SWITCH_ACTIVE, severity=AlertSeverity.CRITICAL, condition="kill switch is active", threshold=1.0, guidance="Keep submissions blocked until an operator clears the kill switch after review.", runbook="docs/runbooks/broker_operations.md#kill-switch-activation", suppression_minutes=suppression),
        AlertRuleDefinition(name="prolonged_degraded_mode", category=AlertCategory.DEGRADED_MODE, severity=AlertSeverity.HIGH, condition="degraded health/operator mode persists for configured samples", threshold=settings.monitoring.prolonged_degraded_alert_samples, guidance="Continue reduced-risk monitoring only, then resolve degraded flags before increasing scope.", runbook="docs/operations.md#operator-controls", suppression_minutes=suppression),
        AlertRuleDefinition(name="guardrail_trigger_spike", category=AlertCategory.GUARDRAIL_TRIGGER_SPIKE, severity=AlertSeverity.HIGH, condition="guardrail trigger count exceeds threshold", threshold=settings.monitoring.guardrail_trigger_spike_threshold, guidance="Inspect guardrail reasons and reduce broker/paper scope until understood.", runbook="docs/runbooks/broker_operations.md#metrics-export-check", suppression_minutes=suppression),
        AlertRuleDefinition(name="excessive_live_submission_failures", category=AlertCategory.LIVE_SUBMISSION_FAILURES, severity=AlertSeverity.CRITICAL, condition="live submission failures exceed threshold", threshold=settings.monitoring.live_submission_failure_threshold, guidance="Block live submissions and review broker validations, rejects, and reconciliation anomalies.", runbook="docs/operations.md#live-gating", suppression_minutes=suppression),
    ]


def evaluate_alert_rules(
    *,
    snapshots: list[BrokerHealthSnapshot],
    metrics: list[OperationalMetric],
    previous_alerts: list[OperationalAlert],
    incidents: list[BrokerIncident],
    anomalies: list[ReconciliationAnomaly],
    orders: list[ExecutionOrder],
    settings: AppSettings,
    operator_controls: OperatorControlState | None = None,
    now: datetime | None = None,
) -> AlertEvaluationBundle:
    """Evaluate built-in alert rules and return triggered/resolved alerts."""

    if not settings.monitoring.alert_rules_enabled:
        return AlertEvaluationBundle()
    timestamp = now or datetime.now(timezone.utc)
    latest = snapshots[-1] if snapshots else None
    evaluations: list[AlertRuleEvaluation] = []
    triggered: list[OperationalAlert] = []
    for rule in default_alert_rules(settings):
        value, linked_incidents, linked_anomalies = _rule_value(rule.name, snapshots, metrics, incidents, anomalies, orders, operator_controls, timestamp)
        is_triggered = value > 0.0 if rule.name == "kill_switch_activated" else value >= rule.threshold
        evaluation = AlertRuleEvaluation(rule=rule, triggered=is_triggered, value=value, checked_at=timestamp, linked_incident_ids=linked_incidents, linked_anomaly_ids=linked_anomalies)
        evaluations.append(evaluation)
        if not is_triggered:
            continue
        alert = _alert_from_rule(rule, value, timestamp, latest, previous_alerts, linked_incidents, linked_anomalies)
        triggered.append(alert)
    resolved = _resolved_rule_alerts(previous_alerts, triggered, timestamp)
    return AlertEvaluationBundle(evaluations=evaluations, triggered_alerts=triggered, resolved_alerts=resolved)


def route_alerts(alerts: list[OperationalAlert], settings: AppSettings, *, now: datetime | None = None) -> AlertRoutingResult:
    """Route alerts to local sink and optional webhook with fail-safe behavior."""

    timestamp = now or datetime.now(timezone.utc)
    records: list[AlertDeliveryRecord] = []
    if settings.monitoring.alert_local_sink_enabled:
        records.extend(_write_local_sink(Path(settings.monitoring.alert_local_sink_path), alerts, timestamp))
    if settings.monitoring.alert_webhook_enabled:
        records.extend(_send_webhook_alerts(alerts, settings, timestamp))
    return AlertRoutingResult(records=records)


def link_alerts_to_incidents(incidents: list[BrokerIncident], alerts: list[OperationalAlert]) -> list[BrokerIncident]:
    """Return incidents with linked alert ids filled from generated alerts."""

    alert_ids_by_incident: dict[str, list[str]] = {}
    for alert in alerts:
        for incident_id in alert.linked_incident_ids:
            alert_ids_by_incident.setdefault(incident_id, []).append(alert.alert_id)
    linked: list[BrokerIncident] = []
    for incident in incidents:
        alert_ids = alert_ids_by_incident.get(incident.incident_id, [])
        if not alert_ids:
            linked.append(incident)
            continue
        merged = sorted({*incident.linked_alert_ids, *alert_ids})
        linked.append(incident.model_copy(update={"linked_alert_ids": merged}))
    return linked


def generate_alert_report(
    alerts: list[OperationalAlert],
    evaluations: list[AlertRuleEvaluation],
    deliveries: list[AlertDeliveryRecord],
    output_dir: Path,
) -> dict[str, Path]:
    """Write operator-facing alert summary views."""

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "summary": output_dir / "summary.md",
        "summary_json": output_dir / "summary.json",
        "active_alerts": output_dir / "active_alerts.csv",
        "resolved_alerts": output_dir / "resolved_alerts.csv",
        "suppressed_alerts": output_dir / "suppressed_alerts.csv",
        "alert_aging": output_dir / "alert_aging.csv",
        "by_category_severity": output_dir / "by_category_severity.csv",
        "rule_evaluations": output_dir / "rule_evaluations.csv",
        "routing_failures": output_dir / "routing_failures.csv",
        "deliveries": output_dir / "deliveries.json",
    }
    _alerts_frame([alert for alert in alerts if alert.status == AlertStatus.ACTIVE]).to_csv(outputs["active_alerts"], index=False)
    _alerts_frame([alert for alert in alerts if alert.status == AlertStatus.RESOLVED]).to_csv(outputs["resolved_alerts"], index=False)
    _alerts_frame([alert for alert in alerts if alert.status == AlertStatus.SUPPRESSED]).to_csv(outputs["suppressed_alerts"], index=False)
    _alert_aging_frame(alerts).to_csv(outputs["alert_aging"], index=False)
    _by_category_severity_frame(alerts).to_csv(outputs["by_category_severity"], index=False)
    _evaluations_frame(evaluations).to_csv(outputs["rule_evaluations"], index=False)
    _deliveries_frame([record for record in deliveries if record.status == "failed"]).to_csv(outputs["routing_failures"], index=False)
    outputs["deliveries"].write_text(json.dumps([record.model_dump(mode="json") for record in deliveries], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = _summary_payload(alerts, evaluations, deliveries)
    outputs["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["summary"].write_text(_summary_markdown(summary), encoding="utf-8")
    return outputs


def _rule_value(
    name: str,
    snapshots: list[BrokerHealthSnapshot],
    metrics: list[OperationalMetric],
    incidents: list[BrokerIncident],
    anomalies: list[ReconciliationAnomaly],
    orders: list[ExecutionOrder],
    controls: OperatorControlState | None,
    now: datetime,
) -> tuple[float, list[str], list[str]]:
    latest = snapshots[-1] if snapshots else None
    severe_anomalies = [anomaly for anomaly in anomalies if anomaly.severity in {"high", "critical"}]
    if name == "broker_unavailable_too_long":
        return float(_consecutive(snapshots, lambda snapshot: not snapshot.connected)), _incident_ids(incidents, {"broker_unavailable", "mt5_terminal_not_reachable", "connectivity_unstable"}), []
    if name == "stale_account_sync":
        if latest is None:
            return 0.0, [], []
        return _stale_age_value(latest.last_successful_account_sync_at if latest else None, now), _incident_ids(incidents, {"account_state_stale"}), []
    if name == "stale_position_sync":
        if latest is None:
            return 0.0, [], []
        return _stale_age_value(latest.last_successful_position_sync_at if latest else None, now), [], []
    if name == "stale_reconciliation":
        if latest is None:
            return 0.0, [], []
        return _stale_age_value(latest.last_successful_reconciliation_at if latest else None, now), _incident_ids(incidents, {"reconciliation_anomaly", "stale_broker_state"}), [anomaly.anomaly_id for anomaly in anomalies]
    if name == "severe_reconciliation_anomalies_active":
        return float(len(severe_anomalies)), _incident_ids(incidents, {"reconciliation_anomaly", "partial_local_broker_desync", "unknown_broker_state"}), [anomaly.anomaly_id for anomaly in severe_anomalies]
    if name == "repeated_broker_rejects":
        return max(_metric_sum(metrics, "broker_rejects"), float(_count_orders_with_state(orders, BrokerOrderState.REJECTED))), _incident_ids(incidents, {"repeated_rejects"}), []
    if name == "repeated_retry_exhaustion":
        return max(_metric_sum(metrics, "retry_exhausted"), float(_count_orders_with_state(orders, BrokerOrderState.RETRY_EXHAUSTED))), _incident_ids(incidents, {"repeated_submit_failures"}), []
    if name == "manual_intervention_required_active":
        return max(_metric_sum(metrics, "manual_intervention_required"), float(_count_orders_with_state(orders, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED))), _incident_ids(incidents, {"manual_intervention_required"}), []
    if name == "kill_switch_activated":
        return 1.0 if latest and latest.kill_switch_active else 0.0, [], []
    if name == "prolonged_degraded_mode":
        sample_count = _consecutive(snapshots, lambda snapshot: snapshot.health_status in {"degraded", "unavailable", "manual_intervention_required"} or bool(snapshot.degraded_flags))
        return float(max(sample_count, 1 if controls and controls.degraded_mode else 0)), [], []
    if name == "guardrail_trigger_spike":
        return _metric_sum(metrics, "live_guardrail_triggers"), [], []
    if name == "excessive_live_submission_failures":
        return float(_live_submission_failures(orders)), [], []
    return 0.0, [], []


def _alert_from_rule(
    rule: AlertRuleDefinition,
    value: float,
    timestamp: datetime,
    latest: BrokerHealthSnapshot | None,
    previous_alerts: list[OperationalAlert],
    linked_incident_ids: list[str],
    linked_anomaly_ids: list[str],
) -> OperationalAlert:
    mode = latest.mode if latest else "unknown"
    broker = latest.broker if latest else "unknown"
    dedupe_key = f"rule:{mode}:{broker}:{rule.name}"
    previous = next((alert for alert in reversed(previous_alerts) if alert.dedupe_key == dedupe_key and alert.status != AlertStatus.RESOLVED), None)
    suppression_until = previous.suppression_until if previous else None
    status = AlertStatus.ACTIVE
    alert_id = str(uuid.uuid4())
    opened_at = timestamp
    if previous is not None:
        alert_id = previous.alert_id
        opened_at = previous.opened_at
        if suppression_until and timestamp <= suppression_until:
            status = AlertStatus.SUPPRESSED
    return OperationalAlert(
        alert_id=alert_id,
        category=rule.category,
        severity=rule.severity,
        status=status,
        opened_at=opened_at,
        updated_at=timestamp,
        reason=f"{rule.name}: value {value:.2f} triggered condition: {rule.condition}",
        recommendation=rule.guidance,
        dedupe_key=dedupe_key,
        suppression_until=suppression_until or timestamp + timedelta(minutes=rule.suppression_minutes),
        linked_incident_ids=linked_incident_ids,
        linked_anomaly_ids=linked_anomaly_ids,
        payload={"rule_name": rule.name, "condition": rule.condition, "threshold": rule.threshold, "value": value, "runbook": rule.runbook, "broker": broker, "mode": mode},
    )


def _resolved_rule_alerts(previous_alerts: list[OperationalAlert], triggered: list[OperationalAlert], timestamp: datetime) -> list[OperationalAlert]:
    active_keys = {alert.dedupe_key for alert in triggered}
    resolved: list[OperationalAlert] = []
    for alert in previous_alerts:
        if not alert.dedupe_key.startswith("rule:") or alert.status == AlertStatus.RESOLVED or alert.dedupe_key in active_keys:
            continue
        resolved.append(alert.model_copy(update={"status": AlertStatus.RESOLVED, "updated_at": timestamp, "resolved_at": timestamp}))
    return resolved


def _write_local_sink(path: Path, alerts: list[OperationalAlert], timestamp: datetime) -> list[AlertDeliveryRecord]:
    path.parent.mkdir(parents=True, exist_ok=True)
    records: list[AlertDeliveryRecord] = []
    with path.open("a", encoding="utf-8") as handle:
        for alert in alerts:
            record = AlertDeliveryRecord(delivery_id=str(uuid.uuid4()), alert_id=alert.alert_id, routed_at=timestamp, route="local", status="sent", payload={"category": alert.category.value, "severity": alert.severity.value, "status": alert.status.value})
            handle.write(json.dumps({"delivery": record.model_dump(mode="json"), "alert": alert.model_dump(mode="json")}, sort_keys=True) + "\n")
            records.append(record)
    return records


def _send_webhook_alerts(alerts: list[OperationalAlert], settings: AppSettings, timestamp: datetime) -> list[AlertDeliveryRecord]:
    url = os.getenv(settings.monitoring.alert_webhook_url_env, "").strip()
    if not url:
        return [
            AlertDeliveryRecord(
                delivery_id=str(uuid.uuid4()),
                alert_id=alert.alert_id,
                routed_at=timestamp,
                route="webhook",
                status="failed",
                reason=f"missing {settings.monitoring.alert_webhook_url_env}",
                endpoint=None,
                payload={"category": alert.category.value, "severity": alert.severity.value},
            )
            for alert in alerts
            if _webhook_should_send(alert, settings)
        ]
    records: list[AlertDeliveryRecord] = []
    for alert in alerts:
        if not _webhook_should_send(alert, settings):
            continue
        payload = json.dumps({"alert": alert.model_dump(mode="json")}).encode("utf-8")
        last_error: str | None = None
        for attempt in range(1, settings.monitoring.alert_webhook_max_attempts + 1):
            try:
                request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=settings.monitoring.alert_webhook_timeout_seconds) as response:
                    status_code = int(getattr(response, "status", 200))
                records.append(AlertDeliveryRecord(delivery_id=str(uuid.uuid4()), alert_id=alert.alert_id, routed_at=timestamp, route="webhook", status="sent", attempt=attempt, endpoint=_redact_url(url), payload={"status_code": status_code}))
                break
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                last_error = str(exc)
                if attempt < settings.monitoring.alert_webhook_max_attempts:
                    time.sleep(0.1)
        else:
            records.append(AlertDeliveryRecord(delivery_id=str(uuid.uuid4()), alert_id=alert.alert_id, routed_at=timestamp, route="webhook", status="failed", attempt=settings.monitoring.alert_webhook_max_attempts, reason=last_error or "webhook delivery failed", endpoint=_redact_url(url), payload={"category": alert.category.value, "severity": alert.severity.value}))
    return records


def _webhook_should_send(alert: OperationalAlert, settings: AppSettings) -> bool:
    if alert.status == AlertStatus.SUPPRESSED:
        return False
    if alert.status == AlertStatus.RESOLVED and not settings.monitoring.alert_webhook_resolved_notifications:
        return False
    return True


def _redact_url(url: str) -> str:
    if "://" not in url:
        return "redacted"
    scheme, rest = url.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host}/..."


def _alerts_frame(alerts: list[OperationalAlert]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "alert_id": alert.alert_id,
                "category": alert.category.value,
                "severity": alert.severity.value,
                "status": alert.status.value,
                "opened_at": alert.opened_at.isoformat(),
                "updated_at": alert.updated_at.isoformat(),
                "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else "",
                "dedupe_key": alert.dedupe_key,
                "linked_incident_ids": ",".join(alert.linked_incident_ids),
                "linked_anomaly_ids": ",".join(alert.linked_anomaly_ids),
                "reason": alert.reason,
                "recommendation": alert.recommendation,
                "runbook": alert.payload.get("runbook"),
            }
            for alert in alerts
        ]
    )


def _alert_aging_frame(alerts: list[OperationalAlert]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "alert_id": alert.alert_id,
                "category": alert.category.value,
                "severity": alert.severity.value,
                "status": alert.status.value,
                "age_minutes": round(max(0.0, (alert.updated_at - alert.opened_at).total_seconds() / 60.0), 2),
                "suppression_until": alert.suppression_until.isoformat() if alert.suppression_until else "",
            }
            for alert in alerts
        ]
    )


def _by_category_severity_frame(alerts: list[OperationalAlert]) -> pd.DataFrame:
    if not alerts:
        return pd.DataFrame(columns=["category", "severity", "status", "count"])
    frame = pd.DataFrame([{"category": alert.category.value, "severity": alert.severity.value, "status": alert.status.value, "count": 1} for alert in alerts])
    return frame.groupby(["category", "severity", "status"], dropna=False).sum(numeric_only=True).reset_index()


def _evaluations_frame(evaluations: list[AlertRuleEvaluation]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "rule": evaluation.rule.name,
                "category": evaluation.rule.category.value,
                "severity": evaluation.rule.severity.value,
                "triggered": evaluation.triggered,
                "value": evaluation.value,
                "threshold": evaluation.rule.threshold,
                "condition": evaluation.rule.condition,
                "runbook": evaluation.rule.runbook,
                "linked_incidents": ",".join(evaluation.linked_incident_ids),
                "linked_anomalies": ",".join(evaluation.linked_anomaly_ids),
            }
            for evaluation in evaluations
        ]
    )


def _deliveries_frame(deliveries: list[AlertDeliveryRecord]) -> pd.DataFrame:
    return pd.DataFrame([record.model_dump(mode="json") for record in deliveries])


def _summary_payload(alerts: list[OperationalAlert], evaluations: list[AlertRuleEvaluation], deliveries: list[AlertDeliveryRecord]) -> dict[str, int]:
    return {
        "alerts": len(alerts),
        "active": sum(1 for alert in alerts if alert.status == AlertStatus.ACTIVE),
        "resolved": sum(1 for alert in alerts if alert.status == AlertStatus.RESOLVED),
        "suppressed": sum(1 for alert in alerts if alert.status == AlertStatus.SUPPRESSED),
        "triggered_rules": sum(1 for evaluation in evaluations if evaluation.triggered),
        "delivery_failures": sum(1 for record in deliveries if record.status == "failed"),
    }


def _summary_markdown(summary: dict[str, int]) -> str:
    return "\n".join(
        [
            "# Alert Rule Summary",
            "",
            f"Alerts: {summary['alerts']}",
            f"Active: {summary['active']}",
            f"Resolved: {summary['resolved']}",
            f"Suppressed: {summary['suppressed']}",
            f"Triggered rules: {summary['triggered_rules']}",
            f"Delivery failures: {summary['delivery_failures']}",
            "",
        ]
    )


def _stale_age_value(timestamp: datetime | None, now: datetime) -> float:
    if timestamp is None:
        return float("inf")
    normalized = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    return max(0.0, (now - normalized).total_seconds())


def _consecutive(snapshots: list[BrokerHealthSnapshot], predicate: Callable[[BrokerHealthSnapshot], bool]) -> int:
    count = 0
    for snapshot in reversed(snapshots):
        if predicate(snapshot):
            count += 1
            continue
        break
    return count


def _incident_ids(incidents: list[BrokerIncident], categories: set[str]) -> list[str]:
    return [incident.incident_id for incident in incidents if incident.status == BrokerIncidentStatus.OPEN and incident.category.value in categories]


def _metric_sum(metrics: list[OperationalMetric], name: str) -> float:
    return sum(metric.value for metric in metrics if metric.name == name)


def _count_orders_with_state(orders: list[ExecutionOrder], state: BrokerOrderState) -> int:
    return sum(1 for order in orders if _has_state(order, state))


def _live_submission_failures(orders: list[ExecutionOrder]) -> int:
    failure_states = {
        BrokerOrderState.VALIDATION_FAILED,
        BrokerOrderState.REJECTED,
        BrokerOrderState.RETRY_EXHAUSTED,
        BrokerOrderState.BROKER_UNREACHABLE,
        BrokerOrderState.MANUAL_INTERVENTION_REQUIRED,
        BrokerOrderState.RECONCILIATION_MISMATCH,
    }
    return sum(1 for order in orders if order.broker_mode == "broker_live" and any(_has_state(order, state) for state in failure_states))


def _has_state(order: ExecutionOrder, state: BrokerOrderState) -> bool:
    return any(transition.state == state for transition in order.broker_transitions)
