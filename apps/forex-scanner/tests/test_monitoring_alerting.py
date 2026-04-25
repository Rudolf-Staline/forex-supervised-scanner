"""Tests for dashboard artifacts and metric-driven operational alerting."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradingStyle
from app.execution.models import BrokerOrderState, BrokerTransition, ExecutionOrder, OrderRequest, OrderStatus
from app.execution.operations import AlertCategory, AlertStatus, BrokerHealthSnapshot, BrokerIncident, BrokerIncidentCategory, BrokerIncidentSeverity, BrokerIncidentStatus
from app.execution.reconciliation import ReconciliationAnomaly, ReconciliationAnomalyType
from app.reporting.alerting import evaluate_alert_rules, generate_alert_report, link_alerts_to_incidents, route_alerts


def _snapshot(
    now: datetime,
    *,
    connected: bool,
    health_status: str,
    consecutive_failures: int = 0,
    account_synced_at: datetime | None = None,
    position_synced_at: datetime | None = None,
    reconciliation_at: datetime | None = None,
    kill_switch_active: bool = False,
    degraded_flags: list[str] | None = None,
) -> BrokerHealthSnapshot:
    return BrokerHealthSnapshot(
        snapshot_id=f"snapshot-{now.timestamp()}-{connected}",
        created_at=now,
        broker="mt5",
        mode="broker_sandbox",
        connected=connected,
        can_trade=connected,
        health_status=health_status,
        degraded_flags=degraded_flags or [],
        consecutive_failures=consecutive_failures,
        last_successful_account_sync_at=account_synced_at,
        last_successful_position_sync_at=position_synced_at,
        last_successful_reconciliation_at=reconciliation_at,
        kill_switch_active=kill_switch_active,
    )


def _incident(now: datetime) -> BrokerIncident:
    return BrokerIncident(
        incident_id="incident-broker-down",
        opened_at=now,
        updated_at=now,
        category=BrokerIncidentCategory.BROKER_UNAVAILABLE,
        severity=BrokerIncidentSeverity.HIGH,
        status=BrokerIncidentStatus.OPEN,
        reason="broker unavailable",
        recommendation="restore broker connectivity",
    )


def _anomaly(now: datetime) -> ReconciliationAnomaly:
    return ReconciliationAnomaly(
        anomaly_id="anomaly-severe",
        detected_at=now,
        anomaly_type=ReconciliationAnomalyType.INTERNAL_OPEN_MISSING_AT_BROKER,
        severity="critical",
        symbol="EUR/USD",
        reason="local order is missing at broker",
    )


def test_dashboard_artifact_is_valid_and_covers_operator_panels() -> None:
    dashboard_path = Path("docs/dashboards/forex_scanner_ops_grafana.json")
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    panel_titles = {panel["title"] for panel in dashboard["panels"]}

    assert dashboard["uid"] == "forex-scanner-ops"
    assert "Broker Connectivity" in panel_titles
    assert "Sync Freshness" in panel_titles
    assert "Reconciliation Anomalies" in panel_titles
    assert "Active Alerts" in panel_titles
    assert "Safety State" in panel_titles
    assert "Soak Reliability Proxy" in panel_titles
    assert all("symbol" not in json.dumps(panel) for panel in dashboard["panels"])


def test_alert_rules_trigger_with_incident_and_runbook_linkage(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.monitoring.broker_unavailable_alert_samples = 2
    now = datetime.now(timezone.utc)
    snapshots = [
        _snapshot(now - timedelta(minutes=1), connected=False, health_status="unavailable", consecutive_failures=1),
        _snapshot(now, connected=False, health_status="unavailable", consecutive_failures=2),
    ]

    bundle = evaluate_alert_rules(
        snapshots=snapshots,
        metrics=[],
        previous_alerts=[],
        incidents=[_incident(now)],
        anomalies=[_anomaly(now)],
        orders=[],
        settings=adjusted,
        now=now,
    )

    broker_down = next(alert for alert in bundle.triggered_alerts if alert.category == AlertCategory.BROKER_DOWN)
    severe_reconciliation = next(alert for alert in bundle.triggered_alerts if alert.category == AlertCategory.SEVERE_RECONCILIATION_MISMATCH)
    assert broker_down.status == AlertStatus.ACTIVE
    assert broker_down.linked_incident_ids == ["incident-broker-down"]
    assert broker_down.payload["runbook"] == "docs/runbooks/broker_operations.md#broker-unavailable"
    assert severe_reconciliation.linked_anomaly_ids == ["anomaly-severe"]

    linked_incidents = link_alerts_to_incidents([_incident(now)], bundle.triggered_alerts)
    assert broker_down.alert_id in linked_incidents[0].linked_alert_ids


def test_alert_rules_deduplicate_suppress_and_resolve(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.monitoring.alert_suppression_minutes = 30.0
    adjusted.monitoring.broker_unavailable_alert_samples = 1
    now = datetime.now(timezone.utc)
    bad_snapshot = _snapshot(now, connected=False, health_status="unavailable", consecutive_failures=1)

    first = evaluate_alert_rules(snapshots=[bad_snapshot], metrics=[], previous_alerts=[], incidents=[], anomalies=[], orders=[], settings=adjusted, now=now)
    second = evaluate_alert_rules(
        snapshots=[bad_snapshot],
        metrics=[],
        previous_alerts=first.triggered_alerts,
        incidents=[],
        anomalies=[],
        orders=[],
        settings=adjusted,
        now=now + timedelta(minutes=1),
    )
    suppressed = next(alert for alert in second.triggered_alerts if alert.category == AlertCategory.BROKER_DOWN)

    fresh = now + timedelta(minutes=2)
    good_snapshot = _snapshot(
        fresh,
        connected=True,
        health_status="healthy",
        account_synced_at=fresh,
        position_synced_at=fresh,
        reconciliation_at=fresh,
    )
    resolved = evaluate_alert_rules(
        snapshots=[good_snapshot],
        metrics=[],
        previous_alerts=first.triggered_alerts,
        incidents=[],
        anomalies=[],
        orders=[],
        settings=adjusted,
        now=fresh,
    )

    assert suppressed.status == AlertStatus.SUPPRESSED
    assert suppressed.alert_id == next(alert.alert_id for alert in first.triggered_alerts if alert.category == AlertCategory.BROKER_DOWN)
    assert resolved.triggered_alerts == []
    assert resolved.resolved_alerts
    assert all(alert.status == AlertStatus.RESOLVED for alert in resolved.resolved_alerts)


def test_alert_routing_local_sink_and_webhook_safe_failure(settings, tmp_path, monkeypatch) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.monitoring.broker_unavailable_alert_samples = 1
    adjusted.monitoring.alert_local_sink_path = str(tmp_path / "alerts.jsonl")
    adjusted.monitoring.alert_webhook_enabled = True
    adjusted.monitoring.alert_webhook_url_env = "FOREX_SCANNER_TEST_WEBHOOK_URL"
    monkeypatch.delenv("FOREX_SCANNER_TEST_WEBHOOK_URL", raising=False)
    now = datetime.now(timezone.utc)
    bundle = evaluate_alert_rules(
        snapshots=[_snapshot(now, connected=False, health_status="unavailable", consecutive_failures=1)],
        metrics=[],
        previous_alerts=[],
        incidents=[],
        anomalies=[],
        orders=[],
        settings=adjusted,
        now=now,
    )

    routing = route_alerts(bundle.triggered_alerts, adjusted, now=now)
    sink_lines = Path(adjusted.monitoring.alert_local_sink_path).read_text(encoding="utf-8").splitlines()

    assert sink_lines
    assert any(record.route == "local" and record.status == "sent" for record in routing.records)
    assert any(record.route == "webhook" and record.status == "failed" and "FOREX_SCANNER_TEST_WEBHOOK_URL" in str(record.reason) for record in routing.records)


def test_alert_report_outputs_operator_summary_files(settings, tmp_path) -> None:
    adjusted = settings.model_copy(deep=True)
    now = datetime.now(timezone.utc)
    snapshot = _snapshot(now, connected=False, health_status="unavailable", consecutive_failures=1)
    bundle = evaluate_alert_rules(
        snapshots=[snapshot],
        metrics=[],
        previous_alerts=[],
        incidents=[],
        anomalies=[],
        orders=[],
        settings=adjusted,
        now=now,
    )
    outputs = generate_alert_report(bundle.triggered_alerts, bundle.evaluations, [], tmp_path)

    assert outputs["summary"].exists()
    assert outputs["summary_json"].exists()
    assert outputs["rule_evaluations"].exists()
    assert "Triggered rules" in outputs["summary"].read_text(encoding="utf-8")


def test_alert_rules_cover_live_failure_state(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    now = datetime.now(timezone.utc)
    request = OrderRequest(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.BREAKOUT_CONFIRMATION,
        setup_subtype=SetupSubtype.BREAKOUT_CLOSE,
        direction=DirectionBias.LONG,
        quantity_units=0.01,
        entry_price=1.1,
        stop_loss=1.095,
        take_profit=1.11,
    )
    order = ExecutionOrder(
        order_id="live-order-1",
        request=request,
        status=OrderStatus.REJECTED,
        created_at=now,
        broker_mode="broker_live",
        broker_transitions=[
            BrokerTransition(
                transition_id="transition-1",
                order_id="live-order-1",
                state=BrokerOrderState.REJECTED,
                occurred_at=now,
                reason="broker reject",
            )
        ],
    )

    bundle = evaluate_alert_rules(snapshots=[], metrics=[], previous_alerts=[], incidents=[], anomalies=[], orders=[order], settings=adjusted, now=now)
    failure_alert = next(alert for alert in bundle.triggered_alerts if alert.category == AlertCategory.LIVE_SUBMISSION_FAILURES)
    assert failure_alert.payload["rule_name"] == "excessive_live_submission_failures"
