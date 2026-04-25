"""Prometheus-compatible monitoring exporters for supervised operations."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.execution.models import BrokerOrderState, ExecutionOrder, TradeEventType
from app.execution.operations import AlertStatus, BrokerHealthSnapshot, BrokerIncident, BrokerIncidentStatus, OperationalAlert, OperationalMetric, OperatorControlState
from app.execution.reconciliation import ReconciliationAnomaly


def write_prometheus_textfile(
    path: Path,
    *,
    snapshots: list[BrokerHealthSnapshot],
    metrics: list[OperationalMetric],
    alerts: list[OperationalAlert],
    incidents: list[BrokerIncident],
    anomalies: list[ReconciliationAnomaly],
    orders: list[ExecutionOrder],
    operator_controls: OperatorControlState | None = None,
) -> Path:
    """Write local Prometheus textfile metrics for node-exporter style pickup."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_prometheus_text(snapshots=snapshots, metrics=metrics, alerts=alerts, incidents=incidents, anomalies=anomalies, orders=orders, operator_controls=operator_controls), encoding="utf-8")
    return path


def build_prometheus_text(
    *,
    snapshots: list[BrokerHealthSnapshot],
    metrics: list[OperationalMetric],
    alerts: list[OperationalAlert],
    incidents: list[BrokerIncident],
    anomalies: list[ReconciliationAnomaly],
    orders: list[ExecutionOrder],
    operator_controls: OperatorControlState | None = None,
) -> str:
    """Build Prometheus text exposition from persisted operational state."""

    latest = snapshots[-1] if snapshots else None
    mode = latest.mode if latest else _latest_metric_value(metrics, "mode", "unknown")
    broker = latest.broker if latest else _latest_metric_value(metrics, "broker", "unknown")
    base = {"execution_mode": str(mode), "broker_adapter": str(broker)}
    active_alerts = [alert for alert in alerts if alert.status != AlertStatus.RESOLVED]
    active_incidents = [incident for incident in incidents if incident.status == BrokerIncidentStatus.OPEN]
    severe_anomalies = [anomaly for anomaly in anomalies if anomaly.severity in {"high", "critical"}]
    latest_labels = _labels(base)
    lines: list[str] = []

    _emit(lines, "forex_scanner_execution_mode", "Current configured execution mode as a labelled gauge.", "gauge", [(base, 1.0)])
    _emit(lines, "forex_scanner_broker_connected", "Latest broker connectivity state.", "gauge", [(base, _bool(latest.connected if latest else False))])
    _emit(lines, "forex_scanner_health_check_success", "Latest broker health check success state.", "gauge", [(base, _bool(latest.health_status == "healthy" if latest else False))])
    _emit(lines, "forex_scanner_broker_connectivity_failures_total", "Count of persisted samples where broker connectivity was unavailable.", "counter", [(base, sum(1 for snapshot in snapshots if not snapshot.connected))])
    _emit(lines, "forex_scanner_broker_consecutive_failures", "Latest broker consecutive failure count.", "gauge", [(base, latest.consecutive_failures if latest else 0)])
    _emit(lines, "forex_scanner_account_sync_fresh", "Latest account sync freshness state.", "gauge", [(base, _bool(latest.last_successful_account_sync_at is not None if latest else False))])
    _emit(lines, "forex_scanner_account_sync_last_success_timestamp_seconds", "Unix timestamp for the latest successful account sync.", "gauge", [(base, _timestamp(latest.last_successful_account_sync_at if latest else None))])
    _emit(lines, "forex_scanner_position_sync_fresh", "Latest position sync freshness state.", "gauge", [(base, _bool(latest.last_successful_position_sync_at is not None if latest else False))])
    _emit(lines, "forex_scanner_position_sync_last_success_timestamp_seconds", "Unix timestamp for the latest successful position sync.", "gauge", [(base, _timestamp(latest.last_successful_position_sync_at if latest else None))])
    _emit(lines, "forex_scanner_reconciliation_fresh", "Latest reconciliation freshness state.", "gauge", [(base, _bool(latest.last_successful_reconciliation_at is not None if latest else False))])
    _emit(lines, "forex_scanner_reconciliation_last_success_timestamp_seconds", "Unix timestamp for the latest successful reconciliation.", "gauge", [(base, _timestamp(latest.last_successful_reconciliation_at if latest else None))])
    _emit(lines, "forex_scanner_reconciliation_failures_total", "Total severe reconciliation anomalies persisted for operator review.", "counter", [(base, len(severe_anomalies))])
    _emit(lines, "forex_scanner_reconciliation_anomalies_active", "Persisted reconciliation anomalies by severity and category.", "gauge", _counter_samples(base, _anomaly_counts(anomalies)))
    _emit(lines, "forex_scanner_operational_alerts_active", "Active operational alerts by severity and category.", "gauge", _counter_samples(base, _alert_counts(active_alerts)))
    _emit(lines, "forex_scanner_operational_incidents_active", "Open operational incidents by severity and category.", "gauge", _counter_samples(base, _incident_counts(active_incidents)))
    _emit(lines, "forex_scanner_broker_rejects_total", "Broker rejected order count.", "counter", [(base, _count_orders_with_state(orders, BrokerOrderState.REJECTED))])
    _emit(lines, "forex_scanner_broker_retry_attempts_total", "Observable broker retry attempts represented by retry-exhausted transitions or metrics.", "counter", [(base, _retry_attempts_total(orders, metrics))])
    _emit(lines, "forex_scanner_broker_retries_exhausted_total", "Broker retry exhaustion count.", "counter", [(base, _retry_exhausted_total(orders, metrics))])
    _emit(lines, "forex_scanner_stale_state_detections_total", "Persisted stale-state detection count.", "counter", [(base, _metric_sum(metrics, "stale_state_detections"))])
    _emit(lines, "forex_scanner_live_guardrail_triggers_total", "Live guardrail trigger count.", "counter", [(base, _metric_sum(metrics, "live_guardrail_triggers"))])
    _emit(lines, "forex_scanner_manual_intervention_required", "Manual intervention requirement count in current persisted state.", "gauge", [(base, _manual_intervention_count(orders, metrics, incidents))])
    _emit(lines, "forex_scanner_kill_switch_active", "Latest broker kill-switch state.", "gauge", [(base, _bool(latest.kill_switch_active if latest else False))])
    _emit(lines, "forex_scanner_operator_degraded_mode", "Persisted operator degraded-mode control state.", "gauge", [(base, _bool(operator_controls.degraded_mode if operator_controls else False))])
    _emit(lines, "forex_scanner_broker_health_degraded", "Latest broker degraded health state.", "gauge", [(base, _bool(latest.health_status in {"degraded", "unavailable", "manual_intervention_required"} if latest else False))])
    _emit(lines, "forex_scanner_live_submission_attempts_total", "Broker-live submit request count.", "counter", [(base, _live_submission_attempts(orders))])
    _emit(lines, "forex_scanner_live_submission_failures_total", "Broker-live validation, rejection, retry, unreachable, or manual-intervention failure count.", "counter", [(base, _live_submission_failures(orders))])
    _emit(lines, "forex_scanner_recovery_actions_total", "Restart/recovery action count from persisted operational metrics.", "counter", [(base, _metric_sum(metrics, "restart_recovery_events"))])
    _emit(lines, "forex_scanner_last_successful_broker_action_timestamp_seconds", "Unix timestamp for latest successful broker action.", "gauge", [(base, _timestamp(latest.last_successful_broker_action_at if latest else None))])
    _emit(lines, "forex_scanner_last_failed_broker_action_timestamp_seconds", "Unix timestamp for latest failed broker action.", "gauge", [(base, _timestamp(latest.last_failed_broker_action_at if latest else None))])
    _emit(lines, "forex_scanner_open_broker_positions", "Latest open broker position count.", "gauge", [(base, latest.open_positions if latest else 0)])
    _emit(lines, "forex_scanner_pending_broker_orders", "Latest pending broker order count.", "gauge", [(base, latest.pending_orders if latest else 0)])
    _emit(lines, "forex_scanner_metric_export_info", "Static metric indicating exporter health.", "gauge", [(base | {"state": "ok"}, 1.0)])

    # Backward-compatible unlabelled gauges retained for existing local checks.
    lines.extend(
        [
            "# HELP forex_scanner_legacy_export_info Compatibility metrics for older local checks.",
            "# TYPE forex_scanner_legacy_export_info gauge",
            f"forex_scanner_legacy_export_info{latest_labels} 1",
            f"forex_scanner_broker_connected {_bool(latest.connected if latest else False)}",
            f"forex_scanner_health_check_success {_bool(latest.health_status == 'healthy' if latest else False)}",
            f"forex_scanner_account_sync_fresh {_bool(latest.last_successful_account_sync_at is not None if latest else False)}",
            f"forex_scanner_position_sync_fresh {_bool(latest.last_successful_position_sync_at is not None if latest else False)}",
            "",
        ]
    )
    return "\n".join(lines)


