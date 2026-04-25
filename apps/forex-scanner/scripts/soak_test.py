"""Run prolonged supervised soak validation without submitting broker orders."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import AppSettings, load_settings
from app.execution.broker import build_execution_adapter
from app.execution.models import BrokerAccountState
from app.execution.operations import (
    BrokerIncident,
    BrokerRecoveryResult,
    OperationalAlert,
    assess_resume_readiness,
    build_broker_health_snapshot,
    build_operational_metrics,
    generate_operational_alerts,
    merge_operational_incidents,
    operational_events_from_snapshot_and_incidents,
    resolve_operational_alerts,
    resolve_recovered_incidents,
    run_startup_recovery,
)
from app.execution.reconciliation import ReconciliationReport, reconcile_broker_state
from app.execution.soak import (
    assess_soak_readiness,
    build_soak_sample,
    complete_soak_run,
    compute_soak_reliability,
    create_soak_run,
    detect_soak_anomalies,
    validate_soak_mode,
)
from app.reporting.monitoring import write_prometheus_textfile
from app.reporting.soak import generate_soak_report
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run a safe soak validation loop and emit operator reports."""

    settings = load_settings().model_copy(deep=True)
    parser = argparse.ArgumentParser(description="Run non-submitting soak validation for paper or broker sandbox operations.")
    parser.add_argument("--mode", choices=["paper", "broker_sandbox", "broker_live"], default="broker_sandbox")
    parser.add_argument("--provider", choices=["mt5", "mock"], default=None)
    parser.add_argument("--duration-minutes", type=float, default=settings.soak.default_duration_minutes)
    parser.add_argument("--interval-seconds", type=float, default=settings.soak.default_interval_seconds)
    parser.add_argument("--iterations", type=int, default=None, help="Optional hard cap for validation samples.")
    parser.add_argument("--db", default=None, help="SQLite database path.")
    parser.add_argument("--out", default=settings.soak.output_dir, help="Output directory for soak reports.")
    parser.add_argument("--allow-live", action="store_true", help="Required for broker_live checks; live remains config/env gated.")
    args = parser.parse_args()

    configure_logging()
    settings.execution.mode = args.mode
    if args.provider:
        settings.broker.provider = args.provider
    _validate_args(args, settings)

    database = Database(Path(args.db) if args.db else settings.database_absolute_path)
    adapter = None if args.mode == "paper" else build_execution_adapter(settings)
    duration_seconds = max(1.0, args.duration_minutes * 60.0)
    interval_seconds = max(0.0, args.interval_seconds)
    broker_name = "paper" if args.mode == "paper" else settings.broker.provider
    run = create_soak_run(args.mode, broker_name, duration_seconds, interval_seconds)
    database.save_soak_run(run)
    print(f"soak_test=start run_id={run.run_id} mode={args.mode} provider={broker_name} duration_seconds={duration_seconds} interval_seconds={interval_seconds}")

    sample_index = 0
    deadline = datetime.now(timezone.utc).timestamp() + duration_seconds
    while sample_index < _max_iterations(args.iterations, settings):
        sample_index += 1
        result = _run_one_observation(settings, database, adapter)
        previous_incidents = database.load_broker_incidents()
        previous_alerts = database.load_operational_alerts()
        incidents = merge_operational_incidents(previous_incidents, result.incidents)
        resolution = resolve_recovered_incidents(previous_incidents, incidents)
        metrics = build_operational_metrics(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders)
        alerts = generate_operational_alerts(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders, settings, previous_alerts)
        resolved_alerts = resolve_operational_alerts(previous_alerts, alerts)
        controls = database.load_operator_controls()
        readiness = assess_resume_readiness(result.snapshot, incidents, [*alerts, *resolved_alerts], result.reconciliation_report.anomalies, controls, settings)
        sample = build_soak_sample(run.run_id, sample_index, result.snapshot, [*incidents, *resolution.closed_incidents], [*alerts, *resolved_alerts], metrics, result.reconciliation_report.anomalies, controls, readiness)

        database.save_broker_orders(result.updated_orders)
        database.save_reconciliation_report(result.reconciliation_report)
        database.save_broker_health_snapshot(result.snapshot)
        database.save_broker_incidents([*incidents, *resolution.closed_incidents])
        database.save_operational_metrics(metrics)
        database.save_operational_alerts([*alerts, *resolved_alerts])
        database.save_trade_events([*result.events, *resolution.events])
        database.save_soak_samples([sample])
        print(
            "soak_test=sample "
            f"run_id={run.run_id} index={sample_index} health={sample.health_status} "
            f"connected={sample.connected} incidents={sample.open_incidents} alerts={sample.active_alerts} "
            f"anomalies={sample.reconciliation_anomalies} readiness={sample.resume_readiness}"
        )
        if args.iterations is not None and sample_index >= args.iterations:
            break
        if datetime.now(timezone.utc).timestamp() >= deadline:
            break
        if interval_seconds > 0.0:
            time.sleep(min(interval_seconds, max(0.0, deadline - datetime.now(timezone.utc).timestamp())))

    samples = database.load_soak_samples(run.run_id)
    ended_at = datetime.now(timezone.utc)
    incidents_for_run = _incidents_for_run(database.load_broker_incidents(), run.started_at, ended_at)
    alerts_for_run = _alerts_for_run(database.load_operational_alerts(), run.started_at, ended_at)
    reliability = compute_soak_reliability(run.run_id, samples, incidents_for_run, alerts_for_run)
    soak_anomalies = detect_soak_anomalies(run.run_id, samples, incidents_for_run, alerts_for_run, reliability, settings)
    assessment = assess_soak_readiness(run.run_id, reliability, soak_anomalies, settings)
    completed = complete_soak_run(run, assessment, samples)
    database.save_soak_run(completed)
    database.save_soak_anomalies(soak_anomalies)
    outputs = generate_soak_report(completed, samples, reliability, soak_anomalies, assessment, Path(args.out), incidents=incidents_for_run, alerts=alerts_for_run)
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
    print(f"soak_test=complete run_id={completed.run_id} readiness={assessment.result.value} samples={len(samples)} anomalies={len(soak_anomalies)}")
    for name, path in outputs.items():
        print(f"{name}={path}")


