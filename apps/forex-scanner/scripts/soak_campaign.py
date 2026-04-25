"""Manage multi-session supervised soak-validation campaigns."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import AppSettings, load_settings
from app.audit.integrity import AuditSealTrigger
from app.execution.soak import (
    SoakCampaign,
    SoakCampaignStatus,
    SoakAnomaly,
    SoakRun,
    SoakSample,
    aggregate_campaign_reliability,
    analyze_campaign_recurrence,
    assess_campaign_readiness,
    attach_run_to_campaign,
    create_soak_campaign,
    finalize_soak_campaign,
    stop_soak_campaign,
    validate_soak_mode,
)
from app.execution.operations import BrokerIncident, OperationalAlert, OperationalMetric
from app.reporting.soak import generate_soak_campaign_report
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Run the soak campaign CLI."""

    settings = load_settings().model_copy(deep=True)
    parser = argparse.ArgumentParser(description="Manage multi-session supervised soak validation campaigns.")
    parser.add_argument("--db", default=None, help="SQLite database path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create a new campaign.")
    start.add_argument("--name", default=settings.soak.campaign_default_name)
    start.add_argument("--mode", choices=["paper", "broker_sandbox", "broker_live"], default="broker_sandbox")
    start.add_argument("--provider", choices=["mt5", "mock"], default=None)
    start.add_argument("--target-hours", type=float, default=settings.soak.campaign_default_duration_hours)
    start.add_argument("--notes", default=None)
    start.add_argument("--allow-live", action="store_true")

    run_session = subparsers.add_parser("run-session", help="Resume a campaign by running one safe soak session.")
    run_session.add_argument("--campaign-id", default=None)
    run_session.add_argument("--name", default=settings.soak.campaign_default_name)
    run_session.add_argument("--provider", choices=["mt5", "mock"], default=None)
    run_session.add_argument("--duration-minutes", type=float, default=settings.soak.campaign_default_session_minutes)
    run_session.add_argument("--interval-seconds", type=float, default=settings.soak.default_interval_seconds)
    run_session.add_argument("--iterations", type=int, default=None)
    run_session.add_argument("--out", default=settings.soak.output_dir)
    run_session.add_argument("--allow-live", action="store_true")

    attach = subparsers.add_parser("attach-run", help="Attach an existing soak run to a campaign.")
    attach.add_argument("--campaign-id", required=True)
    attach.add_argument("--run-id", required=True)

    stop = subparsers.add_parser("stop", help="Stop a running campaign without final readiness.")
    stop.add_argument("--campaign-id", required=True)
    stop.add_argument("--reason", default=None)

    finalize = subparsers.add_parser("finalize", help="Finalize a campaign and write readiness reports.")
    finalize.add_argument("--campaign-id", required=True)
    finalize.add_argument("--out", default=settings.soak.campaign_output_dir)
    finalize.add_argument("--notes", default=None)

    status = subparsers.add_parser("status", help="Print campaign status.")
    status.add_argument("--campaign-id", default=None)
    status.add_argument("--name", default=settings.soak.campaign_default_name)

    args = parser.parse_args()
    configure_logging()
    database = Database(Path(args.db) if args.db else settings.database_absolute_path)

    if args.command == "start":
        settings.execution.mode = args.mode
        if args.provider:
            settings.broker.provider = args.provider
        validate_soak_mode(args.mode, settings, allow_live=args.allow_live)
        broker = "paper" if args.mode == "paper" else settings.broker.provider
        campaign = create_soak_campaign(args.name, args.mode, broker, args.target_hours * 3600.0, operator_notes=args.notes)
        database.save_soak_campaign(campaign)
        _print_campaign("soak_campaign=start", campaign)
        return

    if args.command == "run-session":
        campaign = _resolve_campaign(database, args.campaign_id, args.name)
        if campaign.status != SoakCampaignStatus.RUNNING:
            raise SystemExit(f"campaign {campaign.campaign_id} is not running")
        validate_soak_mode(campaign.mode, settings, allow_live=args.allow_live)
        run = _run_soak_session(args, settings.database_absolute_path if args.db is None else Path(args.db), campaign)
        updated = attach_run_to_campaign(campaign, run)
        database.save_soak_campaign(updated)
        _print_campaign("soak_campaign=session_complete", updated)
        print(f"attached_run={run.run_id}")
        return

    if args.command == "attach-run":
        campaign = _require_campaign(database, args.campaign_id)
        run = next((item for item in database.load_soak_runs() if item.run_id == args.run_id), None)
        if run is None:
            raise SystemExit(f"soak run {args.run_id} was not found")
        updated = attach_run_to_campaign(campaign, run)
        database.save_soak_campaign(updated)
        _print_campaign("soak_campaign=attach_run", updated)
        return

    if args.command == "stop":
        campaign = _require_campaign(database, args.campaign_id)
        stopped = stop_soak_campaign(campaign, reason=args.reason)
        database.save_soak_campaign(stopped)
        _print_campaign("soak_campaign=stop", stopped)
        return

    if args.command == "finalize":
        campaign = _require_campaign(database, args.campaign_id)
        if args.notes:
            campaign = campaign.model_copy(update={"operator_notes": args.notes})
    finalized, outputs = _finalize_campaign(database, campaign, Path(args.out), settings)
    database.save_soak_campaign(finalized)
    if "soak_campaign" in settings.audit_integrity.auto_seal_triggers:
        database.create_audit_seal(
            trigger_type=AuditSealTrigger.SOAK_CAMPAIGN,
            trigger_id=finalized.campaign_id,
            notes=finalized.operator_notes,
        )
    _print_campaign("soak_campaign=finalize", finalized)
    for name, path in outputs.items():
        print(f"{name}={path}")
        return

    if args.command == "status":
        campaign = _resolve_campaign(database, args.campaign_id, args.name)
        _print_campaign("soak_campaign=status", campaign)