def _emit(lines: list[str], name: str, help_text: str, metric_type: str, samples: list[tuple[dict[str, str], int | float]]) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")
    if samples:
        for labels, value in samples:
            lines.append(f"{name}{_labels(labels)} {_format_value(value)}")
    else:
        lines.append(f"{name} 0")


def _labels(values: dict[str, str]) -> str:
    if not values:
        return ""
    labels = ",".join(f'{key}="{_escape_label(value)}"' for key, value in sorted(values.items()))
    return "{" + labels + "}"


def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: int | float) -> str:
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else f"{numeric:.6f}".rstrip("0").rstrip(".")


def _counter_samples(base: dict[str, str], counts: Counter[tuple[str, str]]) -> list[tuple[dict[str, str], int]]:
    return [(base | {"severity": severity, "category": category}, count) for (severity, category), count in sorted(counts.items())]


def _anomaly_counts(anomalies: list[ReconciliationAnomaly]) -> Counter[tuple[str, str]]:
    return Counter((anomaly.severity, anomaly.anomaly_type.value) for anomaly in anomalies)


def _alert_counts(alerts: list[OperationalAlert]) -> Counter[tuple[str, str]]:
    return Counter((alert.severity.value, alert.category.value) for alert in alerts)


def _incident_counts(incidents: list[BrokerIncident]) -> Counter[tuple[str, str]]:
    return Counter((incident.severity.value, incident.category.value) for incident in incidents)