def _validate_args(args: argparse.Namespace, settings: AppSettings) -> None:
    try:
        validate_soak_mode(args.mode, settings, allow_live=args.allow_live)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.mode == "broker_live":
        if settings.broker.provider == "mock":
            raise SystemExit("mock provider is not allowed for broker_live soak checks")
        if os.getenv(settings.broker.live_confirmation_env) != settings.broker.live_confirmation_value:
            raise SystemExit(f"broker_live soak checks require {settings.broker.live_confirmation_env}")
    if args.duration_minutes <= 0.0:
        raise SystemExit("duration-minutes must be positive")
    if args.interval_seconds < 0.0:
        raise SystemExit("interval-seconds cannot be negative")


def _max_iterations(requested: int | None, settings: AppSettings) -> int:
    if requested is None:
        return settings.soak.max_samples
    if requested <= 0:
        raise SystemExit("iterations must be positive when provided")
    return min(requested, settings.soak.max_samples)


def _run_one_observation(settings: AppSettings, database: Database, adapter: object | None) -> BrokerRecoveryResult:
    if settings.execution.mode == "paper":
        return _paper_observation(settings)
    if adapter is None:
        raise RuntimeError("broker observation requires an execution adapter")
    return run_startup_recovery(settings, adapter, database.load_broker_orders())


def _paper_observation(settings: AppSettings) -> BrokerRecoveryResult:
    account = BrokerAccountState(
        broker="paper",
        mode="paper",
        connected=True,
        can_trade=True,
        is_demo=True,
        retrieved_at=datetime.now(timezone.utc),
        health_status="healthy",
    )
    report, updated_orders = reconcile_broker_state([], [], [])
    snapshot = build_broker_health_snapshot(account, updated_orders, report.anomalies, settings, last_reconciliation_at=report.created_at)
    incidents: list[BrokerIncident] = []
    events = operational_events_from_snapshot_and_incidents(snapshot, incidents)
    return BrokerRecoveryResult(account_state=account, snapshot=snapshot, incidents=incidents, reconciliation_report=report, updated_orders=updated_orders, events=events)


def _incidents_for_run(incidents: list[BrokerIncident], started_at: datetime, ended_at: datetime) -> list[BrokerIncident]:
    return [
        incident
        for incident in incidents
        if _in_window(incident.opened_at, started_at, ended_at) or incident.status.value == "open"
    ]


def _alerts_for_run(alerts: list[OperationalAlert], started_at: datetime, ended_at: datetime) -> list[OperationalAlert]:
    return [
        alert
        for alert in alerts
        if _in_window(alert.opened_at, started_at, ended_at) or alert.status.value != "resolved"
    ]


def _in_window(timestamp: datetime, started_at: datetime, ended_at: datetime) -> bool:
    normalized = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    return started_at <= normalized <= ended_at


if __name__ == "__main__":
    main()
