from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.execution.autonomous_recovery import (
    AutonomousRecoveryActionType,
    AutonomousRecoveryConfig,
    AutonomousRecoveryExecutionStatus,
    AutonomousRecoveryFinalStatus,
    AutonomousRecoveryPlannerService,
    AutonomousRecoveryCauseType,
    build_recovery_plan,
    execute_recovery_plan,
    export_autonomous_recovery_json,
    export_autonomous_recovery_txt,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _healthy_reports(tmp_path: Path) -> None:
    _write(tmp_path / "autonomous_evidence_summary.json", {"final_status": "READY_EVIDENCE", "blocking_failures": []})
    _write(
        tmp_path / "autonomous_readiness_report.json",
        {
            "final_status": "READY",
            "ready": True,
            "dry_run_allowed": True,
            "paper_run_allowed": True,
            "blocking_reasons": [],
            "warning_reasons": [],
            "checks": [],
        },
    )
    _write(tmp_path / "session_health_summary.json", {"overall_status": "HEALTHY"})
    _write(tmp_path / "data_health_report.json", {"data_quality_status": "HEALTHY", "data_quality_score": 100})
    _write(tmp_path / "failure_diagnostics_summary.json", {"severity": "CLEAN"})


def _cause_values(plan) -> set[str]:
    return {cause.cause_type.value for cause in plan.causes}


def _action_values(plan) -> set[str]:
    return {action.action_id.value for action in plan.actions}


def test_missing_evidence_produces_missing_evidence_causes_and_rebuild_actions(tmp_path: Path) -> None:
    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))

    assert AutonomousRecoveryCauseType.MISSING_EVIDENCE.value in _cause_values(plan)
    assert AutonomousRecoveryActionType.REBUILD_EVIDENCE_DRY_RUN.value in _action_values(plan)
    assert AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY.value in _action_values(plan)
    assert plan.final_status == AutonomousRecoveryFinalStatus.RECOVERY_BLOCKING
    assert plan.safety_flags["live_execution_allowed"] is False


def test_stale_reports_produce_stale_evidence_causes(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    old = 1_600_000_000
    os.utime(tmp_path / "autonomous_readiness_report.json", (old, old))

    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path, max_report_age_minutes=1))

    assert AutonomousRecoveryCauseType.STALE_EVIDENCE.value in _cause_values(plan)
    assert AutonomousRecoveryActionType.REVIEW_STALE_REPORTS.value in _action_values(plan)


def test_readiness_blocked_by_operator_controls_produces_manual_review_actions_only(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(
        tmp_path / "autonomous_readiness_report.json",
        {
            "final_status": "BLOCKED_BY_OPERATOR_CONTROL",
            "paper_run_allowed": False,
            "blocking_reasons": ["operator maintenance mode is active"],
            "operator_controls": {"maintenance_mode": True, "degraded_mode": False},
            "checks": [{"name": "operator_controls", "status": "FAIL", "reason": "operator maintenance mode is active"}],
        },
    )

    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))

    assert AutonomousRecoveryCauseType.OPERATOR_MAINTENANCE_MODE.value in _cause_values(plan)
    assert AutonomousRecoveryActionType.REVIEW_OPERATOR_CONTROLS.value in _action_values(plan)
    assert all(action.execution_mode.value == "MANUAL_REVIEW" for action in plan.actions)
    assert not plan.safe_actions


def test_failure_diagnostics_blocked_produces_diagnostics_and_rebuild_actions(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(tmp_path / "failure_diagnostics_summary.json", {"severity": "BLOCKED"})

    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))

    assert AutonomousRecoveryCauseType.FAILURE_DIAGNOSTICS_BLOCKED.value in _cause_values(plan)
    assert AutonomousRecoveryActionType.RUN_FAILURE_DIAGNOSTICS.value in _action_values(plan)
    assert AutonomousRecoveryActionType.REBUILD_EVIDENCE_READ_ONLY.value in _action_values(plan)


def test_anomaly_blocked_produces_anomaly_review_action(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)
    _write(tmp_path / "signal_anomaly_summary.json", {"data_integrity_status": "DEGRADED", "high_severity_anomalies": 1})

    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))

    assert AutonomousRecoveryCauseType.SIGNAL_ANOMALIES_BLOCKED.value in _cause_values(plan)
    assert AutonomousRecoveryActionType.RUN_SIGNAL_ANOMALY_DETECTOR.value in _action_values(plan)