def _run_soak_session(args: argparse.Namespace, db_path: Path, campaign: SoakCampaign) -> SoakRun:
    before = {run.run_id for run in Database(db_path).load_soak_runs()}
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "soak_test.py"),
        "--mode",
        campaign.mode,
        "--duration-minutes",
        str(args.duration_minutes),
        "--interval-seconds",
        str(args.interval_seconds),
        "--db",
        str(db_path),
        "--out",
        args.out,
    ]
    if args.provider or campaign.broker not in {"paper", ""}:
        provider = args.provider or campaign.broker
        if provider != "paper":
            command.extend(["--provider", provider])
    if args.iterations is not None:
        command.extend(["--iterations", str(args.iterations)])
    if args.allow_live:
        command.append("--allow-live")
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    database = Database(db_path)
    new_runs = [run for run in database.load_soak_runs() if run.run_id not in before]
    if not new_runs:
        raise SystemExit("soak session completed but no new run was persisted")
    return sorted(new_runs, key=lambda run: run.started_at)[-1]


def _finalize_campaign(database: Database, campaign: SoakCampaign, output_dir: Path, settings: AppSettings) -> tuple[SoakCampaign, dict[str, Path]]:
    runs = _campaign_runs(database, campaign)
    samples = _campaign_samples(database, campaign, runs)
    started, ended = _campaign_window(campaign, samples)
    incidents = _window_incidents(database.load_broker_incidents(), started, ended)
    alerts = _window_alerts(database.load_operational_alerts(), started, ended)
    anomalies = _campaign_anomalies(database.load_soak_anomalies(), runs, started, ended)
    metrics_history = _window_metrics(database.load_operational_metrics(), started, ended)
    metrics = aggregate_campaign_reliability(campaign, runs, samples, incidents, alerts, metrics_history)
    recurring = analyze_campaign_recurrence(samples, incidents, alerts, anomalies, min_count=settings.soak.campaign_recurring_issue_min_count)
    assessment = assess_campaign_readiness(campaign, metrics, recurring, settings)
    finalized = finalize_soak_campaign(campaign, assessment, samples)
    outputs = generate_soak_campaign_report(finalized, runs, samples, metrics, recurring, assessment, output_dir, incidents=incidents, alerts=alerts, anomalies=anomalies)
    return finalized, outputs


