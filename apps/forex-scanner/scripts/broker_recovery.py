"""Run safe broker startup recovery and persist operational diagnostics."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.execution.broker import build_execution_adapter
from app.execution.operator_identity import PermissionAction, require_authenticated_context
from app.execution.operations import assess_resume_readiness, build_operational_metrics, generate_operational_alerts, merge_operational_incidents, resolve_operational_alerts, resolve_recovered_incidents, run_startup_recovery
from app.execution.operator_workflows import OperatorActionResult, OperatorActionType, record_operator_action
from app.reporting.broker import generate_broker_execution_report
from app.reporting.monitoring import write_prometheus_textfile
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run a non-submitting broker recovery pass after restart/interruption."""

    parser = argparse.ArgumentParser(description="Recover and reconcile broker state without submitting orders.")
    parser.add_argument("--mode", choices=["broker_sandbox", "broker_live"], default="broker_sandbox")
    parser.add_argument("--provider", choices=["mt5", "mock"], default=None)
    parser.add_argument("--db", default=None, help="SQLite database path.")
    parser.add_argument("--out", default="reports/broker_recovery", help="Output directory for recovery reports.")
    parser.add_argument("--allow-live", action="store_true", help="Required to run recovery checks in broker_live mode.")
    parser.add_argument("--operator-id", "--operator", dest="operator_id", default=os.getenv("USERNAME", "operator"))
    parser.add_argument("--auth-session-id", default=None)
    args = parser.parse_args()

    configure_logging()
    settings = load_settings().model_copy(deep=True)
    settings.execution.mode = args.mode
    if args.provider:
        settings.broker.provider = args.provider
    if args.mode == "broker_live" and not args.allow_live:
        raise SystemExit("broker_live recovery requires --allow-live plus config/env confirmation")
    if args.mode == "broker_live" and settings.broker.provider == "mock":
        raise SystemExit("mock provider is not allowed for broker_live")
    if args.mode == "broker_live" and not settings.execution_capabilities.broker_live_enabled:
        raise SystemExit("broker_live recovery requires execution_capabilities.broker_live_enabled=true")
    if args.mode == "broker_live" and not settings.broker.live_enabled:
        raise SystemExit("broker_live recovery requires broker.live_enabled=true")
    if args.mode == "broker_live" and os.getenv(settings.broker.live_confirmation_env) != settings.broker.live_confirmation_value:
        raise SystemExit(f"broker_live recovery requires {settings.broker.live_confirmation_env}")

    database = Database(Path(args.db) if args.db else settings.database_absolute_path)
    database.sync_operator_identities(settings)
    if args.mode == "broker_live":
        auth_context, decision = require_authenticated_context(
            database.load_operator_identities(),
            database.load_operator_auth_sessions(),
            operator_id=args.operator_id,
            action=PermissionAction.RUN_BROKER_RECOVERY,
            auth_session_id=args.auth_session_id,
        )
        if auth_context is None:
            denial = record_operator_action(
                operator=args.operator_id,
                action_type=OperatorActionType.OPERATOR_CONTROL_UPDATED,
                mode=settings.execution.mode,
                result=OperatorActionResult.DENIED,
                target_type="broker_recovery",
                reason="; ".join(decision.reasons),
            )
            database.save_operator_actions([denial])
            raise SystemExit("; ".join(decision.reasons))
    adapter = build_execution_adapter(settings)
    result = run_startup_recovery(settings, adapter, database.load_broker_orders())
    previous_incidents = database.load_broker_incidents()
    previous_alerts = database.load_operational_alerts()
    incidents = merge_operational_incidents(previous_incidents, result.incidents)
    resolution = resolve_recovered_incidents(previous_incidents, incidents)
    metrics = build_operational_metrics(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders)
    alerts = generate_operational_alerts(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders, settings, previous_alerts)
    resolved_alerts = resolve_operational_alerts(previous_alerts, alerts)
    controls = database.load_operator_controls()
    readiness = assess_resume_readiness(result.snapshot, incidents, [*alerts, *resolved_alerts], result.reconciliation_report.anomalies, controls, settings)
    database.save_broker_orders(result.updated_orders)
    database.save_reconciliation_report(result.reconciliation_report)
    database.save_broker_health_snapshot(result.snapshot)
    database.save_broker_incidents([*incidents, *resolution.closed_incidents])
    database.save_operational_metrics(metrics)
    database.save_operational_alerts([*alerts, *resolved_alerts])
    database.save_trade_events([*result.events, *resolution.events])
    database.rebuild_trading_journal()
    outputs = generate_broker_execution_report(
        database.load_broker_orders(),
        database.load_reconciliation_anomalies(),
        Path(args.out),
        incidents=database.load_broker_incidents(),
        health_snapshots=database.load_broker_health_snapshots(),
        alerts=database.load_operational_alerts(),
        metrics=database.load_operational_metrics(),
        operator_controls=controls,
        resume_readiness=readiness,
    )
    if settings.monitoring.metrics_export_enabled:
        write_prometheus_textfile(
            Path(settings.monitoring.metrics_export_path),
            snapshots=database.load_broker_health_snapshots(),
            metrics=database.load_operational_metrics(),
            alerts=database.load_operational_alerts(),
            incidents=database.load_broker_incidents(),
            anomalies=database.load_reconciliation_anomalies(),
            orders=database.load_broker_orders(),
            operator_controls=database.load_operator_controls(),
        )
    print(
        "broker_recovery=ok "
        f"mode={settings.execution.mode} provider={settings.broker.provider} "
        f"health={result.snapshot.health_status} incidents={len(incidents)} "
        f"blocking_incidents={sum(1 for incident in incidents if incident.blocks_execution)} "
        f"alerts={len(alerts)} anomalies={len(result.reconciliation_report.anomalies)} readiness={readiness.status.value}"
    )
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
