"""Local supervised broker monitoring loop.

This utility never submits orders. It repeatedly runs the safe recovery path,
persists health/metrics/alerts, and emits operator reports.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.execution.broker import build_execution_adapter
from app.execution.operations import assess_resume_readiness, build_operational_metrics, generate_operational_alerts, merge_operational_incidents, resolve_operational_alerts, resolve_recovered_incidents, run_startup_recovery
from app.reporting.broker import generate_broker_execution_report
from app.reporting.monitoring import write_prometheus_textfile
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run repeated non-submitting broker health/recovery checks."""

    parser = argparse.ArgumentParser(description="Run repeated broker health/recovery checks without submitting orders.")
    parser.add_argument("--mode", choices=["broker_sandbox", "broker_live"], default="broker_sandbox")
    parser.add_argument("--provider", choices=["mt5", "mock"], default=None)
    parser.add_argument("--db", default=None, help="SQLite database path.")
    parser.add_argument("--out", default="reports/broker_monitor", help="Output directory.")
    parser.add_argument("--iterations", type=int, default=3, help="Number of health/recovery checks to run.")
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Delay between checks.")
    parser.add_argument("--allow-live", action="store_true", help="Required for broker_live monitoring.")
    args = parser.parse_args()

    configure_logging()
    settings = load_settings().model_copy(deep=True)
    settings.execution.mode = args.mode
    if args.provider:
        settings.broker.provider = args.provider
    if args.mode == "broker_live" and not args.allow_live:
        raise SystemExit("broker_live monitoring requires --allow-live plus config/env confirmation")
    if args.mode == "broker_live" and settings.broker.provider == "mock":
        raise SystemExit("mock provider is not allowed for broker_live")
    if args.mode == "broker_live" and (not settings.execution_capabilities.broker_live_enabled or not settings.broker.live_enabled):
        raise SystemExit("broker_live monitoring requires live capability and broker.live_enabled config gates")
    if args.mode == "broker_live" and os.getenv(settings.broker.live_confirmation_env) != settings.broker.live_confirmation_value:
        raise SystemExit(f"broker_live monitoring requires {settings.broker.live_confirmation_env}")

    database = Database(Path(args.db) if args.db else settings.database_absolute_path)
    adapter = build_execution_adapter(settings)
    checks = max(1, args.iterations)
    readiness = None
    controls = database.load_operator_controls()
    for index in range(checks):
        result = run_startup_recovery(settings, adapter, database.load_broker_orders())
        previous_incidents = database.load_broker_incidents()
        previous_alerts = database.load_operational_alerts()
        incidents = merge_operational_incidents(previous_incidents, result.incidents)
        incident_resolution = resolve_recovered_incidents(previous_incidents, incidents)
        metrics = build_operational_metrics(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders)
        alerts = generate_operational_alerts(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders, settings, previous_alerts)
        alert_resolution = resolve_operational_alerts(previous_alerts, alerts)
        controls = database.load_operator_controls()
        readiness = assess_resume_readiness(result.snapshot, incidents, [*alerts, *alert_resolution], result.reconciliation_report.anomalies, controls, settings)
        database.save_broker_orders(result.updated_orders)
        database.save_reconciliation_report(result.reconciliation_report)
        database.save_broker_health_snapshot(result.snapshot)
        database.save_broker_incidents([*incidents, *incident_resolution.closed_incidents])
        database.save_operational_metrics(metrics)
        database.save_operational_alerts([*alerts, *alert_resolution])
        database.save_trade_events([*result.events, *incident_resolution.events])
        print(
            "broker_monitor=sample "
            f"index={index + 1}/{checks} health={result.snapshot.health_status} "
            f"incidents={len(incidents)} alerts={len(alerts)} anomalies={len(result.reconciliation_report.anomalies)} readiness={readiness.status.value}"
        )
        if index < checks - 1 and args.interval_seconds > 0.0:
            time.sleep(args.interval_seconds)

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
        metrics_path = write_prometheus_textfile(
            Path(settings.monitoring.metrics_export_path),
            snapshots=database.load_broker_health_snapshots(),
            metrics=database.load_operational_metrics(),
            alerts=database.load_operational_alerts(),
            incidents=database.load_broker_incidents(),
            anomalies=database.load_reconciliation_anomalies(),
            orders=database.load_broker_orders(),
            operator_controls=database.load_operator_controls(),
        )
        print(f"prometheus_metrics={metrics_path}")
    print(f"broker_monitor=ok samples={checks}")
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
