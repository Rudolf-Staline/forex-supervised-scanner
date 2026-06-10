from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.execution.autonomous_evidence import (
    AutonomousEvidenceConfig,
    AutonomousEvidenceFinalStatus,
    AutonomousEvidenceMode,
    AutonomousEvidenceTask,
    AutonomousEvidenceTaskStatus,
    build_default_task_plan,
    build_evidence,
)


def _write_sample_reports(reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "signal_journal.jsonl").write_text(
        json.dumps(
            {
                "symbol": "EUR/USD",
                "asset_class": "forex",
                "session": "london",
                "status": "approved",
                "decision": "approved",
                "score": 82,
                "risk_reward": 2.0,
                "spread_atr": 0.2,
                "created_order": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (reports_dir / "forward_test_paper.csv").write_text(
        "symbol,asset_class,session,status,decision,score,risk_reward,spread_atr\n"
        "EUR/USD,forex,london,approved,approved,81,2.1,0.2\n",
        encoding="utf-8",
    )


def test_dry_run_builds_plan_without_mutating_report_inputs(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    _write_sample_reports(reports_dir)
    before = {path.name: path.read_text(encoding="utf-8") for path in reports_dir.iterdir()}

    report = build_evidence(config=AutonomousEvidenceConfig(reports_dir=reports_dir, mode="dry-run"))

    assert report.final_status == AutonomousEvidenceFinalStatus.DRY_RUN_PLAN
    assert report.tasks_total == len(build_default_task_plan())
    assert report.tasks_skipped == report.tasks_total
    after = {path.name: path.read_text(encoding="utf-8") for path in reports_dir.iterdir()}
    assert after == before
    assert not (reports_dir / "autonomous_evidence_summary.json").exists()


def test_read_only_mode_runs_available_report_builders_using_temp_reports_dir(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    _write_sample_reports(reports_dir)

    report = build_evidence(
        config=AutonomousEvidenceConfig(reports_dir=reports_dir, mode=AutonomousEvidenceMode.READ_ONLY, export_json=True, export_txt=True)
    )

    assert report.final_status in {AutonomousEvidenceFinalStatus.READY_EVIDENCE, AutonomousEvidenceFinalStatus.WARN_EVIDENCE}
    assert (reports_dir / "session_health_summary.json").exists()
    assert (reports_dir / "data_health_report.json").exists()
    assert (reports_dir / "failure_diagnostics_summary.json").exists()
    assert (reports_dir / "signal_anomaly_summary.json").exists()
    assert (reports_dir / "mt5_symbol_mapping_audit.json").exists()
    payload = json.loads((reports_dir / "autonomous_evidence_summary.json").read_text(encoding="utf-8"))
    for key in [
        "generated_at",
        "mode",
        "final_status",
        "tasks_total",
        "tasks_passed",
        "tasks_warned",
        "tasks_failed",
        "tasks_skipped",
        "blocking_failures",
        "task_results",
        "output_paths",
        "readiness_report",
        "safety_flags",
    ]:
        assert key in payload
    assert "Autonomous Evidence Builder Report" in (reports_dir / "autonomous_evidence_report.txt").read_text(encoding="utf-8")


def test_missing_optional_evidence_warns_or_skips_without_crashing(tmp_path: Path) -> None:
    report = build_evidence(config=AutonomousEvidenceConfig(reports_dir=tmp_path / "reports", mode="read-only"))

    assert report.final_status == AutonomousEvidenceFinalStatus.WARN_EVIDENCE
    assert report.tasks_failed == 0
    assert any(result.status in {AutonomousEvidenceTaskStatus.WARN, AutonomousEvidenceTaskStatus.SKIP} for result in report.task_results)


def test_blocking_task_failure_produces_blocked_evidence(tmp_path: Path) -> None:
    def boom(config: AutonomousEvidenceConfig):
        raise RuntimeError("synthetic blocker")

    task = AutonomousEvidenceTask(name="blocker", description="boom", blocking=True, runner=boom)
    report = build_evidence(config=AutonomousEvidenceConfig(reports_dir=tmp_path, mode="read-only"), tasks=[task])

    assert report.final_status == AutonomousEvidenceFinalStatus.BLOCKED_EVIDENCE
    assert report.blocking_failures
    assert report.task_results[0].exception_class == "RuntimeError"


def test_fail_fast_stops_after_first_blocking_failure(tmp_path: Path) -> None:
    def boom(config: AutonomousEvidenceConfig):
        raise RuntimeError("first")

    def ok(config: AutonomousEvidenceConfig):
        return AutonomousEvidenceTaskStatus.PASS, "ok", [], {}

    tasks = [
        AutonomousEvidenceTask(name="first", description="boom", blocking=True, runner=boom),
        AutonomousEvidenceTask(name="second", description="ok", blocking=True, runner=ok),
    ]
    report = build_evidence(config=AutonomousEvidenceConfig(reports_dir=tmp_path, mode="read-only", fail_fast=True), tasks=tasks)

    assert report.tasks_total == 1
    assert report.task_results[0].task_name == "first"


def test_readiness_inclusion_embeds_readiness_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.execution.autonomous_evidence as evidence

    class FakeReadiness:
        def model_dump(self, mode: str = "json"):
            return {"final_status": "READY", "paper_run_allowed": False}

    monkeypatch.setattr(evidence, "build_readiness_report", lambda *args, **kwargs: FakeReadiness())
    monkeypatch.setattr(evidence, "export_autonomous_readiness_json", lambda report, reports_dir: reports_dir / "autonomous_readiness_report.json")
    monkeypatch.setattr(evidence, "export_autonomous_readiness_txt", lambda report, reports_dir: reports_dir / "autonomous_readiness_report.txt")

    report = evidence.build_evidence(
        settings=object(),
        database=object(),
        config=AutonomousEvidenceConfig(reports_dir=tmp_path, mode="read-only", include_readiness=True),
        tasks=[],
    )

    assert report.readiness_report == {"final_status": "READY", "paper_run_allowed": False}


def test_no_mt5_order_execution_live_paths_or_env_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("SECRET=value\n", encoding="utf-8")
    before = env_path.read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    report = build_evidence(config=AutonomousEvidenceConfig(reports_dir=tmp_path / "reports", mode="read-only"))

    assert env_path.read_text(encoding="utf-8") == before
    assert report.safety_flags["live_execution_allowed"] is False
    assert report.safety_flags["broker_order_submission_allowed"] is False
    assert report.safety_flags["orders_sent"] is False
    assert report.safety_flags["order_send_called"] is False
    assert all(result.safety_flags.get("mt5_called") is False for result in report.task_results)


def test_subprocess_usage_requires_explicit_allowance(tmp_path: Path) -> None:
    task = AutonomousEvidenceTask(
        name="subprocess_fallback",
        description="explicit fallback",
        blocking=False,
        subprocess_command=["python", "-c", "print('ok')"],
    )
    blocked = build_evidence(config=AutonomousEvidenceConfig(reports_dir=tmp_path, mode="read-only"), tasks=[task])
    assert blocked.task_results[0].status == AutonomousEvidenceTaskStatus.SKIP
    assert blocked.task_results[0].safety_flags["subprocess_used"] is False

    allowed = build_evidence(config=AutonomousEvidenceConfig(reports_dir=tmp_path, mode="read-only", allow_subprocess=True), tasks=[task])
    assert allowed.task_results[0].status == AutonomousEvidenceTaskStatus.PASS
    assert allowed.task_results[0].safety_flags["subprocess_used"] is True


def test_readiness_and_supervisor_cli_expose_build_evidence_first_options() -> None:
    root = Path(__file__).resolve().parents[1]
    readiness = subprocess.run(
        ["python", "scripts/autonomous_readiness_report.py", "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    supervisor = subprocess.run(
        ["python", "scripts/run_autonomous_supervisor.py", "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert readiness.returncode == 0
    assert supervisor.returncode == 0
    assert "--build-evidence-first" in readiness.stdout
    assert "--evidence-mode" in readiness.stdout
    assert "--build-evidence-first" in supervisor.stdout
    assert "--evidence-mode" in supervisor.stdout