def _metric_sum(metrics: list[OperationalMetric], name: str) -> float:
    return sum(metric.value for metric in metrics if metric.name == name)


def _latest_metric_value(metrics: list[OperationalMetric], key: str, default: str) -> str:
    if not metrics:
        return default
    latest = metrics[-1]
    if key == "mode":
        return latest.mode
    if key == "broker":
        return latest.broker
    return default


def _count_orders_with_state(orders: list[ExecutionOrder], state: BrokerOrderState) -> int:
    return sum(1 for order in orders if _has_state(order, state))


def _retry_attempts_total(orders: list[ExecutionOrder], metrics: list[OperationalMetric]) -> float:
    transitions = _count_orders_with_state(orders, BrokerOrderState.RETRY_EXHAUSTED)
    return max(float(transitions), _metric_sum(metrics, "retry_exhausted"))


def _retry_exhausted_total(orders: list[ExecutionOrder], metrics: list[OperationalMetric]) -> float:
    transitions = _count_orders_with_state(orders, BrokerOrderState.RETRY_EXHAUSTED)
    return max(float(transitions), _metric_sum(metrics, "retry_exhausted"))


def _manual_intervention_count(orders: list[ExecutionOrder], metrics: list[OperationalMetric], incidents: list[BrokerIncident]) -> float:
    order_count = _count_orders_with_state(orders, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED)
    incident_count = sum(1 for incident in incidents if incident.category.value == "manual_intervention_required" and incident.status == BrokerIncidentStatus.OPEN)
    return max(float(order_count + incident_count), _metric_sum(metrics, "manual_intervention_required"))


def _live_submission_attempts(orders: list[ExecutionOrder]) -> int:
    return sum(1 for order in orders if order.broker_mode == "broker_live" and _has_state(order, BrokerOrderState.SUBMIT_REQUESTED))


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


def _timestamp(value: datetime | None) -> int:
    if value is None:
        return 0
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return int(normalized.timestamp())


def _has_state(order: ExecutionOrder, state: BrokerOrderState) -> bool:
    return any(transition.state == state for transition in order.broker_transitions)


def _bool(value: bool) -> int:
    return 1 if value else 0