def _campaign_runs(database: Database, campaign: SoakCampaign) -> list[SoakRun]:
    all_runs = database.load_soak_runs()
    if campaign.run_ids:
        ids = set(campaign.run_ids)
        return [run for run in all_runs if run.run_id in ids]
    end = campaign.ended_at or datetime.now(timezone.utc)
    return [run for run in all_runs if run.mode == campaign.mode and run.broker == campaign.broker and _in_window(run.started_at, campaign.started_at, end)]


def _campaign_samples(database: Database, campaign: SoakCampaign, runs: list[SoakRun]) -> list[SoakSample]:
    run_ids = {run.run_id for run in runs} or set(campaign.run_ids)
    return [sample for sample in database.load_soak_samples() if sample.run_id in run_ids]


def _campaign_anomalies(anomalies: list[SoakAnomaly], runs: list[SoakRun], started_at: datetime, ended_at: datetime) -> list[SoakAnomaly]:
    run_ids = {run.run_id for run in runs}
    return [anomaly for anomaly in anomalies if anomaly.run_id in run_ids or _in_window(anomaly.detected_at, started_at, ended_at)]


def _window_incidents(incidents: list[BrokerIncident], started_at: datetime, ended_at: datetime) -> list[BrokerIncident]:
    return [incident for incident in incidents if _in_window(incident.opened_at, started_at, ended_at) or incident.status.value == "open"]


def _window_alerts(alerts: list[OperationalAlert], started_at: datetime, ended_at: datetime) -> list[OperationalAlert]:
    return [alert for alert in alerts if _in_window(alert.opened_at, started_at, ended_at) or alert.status.value != "resolved"]


def _window_metrics(metrics: list[OperationalMetric], started_at: datetime, ended_at: datetime) -> list[OperationalMetric]:
    return [metric for metric in metrics if _in_window(metric.recorded_at, started_at, ended_at)]


def _campaign_window(campaign: SoakCampaign, samples: list[SoakSample]) -> tuple[datetime, datetime]:
    if samples:
        sorted_samples = sorted(samples, key=lambda sample: sample.sampled_at)
        return sorted_samples[0].sampled_at, sorted_samples[-1].sampled_at
    return campaign.started_at, campaign.ended_at or datetime.now(timezone.utc)


def _resolve_campaign(database: Database, campaign_id: str | None, name: str | None) -> SoakCampaign:
    if campaign_id:
        return _require_campaign(database, campaign_id)
    campaign = database.load_running_soak_campaign(name)
    if campaign is None:
        raise SystemExit("no running soak campaign found")
    return campaign


def _require_campaign(database: Database, campaign_id: str) -> SoakCampaign:
    campaign = database.load_soak_campaign(campaign_id)
    if campaign is None:
        raise SystemExit(f"soak campaign {campaign_id} was not found")
    return campaign


def _in_window(timestamp: datetime, started_at: datetime, ended_at: datetime) -> bool:
    normalized = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    return started_at <= normalized <= ended_at


def _print_campaign(prefix: str, campaign: SoakCampaign) -> None:
    readiness = campaign.readiness.value if campaign.readiness else "pending"
    print(
        f"{prefix} campaign_id={campaign.campaign_id} name={campaign.name} "
        f"status={campaign.status.value} mode={campaign.mode} broker={campaign.broker} "
        f"runs={len(campaign.run_ids)} samples={campaign.samples} readiness={readiness}"
    )


if __name__ == "__main__":
    main()
