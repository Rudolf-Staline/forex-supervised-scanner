"""Soak-validation persistence, metrics, anomaly, and report tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.execution.soak import (
    SoakCampaignReadiness,
    SoakReadinessResult,
    SoakRunStatus,
    SoakSample,
    aggregate_campaign_reliability,
    analyze_campaign_recurrence,
    assess_soak_readiness,
    assess_campaign_readiness,
    attach_run_to_campaign,
    complete_soak_run,
    compute_soak_reliability,
    create_soak_campaign,
    create_soak_run,
    detect_soak_anomalies,
    finalize_soak_campaign,
    stop_soak_campaign,
    validate_soak_mode,
)
from app.reporting.soak import generate_soak_campaign_report, generate_soak_report
from app.storage.database import Database


def _sample(index: int, *, run_id: str = "run-1", connected: bool = True, health: str = "healthy", stale: int = 0, retry: int = 0) -> SoakSample:
    sampled_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    return SoakSample(
        sample_id=f"sample-{index}",
        run_id=run_id,
        sample_index=index,
        sampled_at=sampled_at,
        mode="broker_sandbox",
        broker="mock",
        connected=connected,
        can_trade=connected,
        health_status=health,
        account_sync_fresh=connected and stale == 0,
        position_sync_fresh=connected,
        reconciliation_fresh=connected and stale == 0,
        open_incidents=0,
        blocking_incidents=0,
        active_alerts=0,
        resolved_alerts=0,
        retry_exhausted=retry,
        broker_rejects=0,
        stale_state_detections=stale,
        live_guardrail_triggers=0,
        manual_intervention_required=0,
        reconciliation_anomalies=0 if connected else 1,
        blocking_reconciliation_anomalies=0 if connected else 1,
        degraded_mode=not connected or stale > 0,
        degraded_flags=["account_state_stale"] if stale else [],
        kill_switch_active=False,
        recovery_invoked=True,
        resume_readiness="safe_to_resume" if connected else "blocked_pending_manual_review",
    )


def test_soak_mode_validation_blocks_live_by_default(settings) -> None:
    validate_soak_mode("broker_sandbox", settings)

    with pytest.raises(ValueError, match="not allowed"):
        validate_soak_mode("broker_live", settings, allow_live=True)


def test_soak_persistence_round_trip(settings, tmp_path) -> None:
    database = Database(tmp_path / "soak.sqlite")
    run = create_soak_run("broker_sandbox", "mock", 60.0, 5.0)
    sample = _sample(1, run_id=run.run_id)
    database.save_soak_run(run)
    database.save_soak_samples([sample])

    metrics = compute_soak_reliability(run.run_id, [sample], [], [])
    assessment = assess_soak_readiness(run.run_id, metrics, [], settings)
    completed = complete_soak_run(run, assessment, [sample])
    database.save_soak_run(completed)

    loaded_run = database.load_soak_runs()[0]
    loaded_sample = database.load_soak_samples(run.run_id)[0]

    assert loaded_run.status == SoakRunStatus.COMPLETED
    assert loaded_run.readiness == SoakReadinessResult.PASS
    assert loaded_sample.sample_id == sample.sample_id


def test_soak_metrics_detect_instability_and_fail_readiness(settings) -> None:
    samples = [_sample(1), _sample(2, connected=False, health="unavailable", retry=1), _sample(3), _sample(4, stale=1)]
    metrics = compute_soak_reliability("run-1", samples, [], [])
    anomalies = detect_soak_anomalies("run-1", samples, [], [], metrics, settings)
    assessment = assess_soak_readiness("run-1", metrics, anomalies, settings)

    assert metrics.connectivity_success_rate_pct == 75.0
    assert metrics.health_flap_count >= 2
    assert any(anomaly.category.value == "retry_exhausted" for anomaly in anomalies)
    assert assessment.result == SoakReadinessResult.FAIL


def test_soak_report_generation(settings, tmp_path) -> None:
    run = create_soak_run("broker_sandbox", "mock", 60.0, 5.0)
    samples = [_sample(1), _sample(2)]
    metrics = compute_soak_reliability(run.run_id, samples, [], [])
    anomalies = detect_soak_anomalies(run.run_id, samples, [], [], metrics, settings)
    assessment = assess_soak_readiness(run.run_id, metrics, anomalies, settings)
    completed = complete_soak_run(run, assessment, samples)

    outputs = generate_soak_report(completed, samples, metrics, anomalies, assessment, tmp_path / "reports")

    assert outputs["summary"].exists()
    assert outputs["samples"].exists()
    assert outputs["readiness"].exists()
    assert "Soak Readiness" in outputs["readiness"].read_text(encoding="utf-8")


def test_soak_campaign_persistence_resume_and_finalize(settings, tmp_path) -> None:
    database = Database(tmp_path / "campaign.sqlite")
    campaign = create_soak_campaign("weekly-check", "broker_sandbox", "mock", 7 * 24 * 3600.0)
    run = create_soak_run("broker_sandbox", "mock", 60.0, 5.0)
    sample = _sample(1, run_id=run.run_id)
    database.save_soak_campaign(campaign)
    database.save_soak_run(run)
    database.save_soak_samples([sample])

    resumed = attach_run_to_campaign(database.load_soak_campaign(campaign.campaign_id), run)  # type: ignore[arg-type]
    database.save_soak_campaign(resumed)
    loaded = database.load_running_soak_campaign("weekly-check")
    metrics = aggregate_campaign_reliability(resumed, [run], [sample], [], [], [])
    recurring = analyze_campaign_recurrence([sample], [], [], [], min_count=2)
    assessment = assess_campaign_readiness(resumed, metrics, recurring, settings)
    finalized = finalize_soak_campaign(resumed, assessment, [sample])
    database.save_soak_campaign(finalized)

    assert loaded is not None
    assert loaded.run_ids == [run.run_id]
    assert database.load_soak_campaign(campaign.campaign_id).status.value == "finalized"  # type: ignore[union-attr]
    assert finalized.readiness in {SoakCampaignReadiness.NOT_READY, SoakCampaignReadiness.LIMITED_READY, SoakCampaignReadiness.SUPERVISED_READY}


def test_soak_campaign_readiness_and_recurrence_are_conservative(settings) -> None:
    campaign = create_soak_campaign("unstable", "broker_sandbox", "mock", 24 * 3600.0)
    samples = [
        _sample(1, connected=True),
        _sample(2, connected=False, health="unavailable", retry=1),
        _sample(3, connected=False, health="unavailable", retry=1),
        _sample(4, connected=True, stale=1),
    ]
    metrics = aggregate_campaign_reliability(campaign, [], samples, [], [], [])
    recurring = analyze_campaign_recurrence(samples, [], [], [], min_count=2)
    assessment = assess_campaign_readiness(campaign, metrics, recurring, settings)

    assert metrics.broker_unavailable_pct == 50.0
    assert any(issue.category == "broker_disconnect" for issue in recurring)
    assert assessment.rating == SoakCampaignReadiness.NOT_READY
    assert assessment.blocking_issues
    assert assessment.recommended_next_actions


def test_soak_campaign_report_generation(settings, tmp_path) -> None:
    campaign = create_soak_campaign("weekly-check", "broker_sandbox", "mock", 7 * 24 * 3600.0)
    run = create_soak_run("broker_sandbox", "mock", 60.0, 5.0)
    campaign = attach_run_to_campaign(campaign, run)
    samples = [_sample(1, run_id=run.run_id), _sample(2, run_id=run.run_id)]
    metrics = aggregate_campaign_reliability(campaign, [run], samples, [], [], [])
    recurring = analyze_campaign_recurrence(samples, [], [], [], min_count=2)
    assessment = assess_campaign_readiness(campaign, metrics, recurring, settings)
    finalized = finalize_soak_campaign(campaign, assessment, samples)

    outputs = generate_soak_campaign_report(finalized, [run], samples, metrics, recurring, assessment, tmp_path / "campaigns")

    assert outputs["campaign_summary"].exists()
    assert outputs["weekly_reliability"].exists()
    assert outputs["readiness"].exists()
    assert "Campaign Readiness" in outputs["readiness"].read_text(encoding="utf-8")


def test_soak_campaign_stop_preserves_reason() -> None:
    campaign = create_soak_campaign("pause-me", "paper", "paper", 3600.0)
    stopped = stop_soak_campaign(campaign, reason="operator paused")

    assert stopped.status.value == "stopped"
    assert stopped.summary["stop_reason"] == "operator paused"
