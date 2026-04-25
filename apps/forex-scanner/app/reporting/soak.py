"""Operator-facing reports for supervised soak validation."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.execution.operations import BrokerIncident, OperationalAlert
from app.execution.soak import SoakAnomaly, SoakCampaign, SoakCampaignReadinessAssessment, SoakCampaignRecurringIssue, SoakCampaignReliabilityMetrics, SoakReadinessAssessment, SoakReliabilityMetrics, SoakRun, SoakSample


def generate_soak_report(
    run: SoakRun,
    samples: list[SoakSample],
    metrics: SoakReliabilityMetrics,
    anomalies: list[SoakAnomaly],
    assessment: SoakReadinessAssessment,
    output_dir: Path,
    *,
    incidents: list[BrokerIncident] | None = None,
    alerts: list[OperationalAlert] | None = None,
) -> dict[str, Path]:
    """Write CSV, JSON, and Markdown outputs for a completed soak run."""

    incidents = incidents or []
    alerts = alerts or []
    run_dir = output_dir / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "summary": run_dir / "summary.md",
        "summary_json": run_dir / "summary.json",
        "samples": run_dir / "samples.csv",
        "reliability": run_dir / "reliability.json",
        "reliability_summary": run_dir / "reliability.md",
        "anomalies": run_dir / "anomalies.csv",
        "anomalies_summary": run_dir / "anomalies.md",
        "alert_incident_summary": run_dir / "alert_incident_summary.csv",
        "health_timeline": run_dir / "health_timeline.csv",
        "reconciliation_timeline": run_dir / "reconciliation_timeline.csv",
        "degraded_periods": run_dir / "degraded_periods.csv",
        "unresolved_issues": run_dir / "unresolved_issues.csv",
        "readiness": run_dir / "readiness.md",
        "readiness_json": run_dir / "readiness.json",
    }
    _samples_frame(samples).to_csv(outputs["samples"], index=False)
    _anomalies_frame(anomalies).to_csv(outputs["anomalies"], index=False)
    _alert_incident_frame(alerts, incidents).to_csv(outputs["alert_incident_summary"], index=False)
    _health_timeline_frame(samples).to_csv(outputs["health_timeline"], index=False)
    _reconciliation_timeline_frame(samples).to_csv(outputs["reconciliation_timeline"], index=False)
    _degraded_periods_frame(samples).to_csv(outputs["degraded_periods"], index=False)
    _unresolved_issues_frame(alerts, incidents, anomalies).to_csv(outputs["unresolved_issues"], index=False)
    outputs["summary_json"].write_text(json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["reliability"].write_text(json.dumps(metrics.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["readiness_json"].write_text(json.dumps(assessment.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["summary"].write_text(_summary_markdown(run, assessment, metrics, anomalies), encoding="utf-8")
    outputs["reliability_summary"].write_text(_reliability_markdown(metrics), encoding="utf-8")
    outputs["anomalies_summary"].write_text(_anomalies_markdown(anomalies), encoding="utf-8")
    outputs["readiness"].write_text(_readiness_markdown(assessment), encoding="utf-8")
    return outputs


def generate_soak_campaign_report(
    campaign: SoakCampaign,
    runs: list[SoakRun],
    samples: list[SoakSample],
    metrics: SoakCampaignReliabilityMetrics,
    recurring_issues: list[SoakCampaignRecurringIssue],
    assessment: SoakCampaignReadinessAssessment,
    output_dir: Path,
    *,
    incidents: list[BrokerIncident] | None = None,
    alerts: list[OperationalAlert] | None = None,
    anomalies: list[SoakAnomaly] | None = None,
) -> dict[str, Path]:
    """Write weekly-style campaign reliability and readiness reports."""

    incidents = incidents or []
    alerts = alerts or []
    anomalies = anomalies or []
    campaign_dir = output_dir / campaign.campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "campaign_summary": campaign_dir / "campaign_summary.md",
        "campaign_summary_json": campaign_dir / "campaign_summary.json",
        "weekly_reliability": campaign_dir / "weekly_reliability.md",
        "weekly_reliability_json": campaign_dir / "weekly_reliability.json",
        "readiness": campaign_dir / "readiness.md",
        "readiness_json": campaign_dir / "readiness.json",
        "recurring_anomalies": campaign_dir / "recurring_anomalies.csv",
        "recurring_anomalies_summary": campaign_dir / "recurring_anomalies.md",
        "unresolved_issues": campaign_dir / "unresolved_issues.csv",
        "campaign_timeline": campaign_dir / "campaign_timeline.csv",
        "restart_recovery_events": campaign_dir / "restart_recovery_events.csv",
        "alert_incident_burden": campaign_dir / "alert_incident_burden.csv",
        "runs": campaign_dir / "runs.csv",
        "operator_notes": campaign_dir / "operator_notes.md",
    }
    outputs["campaign_summary_json"].write_text(json.dumps(campaign.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["weekly_reliability_json"].write_text(json.dumps(metrics.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["readiness_json"].write_text(json.dumps(assessment.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["campaign_summary"].write_text(_campaign_summary_markdown(campaign, metrics, assessment), encoding="utf-8")
    outputs["weekly_reliability"].write_text(_weekly_reliability_markdown(metrics), encoding="utf-8")
    outputs["readiness"].write_text(_campaign_readiness_markdown(assessment), encoding="utf-8")
    outputs["recurring_anomalies_summary"].write_text(_recurring_issues_markdown(recurring_issues), encoding="utf-8")
    outputs["operator_notes"].write_text(_operator_notes_markdown(campaign), encoding="utf-8")
    _recurring_issues_frame(recurring_issues).to_csv(outputs["recurring_anomalies"], index=False)
    _campaign_unresolved_frame(alerts, incidents, anomalies, recurring_issues).to_csv(outputs["unresolved_issues"], index=False)
    _campaign_timeline_frame(samples).to_csv(outputs["campaign_timeline"], index=False)
    _restart_recovery_frame(samples).to_csv(outputs["restart_recovery_events"], index=False)
    _campaign_burden_frame(metrics).to_csv(outputs["alert_incident_burden"], index=False)
    _campaign_runs_frame(runs).to_csv(outputs["runs"], index=False)
    return outputs


def _samples_frame(samples: list[SoakSample]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sample_index": sample.sample_index,
                "sampled_at": sample.sampled_at.isoformat(),
                "mode": sample.mode,
                "broker": sample.broker,
                "connected": sample.connected,
                "can_trade": sample.can_trade,
                "health_status": sample.health_status,
                "account_sync_fresh": sample.account_sync_fresh,
                "position_sync_fresh": sample.position_sync_fresh,
                "reconciliation_fresh": sample.reconciliation_fresh,
                "open_incidents": sample.open_incidents,
                "blocking_incidents": sample.blocking_incidents,
                "active_alerts": sample.active_alerts,
                "retry_exhausted": sample.retry_exhausted,
                "broker_rejects": sample.broker_rejects,
                "stale_state_detections": sample.stale_state_detections,
                "degraded_mode": sample.degraded_mode,
                "degraded_flags": ",".join(sample.degraded_flags),
                "kill_switch_active": sample.kill_switch_active,
                "resume_readiness": sample.resume_readiness,
            }
            for sample in samples
        ]
    )


def _anomalies_frame(anomalies: list[SoakAnomaly]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "anomaly_id": anomaly.anomaly_id,
                "run_id": anomaly.run_id,
                "detected_at": anomaly.detected_at.isoformat(),
                "category": anomaly.category.value,
                "severity": anomaly.severity,
                "sample_index": anomaly.sample_index,
                "count": anomaly.count,
                "reason": anomaly.reason,
                "recommendation": anomaly.recommendation,
            }
            for anomaly in anomalies
        ]
    )


def _alert_incident_frame(alerts: list[OperationalAlert], incidents: list[BrokerIncident]) -> pd.DataFrame:
    rows: list[dict[str, str | int | bool | None]] = []
    for alert in alerts:
        rows.append(
            {
                "source": "alert",
                "id": alert.alert_id,
                "category": alert.category.value,
                "severity": alert.severity.value,
                "status": alert.status.value,
                "opened_at": alert.opened_at.isoformat(),
                "blocks_execution": None,
                "reason": alert.reason,
            }
        )
    for incident in incidents:
        rows.append(
            {
                "source": "incident",
                "id": incident.incident_id,
                "category": incident.category.value,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "opened_at": incident.opened_at.isoformat(),
                "blocks_execution": incident.blocks_execution,
                "reason": incident.reason,
            }
        )
    return pd.DataFrame(rows)


def _health_timeline_frame(samples: list[SoakSample]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sample_index": sample.sample_index,
                "sampled_at": sample.sampled_at.isoformat(),
                "connected": sample.connected,
                "can_trade": sample.can_trade,
                "health_status": sample.health_status,
                "degraded_mode": sample.degraded_mode,
                "degraded_flags": ",".join(sample.degraded_flags),
            }
            for sample in samples
        ]
    )


def _reconciliation_timeline_frame(samples: list[SoakSample]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sample_index": sample.sample_index,
                "sampled_at": sample.sampled_at.isoformat(),
                "reconciliation_fresh": sample.reconciliation_fresh,
                "reconciliation_anomalies": sample.reconciliation_anomalies,
                "blocking_reconciliation_anomalies": sample.blocking_reconciliation_anomalies,
            }
            for sample in samples
        ]
    )


def _degraded_periods_frame(samples: list[SoakSample]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sample_index": sample.sample_index,
                "sampled_at": sample.sampled_at.isoformat(),
                "health_status": sample.health_status,
                "degraded_flags": ",".join(sample.degraded_flags),
                "resume_readiness": sample.resume_readiness,
            }
            for sample in samples
            if sample.degraded_mode
        ]
    )


def _unresolved_issues_frame(alerts: list[OperationalAlert], incidents: list[BrokerIncident], anomalies: list[SoakAnomaly]) -> pd.DataFrame:
    rows: list[dict[str, str | int | bool | None]] = []
    rows.extend(
        {
            "source": "alert",
            "id": alert.alert_id,
            "category": alert.category.value,
            "severity": alert.severity.value,
            "status": alert.status.value,
            "reason": alert.reason,
            "recommendation": alert.recommendation,
        }
        for alert in alerts
        if alert.status.value != "resolved"
    )
    rows.extend(
        {
            "source": "incident",
            "id": incident.incident_id,
            "category": incident.category.value,
            "severity": incident.severity.value,
            "status": incident.status.value,
            "reason": incident.reason,
            "recommendation": incident.recommendation,
        }
        for incident in incidents
        if incident.status.value == "open"
    )
    rows.extend(
        {
            "source": "soak_anomaly",
            "id": anomaly.anomaly_id,
            "category": anomaly.category.value,
            "severity": anomaly.severity,
            "status": "open",
            "reason": anomaly.reason,
            "recommendation": anomaly.recommendation,
        }
        for anomaly in anomalies
    )
    return pd.DataFrame(rows)


def _summary_markdown(run: SoakRun, assessment: SoakReadinessAssessment, metrics: SoakReliabilityMetrics, anomalies: list[SoakAnomaly]) -> str:
    return "\n".join(
        [
            "# Soak Validation Summary",
            "",
            f"Run id: {run.run_id}",
            f"Mode: {run.mode}",
            f"Broker: {run.broker}",
            f"Started: {run.started_at.isoformat()}",
            f"Ended: {run.ended_at.isoformat() if run.ended_at else 'running'}",
            f"Samples: {metrics.samples}",
            f"Readiness: {assessment.result.value}",
            f"Connectivity success: {metrics.connectivity_success_rate_pct}%",
            f"Reconciliation success: {metrics.reconciliation_success_rate_pct}%",
            f"Healthy state: {metrics.healthy_state_pct}%",
            f"Anomalies: {len(anomalies)}",
            "",
        ]
    )


def _reliability_markdown(metrics: SoakReliabilityMetrics) -> str:
    return "\n".join(
        [
            "# Soak Reliability Report",
            "",
            f"Samples: {metrics.samples}",
            f"Connectivity success: {metrics.connectivity_success_rate_pct}%",
            f"Account sync success: {metrics.account_sync_success_rate_pct}%",
            f"Position sync success: {metrics.position_sync_success_rate_pct}%",
            f"Reconciliation success: {metrics.reconciliation_success_rate_pct}%",
            f"Retry exhausted: {metrics.retry_exhausted_count}",
            f"Broker rejects: {metrics.broker_reject_count}",
            f"Stale state detections: {metrics.stale_state_detection_count}",
            f"Health flaps: {metrics.health_flap_count}",
            f"Broker unavailable: {metrics.broker_unavailable_pct}%",
            f"Degraded mode: {metrics.degraded_mode_pct}%",
            f"Mean unhealthy interval seconds: {metrics.mean_unhealthy_interval_seconds}",
            f"Mean recovery seconds: {metrics.mean_recovery_seconds}",
            f"Unresolved incidents at end: {metrics.unresolved_incidents_end}",
            f"Unresolved severe incidents at end: {metrics.unresolved_severe_incidents_end}",
            "",
        ]
    )


def _anomalies_markdown(anomalies: list[SoakAnomaly]) -> str:
    if not anomalies:
        return "# Soak Anomaly Summary\n\nNo soak anomalies were detected.\n"
    lines = ["# Soak Anomaly Summary", ""]
    for anomaly in anomalies:
        lines.extend(
            [
                f"## {anomaly.severity.upper()} - {anomaly.category.value}",
                f"Count: {anomaly.count}",
                f"Reason: {anomaly.reason}",
                f"Recommendation: {anomaly.recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def _readiness_markdown(assessment: SoakReadinessAssessment) -> str:
    lines = [
        "# Soak Readiness Recommendation",
        "",
        f"Result: {assessment.result.value}",
        f"Assessed at: {assessment.assessed_at.isoformat()}",
        "",
        "## Fail Reasons",
    ]
    lines.extend(f"- {reason}" for reason in assessment.reasons or ["none"])
    lines.append("")
    lines.append("## Warnings")
    lines.extend(f"- {warning}" for warning in assessment.warnings or ["none"])
    lines.append("")
    lines.append("This is an operator aid only. A pass does not authorize unattended live trading.")
    lines.append("")
    return "\n".join(lines)


def _campaign_summary_markdown(campaign: SoakCampaign, metrics: SoakCampaignReliabilityMetrics, assessment: SoakCampaignReadinessAssessment) -> str:
    return "\n".join(
        [
            "# Soak Campaign Summary",
            "",
            f"Campaign id: {campaign.campaign_id}",
            f"Name: {campaign.name}",
            f"Mode: {campaign.mode}",
            f"Broker: {campaign.broker}",
            f"Status: {campaign.status.value}",
            f"Readiness: {assessment.rating.value}",
            f"Started: {campaign.started_at.isoformat()}",
            f"Ended: {campaign.ended_at.isoformat() if campaign.ended_at else 'running'}",
            f"Target duration hours: {round(campaign.target_duration_seconds / 3600.0, 2)}",
            f"Observed duration hours: {round(metrics.observed_duration_seconds / 3600.0, 2)}",
            f"Runs: {metrics.run_count}",
            f"Samples: {metrics.samples}",
            f"Health success: {metrics.health_success_pct}%",
            f"Reconciliation success: {metrics.reconciliation_success_pct}%",
            f"Alert burden per day: {metrics.alert_burden_per_day}",
            f"Incident burden per day: {metrics.incident_burden_per_day}",
            "",
            "This report is an operator aid. It is not an autonomous approval to trade live.",
            "",
        ]
    )


def _weekly_reliability_markdown(metrics: SoakCampaignReliabilityMetrics) -> str:
    weekly_alert_estimate = round(metrics.alert_burden_per_day * 7.0, 2)
    weekly_incident_estimate = round(metrics.incident_burden_per_day * 7.0, 2)
    return "\n".join(
        [
            "# Weekly-Style Reliability Summary",
            "",
            f"Observed samples: {metrics.samples}",
            f"Observed duration hours: {round(metrics.observed_duration_seconds / 3600.0, 2)}",
            f"Health success: {metrics.health_success_pct}%",
            f"Degraded mode: {metrics.degraded_mode_pct}%",
            f"Broker unavailable: {metrics.broker_unavailable_pct}%",
            f"Account sync success: {metrics.account_sync_success_pct}%",
            f"Position sync success: {metrics.position_sync_success_pct}%",
            f"Reconciliation success: {metrics.reconciliation_success_pct}%",
            f"Retry exhausted: {metrics.retry_exhausted_count}",
            f"Broker rejects: {metrics.broker_reject_count}",
            f"Guardrail triggers: {metrics.live_guardrail_trigger_count}",
            f"Manual intervention events: {metrics.manual_intervention_count}",
            f"Restart/recovery events: {metrics.restart_recovery_events}",
            f"Downtime intervals: {metrics.downtime_intervals}",
            f"Stale sync intervals: {metrics.stale_sync_intervals}",
            f"Reconciliation failure intervals: {metrics.reconciliation_failure_intervals}",
            f"Unresolved alerts at end: {metrics.unresolved_alerts_end}",
            f"Unresolved incidents at end: {metrics.unresolved_incidents_end}",
            f"Weekly alert burden estimate: {weekly_alert_estimate}",
            f"Weekly incident burden estimate: {weekly_incident_estimate}",
            "",
            "## Alert Counts By Severity",
            *_dict_lines(metrics.alert_count_by_severity),
            "",
            "## Incident Counts By Severity",
            *_dict_lines(metrics.incident_count_by_severity),
            "",
        ]
    )


def _campaign_readiness_markdown(assessment: SoakCampaignReadinessAssessment) -> str:
    lines = [
        "# Campaign Readiness Recommendation",
        "",
        f"Rating: {assessment.rating.value}",
        f"Assessed at: {assessment.assessed_at.isoformat()}",
        "",
        "## Key Reasons",
    ]
    lines.extend(f"- {item}" for item in assessment.key_reasons or ["none"])
    lines.extend(["", "## Blocking Issues"])
    lines.extend(f"- {item}" for item in assessment.blocking_issues or ["none"])
    lines.extend(["", "## Warnings"])
    lines.extend(f"- {item}" for item in assessment.warnings or ["none"])
    lines.extend(["", "## Recommended Next Actions"])
    lines.extend(f"- {item}" for item in assessment.recommended_next_actions or ["none"])
    if assessment.suggested_rerun_duration_hours is not None:
        lines.extend(["", f"Suggested rerun duration hours: {assessment.suggested_rerun_duration_hours}"])
    lines.extend(["", "A strong result still requires operator review, fresh recovery, and all broker-live gates before any live-supervised action.", ""])
    return "\n".join(lines)


def _recurring_issues_markdown(issues: list[SoakCampaignRecurringIssue]) -> str:
    if not issues:
        return "# Recurring Anomaly Summary\n\nNo recurring campaign issues met the configured threshold.\n"
    lines = ["# Recurring Anomaly Summary", ""]
    for issue in issues:
        lines.extend(
            [
                f"## {issue.severity.upper()} - {issue.category}",
                f"Count: {issue.count}",
                f"Trend: {issue.trend}",
                f"Cluster: {issue.cluster}",
                f"First seen: {issue.first_seen.isoformat()}",
                f"Last seen: {issue.last_seen.isoformat()}",
                f"Recommendation: {issue.recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def _operator_notes_markdown(campaign: SoakCampaign) -> str:
    return "\n".join(
        [
            "# Operator Notes",
            "",
            campaign.operator_notes or "Add operator observations, terminal context, maintenance windows, and manual review notes here.",
            "",
        ]
    )


def _recurring_issues_frame(issues: list[SoakCampaignRecurringIssue]) -> pd.DataFrame:
    return pd.DataFrame([issue.model_dump(mode="json") for issue in issues])


def _campaign_unresolved_frame(
    alerts: list[OperationalAlert],
    incidents: list[BrokerIncident],
    anomalies: list[SoakAnomaly],
    issues: list[SoakCampaignRecurringIssue],
) -> pd.DataFrame:
    rows: list[dict[str, str | int | None]] = []
    rows.extend({"source": "alert", "id": alert.alert_id, "category": alert.category.value, "severity": alert.severity.value, "status": alert.status.value, "reason": alert.reason} for alert in alerts if alert.status.value != "resolved")
    rows.extend({"source": "incident", "id": incident.incident_id, "category": incident.category.value, "severity": incident.severity.value, "status": incident.status.value, "reason": incident.reason} for incident in incidents if incident.status.value == "open")
    rows.extend({"source": "soak_anomaly", "id": anomaly.anomaly_id, "category": anomaly.category.value, "severity": anomaly.severity, "status": "open", "reason": anomaly.reason} for anomaly in anomalies)
    rows.extend({"source": "recurring_issue", "id": issue.category, "category": issue.category, "severity": issue.severity, "status": issue.trend, "reason": issue.recommendation} for issue in issues)
    return pd.DataFrame(rows)


def _campaign_timeline_frame(samples: list[SoakSample]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sample_index": sample.sample_index,
                "run_id": sample.run_id,
                "sampled_at": sample.sampled_at.isoformat(),
                "mode": sample.mode,
                "broker": sample.broker,
                "health_status": sample.health_status,
                "connected": sample.connected,
                "account_sync_fresh": sample.account_sync_fresh,
                "position_sync_fresh": sample.position_sync_fresh,
                "reconciliation_fresh": sample.reconciliation_fresh,
                "reconciliation_anomalies": sample.reconciliation_anomalies,
                "active_alerts": sample.active_alerts,
                "open_incidents": sample.open_incidents,
                "degraded_mode": sample.degraded_mode,
                "recovery_invoked": sample.recovery_invoked,
                "resume_readiness": sample.resume_readiness,
            }
            for sample in samples
        ]
    )


def _restart_recovery_frame(samples: list[SoakSample]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sample_index": sample.sample_index,
                "run_id": sample.run_id,
                "sampled_at": sample.sampled_at.isoformat(),
                "health_status": sample.health_status,
                "connected": sample.connected,
                "reconciliation_anomalies": sample.reconciliation_anomalies,
                "resume_readiness": sample.resume_readiness,
            }
            for sample in samples
            if sample.recovery_invoked
        ]
    )


def _campaign_burden_frame(metrics: SoakCampaignReliabilityMetrics) -> pd.DataFrame:
    rows: list[dict[str, str | int | float]] = []
    rows.extend({"type": "alert_severity", "name": key, "count": value, "per_day": metrics.alert_burden_per_day} for key, value in metrics.alert_count_by_severity.items())
    rows.extend({"type": "alert_category", "name": key, "count": value, "per_day": metrics.alert_burden_per_day} for key, value in metrics.alert_count_by_category.items())
    rows.extend({"type": "incident_severity", "name": key, "count": value, "per_day": metrics.incident_burden_per_day} for key, value in metrics.incident_count_by_severity.items())
    rows.extend({"type": "incident_category", "name": key, "count": value, "per_day": metrics.incident_burden_per_day} for key, value in metrics.incident_count_by_category.items())
    return pd.DataFrame(rows)


def _campaign_runs_frame(runs: list[SoakRun]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": run.run_id,
                "started_at": run.started_at.isoformat(),
                "ended_at": run.ended_at.isoformat() if run.ended_at else "",
                "mode": run.mode,
                "broker": run.broker,
                "status": run.status.value,
                "readiness": run.readiness.value if run.readiness else "",
                "samples": run.samples,
            }
            for run in runs
        ]
    )


def _dict_lines(values: dict[str, int]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {key}: {value}" for key, value in sorted(values.items())]