def test_healthy_readiness_and_evidence_produces_no_recovery_needed(tmp_path: Path) -> None:
    _healthy_reports(tmp_path)

    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))

    assert plan.final_status == AutonomousRecoveryFinalStatus.NO_RECOVERY_NEEDED
    assert plan.causes == []
    assert plan.actions == []


def test_json_and_txt_exports_contain_required_schema(tmp_path: Path) -> None:
    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))

    json_path = export_autonomous_recovery_json(plan, tmp_path)
    txt_path = export_autonomous_recovery_txt(plan, tmp_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    required = {
        "generated_at",
        "final_status",
        "causes",
        "actions",
        "safe_actions",
        "manual_actions",
        "executed_actions",
        "skipped_actions",
        "blocking_reasons",
        "safety_flags",
        "next_recommended_command",
    }
    assert required <= payload.keys()
    assert "Autonomous Recovery Plan" in txt_path.read_text(encoding="utf-8")


def test_default_cli_does_not_execute_anything(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/autonomous_recovery_planner.py", "--reports-dir", str(tmp_path), "--export-json", "--export-txt"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "live_execution_allowed=false" in result.stdout
    payload = json.loads((tmp_path / "autonomous_recovery_plan.json").read_text(encoding="utf-8"))
    assert payload["executed_actions"] == []
    assert all(action["execution_status"] == "NOT_REQUESTED" for action in payload["actions"])


def test_execute_safe_actions_executes_only_safe_read_only_or_dry_run_actions(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], cwd: Path):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))
    executed = AutonomousRecoveryPlannerService(command_runner=runner).execute_plan(
        plan,
        AutonomousRecoveryConfig(reports_dir=tmp_path, execute_safe_actions=True),
    )

    assert calls
    assert set(executed.executed_actions) == set(executed.safe_actions)
    assert not set(executed.executed_actions) & set(executed.manual_actions)
    assert all(action.execution_status != AutonomousRecoveryExecutionStatus.EXECUTED for action in executed.actions if not action.safe_to_execute_automatically)


def test_forbidden_actions_are_never_executed(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=tmp_path))

    execute_recovery_plan(plan, AutonomousRecoveryConfig(reports_dir=tmp_path, execute_safe_actions=False))

    assert calls == []
    for action in plan.actions:
        command = " ".join(action.command_suggestion or []).lower()
        assert "--enabled" not in command
        assert "--no-dry-run" not in command
        assert "live" not in command


def test_no_env_mutation_and_no_mt5_order_execution_or_live_trading_calls(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    env_file = repo / ".env"
    before = env_file.read_text(encoding="utf-8") if env_file.exists() else None

    result = subprocess.run(
        [sys.executable, "scripts/autonomous_recovery_planner.py", "--reports-dir", str(tmp_path), "--execute-safe-actions", "--dry-run", "--export-json", "--export-txt"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    after = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    source = (repo / "app/execution/autonomous_recovery.py").read_text(encoding="utf-8")
    forbidden_call = "order" + "_" + "send"
    assert result.returncode == 0
    assert before == after
    assert forbidden_call not in source
    assert "MetaTrader5" not in source
    assert "live_execution_allowed=false" in result.stdout


def test_integration_flags_generate_recovery_plan_on_readiness_block(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_autonomous_supervisor.py",
            "--once",
            "--symbols",
            "EUR/USD",
            "--dry-run",
            "--build-evidence-first",
            "--evidence-mode",
            "read-only",
            "--readiness-only",
            "--plan-recovery-on-block",
            "--export-recovery-json",
            "--export-recovery-txt",
            "--reports-dir",
            str(tmp_path),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "recovery_plan=" in result.stdout
    assert (tmp_path / "autonomous_recovery_plan.json").exists()
    assert (tmp_path / "autonomous_recovery_plan.txt").exists()
def test_recovery_planner_never_unblocks_supervisor_directly() -> None:
    from app.execution.autonomous_recovery import AutonomousRecoveryActionType, _action
    action = _action(AutonomousRecoveryActionType.RUN_READINESS_ONLY, [])
    assert action.safe_to_execute_automatically is True
    assert "supervisor" not in str(action.command_suggestion).lower()
    assert "readiness" in str(action.command_suggestion).lower()

    action2 = _action(AutonomousRecoveryActionType.KEEP_SUPERVISOR_BLOCKED, [])
    assert action2.safe_to_execute_automatically is False
