"""Broker execution and reconciliation reports for operator review."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.execution.models import BrokerAccountState, BrokerOrderState, ExecutionOrder, TradeEventType
from app.execution.operations import BrokerHealthSnapshot, BrokerIncident, OperationalAlert, OperationalMetric, OperatorControlState, ReliabilitySummary, ResumeReadiness, build_reliability_summary
from app.execution.reconciliation import ReconciliationAnomaly


def generate_broker_execution_report(
    orders: list[ExecutionOrder],
    anomalies: list[ReconciliationAnomaly],
    output_dir: Path,
    account_state: BrokerAccountState | None = None,
    incidents: list[BrokerIncident] | None = None,
    health_snapshots: list[BrokerHealthSnapshot] | None = None,
    alerts: list[OperationalAlert] | None = None,
    metrics: list[OperationalMetric] | None = None,
    operator_controls: OperatorControlState | None = None,
    resume_readiness: ResumeReadiness | None = None,
) -> dict[str, Path]:
    """Write broker execution, safety, and reconciliation reports."""

    incidents = incidents or []
    health_snapshots = health_snapshots or []
    alerts = alerts or []
    metrics = metrics or []
    output_dir.mkdir(parents=True, exist_ok=True)
    orders_frame = _orders_frame(orders)
    anomalies_frame = _anomalies_frame(anomalies)
    incidents_frame = _incidents_frame(incidents)
    health_history_frame = _health_history_frame(health_snapshots)
    alerts_frame = _alerts_frame(alerts)
    metrics_frame = _metrics_frame(metrics)
    guardrails_frame = _live_guardrails_frame(orders)
    daily_frame = _daily_execution_frame(orders)
    symbol_frame = _symbol_execution_frame(orders)
    lifecycle_frame = _lifecycle_frame(orders)
    rejection_frame = _rejection_frame(orders)
    manual_frame = _manual_intervention_frame(orders, anomalies, incidents)
    recovery_frame = _restart_recovery_frame(orders, anomalies, incidents, health_snapshots)
    reliability_frame = _broker_reliability_frame(health_snapshots, alerts, metrics)
    long_term_reliability = build_reliability_summary(health_snapshots, metrics, alerts, incidents, orders, anomalies)
    daily_ops_frame = _daily_operational_frame(metrics, alerts, incidents)
    alert_aging_frame = _alert_aging_frame(alerts)
    unresolved_incident_frame = _unresolved_incident_frame(incidents)
    operator_control_frame = _operator_control_frame(operator_controls)
    resume_frame = _resume_readiness_frame(resume_readiness)
    health_payload = _health_payload(account_state, orders, anomalies, health_snapshots)
    summary = _summary(orders, anomalies, incidents)
    outputs = {
        "summary": output_dir / "summary.md",
        "summary_json": output_dir / "summary.json",
        "broker_orders": output_dir / "broker_orders.csv",
        "reconciliation_anomalies": output_dir / "reconciliation_anomalies.csv",
        "incident_report": output_dir / "incident_report.csv",
        "incident_report_json": output_dir / "incident_report.json",
        "incident_report_summary": output_dir / "incident_report.md",
        "health_history": output_dir / "broker_health_history.csv",
        "operational_metrics": output_dir / "operational_metrics.csv",
        "alert_summary": output_dir / "alert_summary.csv",
        "alert_summary_json": output_dir / "alert_summary.json",
        "alert_summary_md": output_dir / "alert_summary.md",
        "live_guardrails": output_dir / "live_guardrails.csv",
        "daily_execution": output_dir / "daily_execution.csv",
        "per_symbol": output_dir / "per_symbol.csv",
        "order_lifecycle": output_dir / "order_lifecycle.csv",
        "rejections": output_dir / "rejections.csv",
        "manual_intervention": output_dir / "manual_intervention.csv",
        "restart_recovery": output_dir / "restart_recovery.csv",
        "restart_recovery_summary": output_dir / "restart_recovery.md",
        "broker_reliability": output_dir / "broker_reliability.csv",
        "broker_reliability_summary": output_dir / "broker_reliability.md",
        "long_term_reliability": output_dir / "long_term_reliability.json",
        "long_term_reliability_summary": output_dir / "long_term_reliability.md",
        "alert_aging": output_dir / "alert_aging.csv",
        "unresolved_incidents": output_dir / "unresolved_incidents.csv",
        "operator_controls": output_dir / "operator_controls.csv",
        "resume_readiness_csv": output_dir / "resume_readiness.csv",
        "resume_readiness": output_dir / "resume_readiness.md",
        "daily_operational": output_dir / "daily_operational_summary.csv",
        "daily_operational_summary": output_dir / "daily_operational_summary.md",
        "broker_health": output_dir / "broker_health.json",
        "broker_health_summary": output_dir / "broker_health.md",
    }
    orders_frame.to_csv(outputs["broker_orders"], index=False)
    anomalies_frame.to_csv(outputs["reconciliation_anomalies"], index=False)
    incidents_frame.to_csv(outputs["incident_report"], index=False)
    outputs["incident_report_json"].write_text(json.dumps([incident.model_dump(mode="json") for incident in incidents], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["incident_report_summary"].write_text(_incidents_markdown(incidents), encoding="utf-8")
    health_history_frame.to_csv(outputs["health_history"], index=False)
    metrics_frame.to_csv(outputs["operational_metrics"], index=False)
    alerts_frame.to_csv(outputs["alert_summary"], index=False)
    outputs["alert_summary_json"].write_text(json.dumps([alert.model_dump(mode="json") for alert in alerts], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["alert_summary_md"].write_text(_alerts_markdown(alerts), encoding="utf-8")
    guardrails_frame.to_csv(outputs["live_guardrails"], index=False)
    daily_frame.to_csv(outputs["daily_execution"], index=False)
    symbol_frame.to_csv(outputs["per_symbol"], index=False)
    lifecycle_frame.to_csv(outputs["order_lifecycle"], index=False)
    rejection_frame.to_csv(outputs["rejections"], index=False)
    manual_frame.to_csv(outputs["manual_intervention"], index=False)
    recovery_frame.to_csv(outputs["restart_recovery"], index=False)
    outputs["restart_recovery_summary"].write_text(_restart_recovery_markdown(recovery_frame), encoding="utf-8")
    reliability_frame.to_csv(outputs["broker_reliability"], index=False)
    outputs["broker_reliability_summary"].write_text(_broker_reliability_markdown(reliability_frame), encoding="utf-8")
    outputs["long_term_reliability"].write_text(json.dumps(long_term_reliability.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["long_term_reliability_summary"].write_text(_long_term_reliability_markdown(long_term_reliability), encoding="utf-8")
    alert_aging_frame.to_csv(outputs["alert_aging"], index=False)
    unresolved_incident_frame.to_csv(outputs["unresolved_incidents"], index=False)
    operator_control_frame.to_csv(outputs["operator_controls"], index=False)
    resume_frame.to_csv(outputs["resume_readiness_csv"], index=False)
    outputs["resume_readiness"].write_text(_resume_readiness_markdown(resume_readiness), encoding="utf-8")
    daily_ops_frame.to_csv(outputs["daily_operational"], index=False)
    outputs["daily_operational_summary"].write_text(_daily_operational_markdown(daily_ops_frame), encoding="utf-8")
    outputs["broker_health"].write_text(json.dumps(health_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["broker_health_summary"].write_text(_health_markdown(health_payload), encoding="utf-8")
    outputs["summary_json"].write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["summary"].write_text(_summary_markdown(summary), encoding="utf-8")
    return outputs


def _orders_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order_id": order.order_id,
                "broker_name": order.broker_name,
                "broker_mode": order.broker_mode,
                "broker_order_id": order.broker_order_id,
                "broker_position_id": order.broker_position_id,
                "broker_state": order.broker_state.value if order.broker_state else "",
                "status": order.status.value,
                "symbol": order.request.symbol,
                "direction": order.request.direction.value,
                "setup_subtype": order.request.setup_subtype.value,
                "source_status": order.request.source_status,
                "filled_quantity": order.filled_quantity,
                "average_fill_price": order.average_fill_price,
                "reconciliation_status": order.reconciliation_status,
                "reconciliation_reason": order.reconciliation_reason,
                "created_at": order.created_at.isoformat(),
            }
            for order in orders
        ]
    )


def _anomalies_frame(anomalies: list[ReconciliationAnomaly]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "anomaly_id": anomaly.anomaly_id,
                "detected_at": anomaly.detected_at.isoformat(),
                "type": anomaly.anomaly_type.value,
                "severity": anomaly.severity,
                "symbol": anomaly.symbol,
                "internal_order_id": anomaly.internal_order_id,
                "broker_order_id": anomaly.broker_order_id,
                "broker_position_id": anomaly.broker_position_id,
                "reason": anomaly.reason,
                "payload": json.dumps(anomaly.payload, sort_keys=True),
            }
            for anomaly in anomalies
        ]
    )


def _live_guardrails_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = []
    for order in orders:
        for event in order.events:
            if event.event_type in {TradeEventType.BROKER_VALIDATION_FAILED, TradeEventType.LIVE_GUARDRAIL_TRIGGERED, TradeEventType.RECONCILIATION_MISMATCH}:
                rows.append(
                    {
                        "order_id": order.order_id,
                        "event_type": event.event_type.value,
                        "occurred_at": event.occurred_at.isoformat(),
                        "symbol": order.request.symbol,
                        "reason": event.reason,
                        "payload": json.dumps(event.payload, sort_keys=True),
                    }
                )
    return pd.DataFrame(rows)


def _daily_execution_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = [
        {
            "created_at": order.created_at,
            "submitted": _has_state(order, BrokerOrderState.SUBMITTED),
            "filled": _has_state(order, BrokerOrderState.FILLED),
            "rejected": _has_state(order, BrokerOrderState.REJECTED),
            "closed": _has_state(order, BrokerOrderState.CLOSED),
        }
        for order in orders
    ]
    if not rows:
        return pd.DataFrame(columns=["day", "orders", "submitted", "filled", "rejected", "closed"])
    frame = pd.DataFrame(rows)
    frame["day"] = pd.to_datetime(frame["created_at"], utc=True).dt.date.astype(str)
    return frame.groupby("day", dropna=False).agg(
        orders=("submitted", "count"),
        submitted=("submitted", "sum"),
        filled=("filled", "sum"),
        rejected=("rejected", "sum"),
        closed=("closed", "sum"),
    ).reset_index()


def _symbol_execution_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = [
        {
            "symbol": order.request.symbol,
            "submitted": _has_state(order, BrokerOrderState.SUBMITTED),
            "filled": _has_state(order, BrokerOrderState.FILLED),
            "rejected": _has_state(order, BrokerOrderState.REJECTED),
        }
        for order in orders
    ]
    if not rows:
        return pd.DataFrame(columns=["symbol", "orders", "submitted", "filled", "rejected"])
    frame = pd.DataFrame(rows)
    return frame.groupby("symbol", dropna=False).agg(
        orders=("submitted", "count"),
        submitted=("submitted", "sum"),
        filled=("filled", "sum"),
        rejected=("rejected", "sum"),
    ).reset_index()


def _summary(orders: list[ExecutionOrder], anomalies: list[ReconciliationAnomaly], incidents: list[BrokerIncident]) -> dict[str, int]:
    return {
        "broker_orders": len(orders),
        "submitted": sum(1 for order in orders if _has_state(order, BrokerOrderState.SUBMITTED)),
        "acknowledged": sum(1 for order in orders if _has_state(order, BrokerOrderState.ACKNOWLEDGED)),
        "filled": sum(1 for order in orders if _has_state(order, BrokerOrderState.FILLED)),
        "rejected": sum(1 for order in orders if _has_state(order, BrokerOrderState.REJECTED)),
        "closed": sum(1 for order in orders if _has_state(order, BrokerOrderState.CLOSED)),
        "reconciliation_anomalies": len(anomalies),
        "blocking_anomalies": sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"}),
        "manual_intervention_required": sum(1 for order in orders if _has_state(order, BrokerOrderState.MANUAL_INTERVENTION_REQUIRED)),
        "open_incidents": sum(1 for incident in incidents if incident.status.value == "open"),
        "blocking_incidents": sum(1 for incident in incidents if incident.blocks_execution),
    }


def _summary_markdown(summary: dict[str, int]) -> str:
    return "\n".join(
        [
            "# Broker Execution Report",
            "",
            f"Broker orders: {summary['broker_orders']}",
            f"Submitted: {summary['submitted']}",
            f"Acknowledged: {summary['acknowledged']}",
            f"Filled: {summary['filled']}",
            f"Rejected: {summary['rejected']}",
            f"Closed: {summary['closed']}",
            f"Reconciliation anomalies: {summary['reconciliation_anomalies']}",
            f"Blocking anomalies: {summary['blocking_anomalies']}",
            f"Manual intervention required: {summary['manual_intervention_required']}",
            f"Open incidents: {summary['open_incidents']}",
            f"Blocking incidents: {summary['blocking_incidents']}",
            "",
        ]
    )


def _has_state(order: ExecutionOrder, state: BrokerOrderState) -> bool:
    return any(transition.state == state for transition in order.broker_transitions)


def _lifecycle_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = []
    for order in orders:
        for transition in order.broker_transitions:
            rows.append(
                {
                    "order_id": order.order_id,
                    "broker_order_id": order.broker_order_id,
                    "symbol": order.request.symbol,
                    "state": transition.state.value,
                    "occurred_at": transition.occurred_at.isoformat(),
                    "reason": transition.reason,
                    "payload": json.dumps(transition.payload, sort_keys=True),
                }
            )
    return pd.DataFrame(rows)


def _rejection_frame(orders: list[ExecutionOrder]) -> pd.DataFrame:
    rows = []
    for order in orders:
        for event in order.events:
            if event.event_type in {TradeEventType.BROKER_REJECTED, TradeEventType.BROKER_VALIDATION_FAILED, TradeEventType.BROKER_RETRY_EXHAUSTED}:
                rows.append(
                    {
                        "order_id": order.order_id,
                        "symbol": order.request.symbol,
                        "event_type": event.event_type.value,
                        "occurred_at": event.occurred_at.isoformat(),
                        "reason": event.reason,
                        "payload": json.dumps(event.payload, sort_keys=True),
                    }
                )
    return pd.DataFrame(rows)


def _manual_intervention_frame(orders: list[ExecutionOrder], anomalies: list[ReconciliationAnomaly], incidents: list[BrokerIncident]) -> pd.DataFrame:
    rows = []
    for order in orders:
        for event in order.events:
            if event.event_type == TradeEventType.MANUAL_INTERVENTION_REQUIRED:
                rows.append(
                    {
                        "source": "event",
                        "order_id": order.order_id,
                        "symbol": order.request.symbol,
                        "reason": event.reason,
                        "occurred_at": event.occurred_at.isoformat(),
                    }
                )
    for anomaly in anomalies:
        if anomaly.severity in {"high", "critical"}:
            rows.append(
                {
                    "source": "reconciliation",
                    "order_id": anomaly.internal_order_id,
                    "symbol": anomaly.symbol,
                    "reason": anomaly.reason,
                    "occurred_at": anomaly.detected_at.isoformat(),
                }
            )
    for incident in incidents:
        if incident.blocks_execution:
            rows.append(
                {
                    "source": "incident",
                    "order_id": incident.order_id,
                    "symbol": incident.symbol,
                    "reason": f"{incident.category.value}: {incident.recommendation}",
                    "occurred_at": incident.opened_at.isoformat(),
                }
            )
    return pd.DataFrame(rows)


def _health_payload(
    account_state: BrokerAccountState | None,
    orders: list[ExecutionOrder],
    anomalies: list[ReconciliationAnomaly],
    health_snapshots: list[BrokerHealthSnapshot],
) -> dict[str, object]:
    latest_snapshot = health_snapshots[-1] if health_snapshots else None
    if account_state is None:
        if latest_snapshot is not None:
            return {
                "broker": latest_snapshot.broker,
                "mode": latest_snapshot.mode,
                "connected": latest_snapshot.connected,
                "can_trade": latest_snapshot.can_trade,
                "health_status": latest_snapshot.health_status,
                "retrieved_at": latest_snapshot.created_at.isoformat(),
                "last_error": latest_snapshot.last_error,
                "error_category": latest_snapshot.error_category,
                "consecutive_failures": latest_snapshot.consecutive_failures,
                "open_positions": latest_snapshot.open_positions,
                "pending_orders": latest_snapshot.pending_orders,
                "blocking_anomalies": sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"}),
                "orders": len(orders),
                "degraded_flags": latest_snapshot.degraded_flags,
                "last_successful_account_sync_at": latest_snapshot.last_successful_account_sync_at.isoformat() if latest_snapshot.last_successful_account_sync_at else None,
                "last_successful_position_sync_at": latest_snapshot.last_successful_position_sync_at.isoformat() if latest_snapshot.last_successful_position_sync_at else None,
                "last_successful_reconciliation_at": latest_snapshot.last_successful_reconciliation_at.isoformat() if latest_snapshot.last_successful_reconciliation_at else None,
                "kill_switch_active": latest_snapshot.kill_switch_active,
                "live_capability_enabled": latest_snapshot.live_capability_enabled,
                "active_incidents": latest_snapshot.active_incidents,
                "last_successful_broker_action_at": latest_snapshot.last_successful_broker_action_at.isoformat() if latest_snapshot.last_successful_broker_action_at else None,
                "last_failed_broker_action_at": latest_snapshot.last_failed_broker_action_at.isoformat() if latest_snapshot.last_failed_broker_action_at else None,
            }
        return {
            "broker": "unknown",
            "mode": "unknown",
            "connected": False,
            "can_trade": False,
            "health_status": "not_checked",
            "retrieved_at": None,
            "last_error": None,
            "error_category": None,
            "consecutive_failures": 0,
            "open_positions": 0,
            "pending_orders": 0,
            "blocking_anomalies": sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"}),
            "orders": len(orders),
            "degraded_flags": latest_snapshot.degraded_flags if latest_snapshot else [],
            "last_successful_account_sync_at": latest_snapshot.last_successful_account_sync_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_account_sync_at else None,
            "last_successful_position_sync_at": latest_snapshot.last_successful_position_sync_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_position_sync_at else None,
            "last_successful_reconciliation_at": latest_snapshot.last_successful_reconciliation_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_reconciliation_at else None,
            "kill_switch_active": latest_snapshot.kill_switch_active if latest_snapshot else False,
            "live_capability_enabled": latest_snapshot.live_capability_enabled if latest_snapshot else False,
            "active_incidents": latest_snapshot.active_incidents if latest_snapshot else 0,
            "last_successful_broker_action_at": latest_snapshot.last_successful_broker_action_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_broker_action_at else None,
            "last_failed_broker_action_at": latest_snapshot.last_failed_broker_action_at.isoformat() if latest_snapshot and latest_snapshot.last_failed_broker_action_at else None,
        }
    return {
        "broker": account_state.broker,
        "mode": account_state.mode,
        "connected": account_state.connected,
        "can_trade": account_state.can_trade,
        "health_status": account_state.health_status,
        "retrieved_at": account_state.retrieved_at.isoformat() if account_state.retrieved_at else None,
        "last_error": account_state.last_error,
        "error_category": account_state.error_category.value if account_state.error_category else None,
        "consecutive_failures": account_state.consecutive_failures,
        "open_positions": account_state.open_positions,
        "pending_orders": account_state.pending_orders,
        "blocking_anomalies": sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"}),
        "orders": len(orders),
        "degraded_flags": latest_snapshot.degraded_flags if latest_snapshot else [],
        "last_successful_account_sync_at": latest_snapshot.last_successful_account_sync_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_account_sync_at else None,
        "last_successful_position_sync_at": latest_snapshot.last_successful_position_sync_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_position_sync_at else None,
        "last_successful_reconciliation_at": latest_snapshot.last_successful_reconciliation_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_reconciliation_at else None,
        "kill_switch_active": latest_snapshot.kill_switch_active if latest_snapshot else False,
        "live_capability_enabled": latest_snapshot.live_capability_enabled if latest_snapshot else False,
        "active_incidents": latest_snapshot.active_incidents if latest_snapshot else 0,
        "last_successful_broker_action_at": latest_snapshot.last_successful_broker_action_at.isoformat() if latest_snapshot and latest_snapshot.last_successful_broker_action_at else None,
        "last_failed_broker_action_at": latest_snapshot.last_failed_broker_action_at.isoformat() if latest_snapshot and latest_snapshot.last_failed_broker_action_at else None,
    }


def _health_markdown(payload: dict[str, object]) -> str:
    return "\n".join(
        [
            "# Broker Health Report",
            "",
            f"Broker: {payload['broker']}",
            f"Mode: {payload['mode']}",
            f"Connected: {payload['connected']}",
            f"Can trade: {payload['can_trade']}",
            f"Health: {payload['health_status']}",
            f"Consecutive failures: {payload['consecutive_failures']}",
            f"Blocking anomalies: {payload['blocking_anomalies']}",
            f"Degraded flags: {', '.join(payload['degraded_flags']) if payload['degraded_flags'] else 'none'}",
            f"Last account sync: {payload['last_successful_account_sync_at']}",
            f"Last position sync: {payload['last_successful_position_sync_at']}",
            f"Last reconciliation: {payload['last_successful_reconciliation_at']}",
            f"Kill switch active: {payload['kill_switch_active']}",
            f"Live capability enabled: {payload['live_capability_enabled']}",
            f"Active incidents: {payload['active_incidents']}",
            f"Last successful broker action: {payload['last_successful_broker_action_at']}",
            f"Last failed broker action: {payload['last_failed_broker_action_at']}",
            f"Last error: {payload['last_error']}",
            "",
        ]
    )


def _incidents_frame(incidents: list[BrokerIncident]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "incident_id": incident.incident_id,
                "opened_at": incident.opened_at.isoformat(),
                "updated_at": (incident.updated_at or incident.opened_at).isoformat(),
                "closed_at": incident.closed_at.isoformat() if incident.closed_at else "",
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else "",
                "category": incident.category.value,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "symbol": incident.symbol,
                "order_id": incident.order_id,
                "broker_order_id": incident.broker_order_id,
                "blocks_execution": incident.blocks_execution,
                "reason": incident.reason,
                "recommendation": incident.recommendation,
                "payload": json.dumps(incident.payload, sort_keys=True),
            }
            for incident in incidents
        ]
    )


def _health_history_frame(snapshots: list[BrokerHealthSnapshot]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "snapshot_id": snapshot.snapshot_id,
                "created_at": snapshot.created_at.isoformat(),
                "broker": snapshot.broker,
                "mode": snapshot.mode,
                "connected": snapshot.connected,
                "can_trade": snapshot.can_trade,
                "health_status": snapshot.health_status,
                "degraded_flags": ",".join(snapshot.degraded_flags),
                "last_successful_account_sync_at": snapshot.last_successful_account_sync_at.isoformat() if snapshot.last_successful_account_sync_at else "",
                "last_successful_position_sync_at": snapshot.last_successful_position_sync_at.isoformat() if snapshot.last_successful_position_sync_at else "",
                "last_successful_reconciliation_at": snapshot.last_successful_reconciliation_at.isoformat() if snapshot.last_successful_reconciliation_at else "",
                "consecutive_failures": snapshot.consecutive_failures,
                "blocking_incidents": snapshot.blocking_incidents,
                "manual_intervention_required": snapshot.manual_intervention_required,
                "kill_switch_active": snapshot.kill_switch_active,
                "live_capability_enabled": snapshot.live_capability_enabled,
                "active_incidents": snapshot.active_incidents,
                "open_reconciliation_anomalies": snapshot.open_reconciliation_anomalies,
                "last_successful_broker_action_at": snapshot.last_successful_broker_action_at.isoformat() if snapshot.last_successful_broker_action_at else "",
                "last_failed_broker_action_at": snapshot.last_failed_broker_action_at.isoformat() if snapshot.last_failed_broker_action_at else "",
            }
            for snapshot in snapshots
        ]
    )


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
                "suppression_until": alert.suppression_until.isoformat() if alert.suppression_until else "",
                "reason": alert.reason,
                "recommendation": alert.recommendation,
                "linked_incident_ids": ",".join(alert.linked_incident_ids),
                "linked_anomaly_ids": ",".join(alert.linked_anomaly_ids),
                "linked_order_ids": ",".join(alert.linked_order_ids),
            }
            for alert in alerts
        ]
    )


def _metrics_frame(metrics: list[OperationalMetric]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "metric_id": metric.metric_id,
                "recorded_at": metric.recorded_at.isoformat(),
                "name": metric.name,
                "value": metric.value,
                "status": metric.status,
                "broker": metric.broker,
                "mode": metric.mode,
                "dimensions": json.dumps(metric.dimensions, sort_keys=True),
            }
            for metric in metrics
        ]
    )


def _restart_recovery_frame(
    orders: list[ExecutionOrder],
    anomalies: list[ReconciliationAnomaly],
    incidents: list[BrokerIncident],
    health_snapshots: list[BrokerHealthSnapshot],
) -> pd.DataFrame:
    latest = health_snapshots[-1] if health_snapshots else None
    return pd.DataFrame(
        [
            {
                "checked_at": latest.created_at.isoformat() if latest else "",
                "tracked_orders": len(orders),
                "open_local_orders": sum(1 for order in orders if order.is_open),
                "anomalies": len(anomalies),
                "blocking_anomalies": sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"}),
                "open_incidents": sum(1 for incident in incidents if incident.status.value == "open"),
                "blocking_incidents": sum(1 for incident in incidents if incident.blocks_execution),
                "health_status": latest.health_status if latest else "not_checked",
                "degraded_flags": ",".join(latest.degraded_flags) if latest else "",
            }
        ]
    )


def _incidents_markdown(incidents: list[BrokerIncident]) -> str:
    blocking = [incident for incident in incidents if incident.blocks_execution]
    lines = [
        "# Broker Incident Report",
        "",
        f"Open incidents: {sum(1 for incident in incidents if incident.status.value == 'open')}",
        f"Blocking incidents: {len(blocking)}",
        "",
    ]
    for incident in sorted(incidents, key=lambda item: (item.severity.value, item.opened_at.isoformat()), reverse=True)[:20]:
        lines.extend(
            [
                f"## {incident.severity.value.upper()} - {incident.category.value}",
                f"Reason: {incident.reason}",
                f"Recommendation: {incident.recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def _alerts_markdown(alerts: list[OperationalAlert]) -> str:
    active = [alert for alert in alerts if alert.status.value == "active"]
    critical = [alert for alert in active if alert.severity.value == "critical"]
    lines = [
        "# Operational Alert Summary",
        "",
        f"Alerts: {len(alerts)}",
        f"Active: {len(active)}",
        f"Critical active: {len(critical)}",
        "",
    ]
    for alert in sorted(active, key=lambda item: (item.severity.value, item.updated_at.isoformat()), reverse=True)[:20]:
        lines.extend(
            [
                f"## {alert.severity.value.upper()} - {alert.category.value}",
                f"Reason: {alert.reason}",
                f"Recommendation: {alert.recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def _restart_recovery_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "# Restart Recovery Report\n\nNo recovery data available.\n"
    row = frame.iloc[0].to_dict()
    return "\n".join(
        [
            "# Restart Recovery Report",
            "",
            f"Checked at: {row['checked_at']}",
            f"Health: {row['health_status']}",
            f"Tracked orders: {row['tracked_orders']}",
            f"Open local orders: {row['open_local_orders']}",
            f"Anomalies: {row['anomalies']}",
            f"Blocking anomalies: {row['blocking_anomalies']}",
            f"Open incidents: {row['open_incidents']}",
            f"Blocking incidents: {row['blocking_incidents']}",
            f"Degraded flags: {row['degraded_flags'] or 'none'}",
            "",
        ]
    )


def _broker_reliability_frame(
    snapshots: list[BrokerHealthSnapshot],
    alerts: list[OperationalAlert],
    metrics: list[OperationalMetric],
) -> pd.DataFrame:
    total = len(snapshots)
    connected = sum(1 for snapshot in snapshots if snapshot.connected)
    healthy = sum(1 for snapshot in snapshots if snapshot.health_status == "healthy")
    retry_exhausted = sum(metric.value for metric in metrics if metric.name == "retry_exhausted")
    return pd.DataFrame(
        [
            {
                "snapshots": total,
                "connected_pct": round(connected / total * 100.0, 2) if total else 0.0,
                "healthy_pct": round(healthy / total * 100.0, 2) if total else 0.0,
                "active_alerts": sum(1 for alert in alerts if alert.status.value == "active"),
                "critical_alerts": sum(1 for alert in alerts if alert.status.value == "active" and alert.severity.value == "critical"),
                "retry_exhausted": retry_exhausted,
            }
        ]
    )


def _daily_operational_frame(
    metrics: list[OperationalMetric],
    alerts: list[OperationalAlert],
    incidents: list[BrokerIncident],
) -> pd.DataFrame:
    metric_rows = [{"day": metric.recorded_at.date().isoformat(), "metrics": 1, "alerts": 0, "incidents": 0} for metric in metrics]
    alert_rows = [{"day": alert.opened_at.date().isoformat(), "metrics": 0, "alerts": 1, "incidents": 0} for alert in alerts]
    incident_rows = [{"day": incident.opened_at.date().isoformat(), "metrics": 0, "alerts": 0, "incidents": 1} for incident in incidents]
    rows = [*metric_rows, *alert_rows, *incident_rows]
    if not rows:
        return pd.DataFrame(columns=["day", "metrics", "alerts", "incidents"])
    frame = pd.DataFrame(rows)
    return frame.groupby("day", dropna=False).sum(numeric_only=True).reset_index()


def _alert_aging_frame(alerts: list[OperationalAlert]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "alert_id": alert.alert_id,
                "category": alert.category.value,
                "severity": alert.severity.value,
                "status": alert.status.value,
                "opened_at": alert.opened_at.isoformat(),
                "updated_at": alert.updated_at.isoformat(),
                "age_minutes": round(max(0.0, (alert.updated_at - alert.opened_at).total_seconds() / 60.0), 2),
                "reason": alert.reason,
                "recommendation": alert.recommendation,
            }
            for alert in alerts
            if alert.status.value != "resolved"
        ]
    )


def _unresolved_incident_frame(incidents: list[BrokerIncident]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "incident_id": incident.incident_id,
                "category": incident.category.value,
                "severity": incident.severity.value,
                "opened_at": incident.opened_at.isoformat(),
                "updated_at": (incident.updated_at or incident.opened_at).isoformat(),
                "blocks_execution": incident.blocks_execution,
                "reason": incident.reason,
                "recommendation": incident.recommendation,
                "linked_alert_ids": ",".join(incident.linked_alert_ids),
                "linked_anomaly_ids": ",".join(incident.linked_anomaly_ids),
            }
            for incident in incidents
            if incident.status.value == "open"
        ]
    )


def _operator_control_frame(controls: OperatorControlState | None) -> pd.DataFrame:
    if controls is None:
        return pd.DataFrame(columns=["control_id", "updated_at", "broker_submissions_enabled", "live_submissions_enabled", "maintenance_mode", "degraded_mode", "acknowledged_incidents", "reason"])
    return pd.DataFrame(
        [
            {
                "control_id": controls.control_id,
                "updated_at": controls.updated_at.isoformat(),
                "updated_by": controls.updated_by,
                "broker_submissions_enabled": controls.broker_submissions_enabled,
                "live_submissions_enabled": controls.live_submissions_enabled,
                "maintenance_mode": controls.maintenance_mode,
                "degraded_mode": controls.degraded_mode,
                "acknowledged_incidents": len(controls.acknowledged_incident_ids),
                "reason": controls.reason,
            }
        ]
    )


def _resume_readiness_frame(readiness: ResumeReadiness | None) -> pd.DataFrame:
    if readiness is None:
        return pd.DataFrame(columns=["status", "checked_at", "reasons", "required_actions"])
    return pd.DataFrame(
        [
            {
                "status": readiness.status.value,
                "checked_at": readiness.checked_at.isoformat(),
                "broker": readiness.broker,
                "broker_mode": readiness.broker_mode,
                "unresolved_incidents": readiness.unresolved_incidents,
                "active_alerts": readiness.active_alerts,
                "severe_anomalies": readiness.severe_anomalies,
                "reasons": "; ".join(readiness.reasons),
                "required_actions": "; ".join(readiness.required_actions),
            }
        ]
    )


def _broker_reliability_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "# Broker Reliability Report\n\nNo reliability samples available.\n"
    row = frame.iloc[0].to_dict()
    return "\n".join(
        [
            "# Broker Reliability Report",
            "",
            f"Health snapshots: {row['snapshots']}",
            f"Connected samples: {row['connected_pct']}%",
            f"Healthy samples: {row['healthy_pct']}%",
            f"Active alerts: {row['active_alerts']}",
            f"Critical alerts: {row['critical_alerts']}",
            f"Retry exhausted count: {row['retry_exhausted']}",
            "",
        ]
    )


def _daily_operational_markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "# Daily Operational Summary\n\nNo operational samples available.\n"
    lines = ["# Daily Operational Summary", ""]
    for row in frame.to_dict(orient="records"):
        lines.append(f"- {row['day']}: metrics={row['metrics']}, alerts={row['alerts']}, incidents={row['incidents']}")
    lines.append("")
    return "\n".join(lines)


def _long_term_reliability_markdown(summary: ReliabilitySummary) -> str:
    return "\n".join(
        [
            "# Long-Term Reliability Summary",
            "",
            f"Samples: {summary.samples}",
            f"Broker uptime proxy: {summary.broker_uptime_pct}%",
            f"Health success: {summary.health_success_pct}%",
            f"Reconciliation reliability: {summary.reconciliation_reliability_pct}%",
            f"Account sync reliability: {summary.account_sync_reliability_pct}%",
            f"Position sync reliability: {summary.position_sync_reliability_pct}%",
            f"Order submission success: {summary.order_submission_success_rate_pct}%",
            f"Rejection rate: {summary.rejection_rate_pct}%",
            f"Retry exhaustion rate: {summary.retry_exhaustion_rate_pct}%",
            f"Incident rate per sample: {summary.incident_rate_per_sample}",
            f"Guardrail trigger rate per sample: {summary.guardrail_trigger_rate_per_sample}",
            f"Recovery success: {summary.recovery_success_pct}%",
            f"Mean time to detect: {summary.mean_time_to_detect_minutes}",
            f"Mean time to resolve: {summary.mean_time_to_resolve_minutes}",
            "",
        ]
    )


def _resume_readiness_markdown(readiness: ResumeReadiness | None) -> str:
    if readiness is None:
        return "# Resume Readiness\n\nNo broker health snapshot is available yet.\n"
    lines = [
        "# Resume Readiness",
        "",
        f"Status: {readiness.status.value}",
        f"Checked at: {readiness.checked_at.isoformat()}",
        f"Broker: {readiness.broker}",
        f"Mode: {readiness.broker_mode}",
        f"Unresolved incidents: {readiness.unresolved_incidents}",
        f"Active alerts: {readiness.active_alerts}",
        f"Severe anomalies: {readiness.severe_anomalies}",
        "",
        "## Reasons",
    ]
    lines.extend(f"- {reason}" for reason in readiness.reasons or ["none"])
    lines.append("")
    lines.append("## Required Actions")
    lines.extend(f"- {action}" for action in readiness.required_actions or ["none"])
    lines.append("")
    return "\n".join(lines)
