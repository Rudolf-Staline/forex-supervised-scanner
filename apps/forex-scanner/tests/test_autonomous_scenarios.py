from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.execution.autonomous_policy import AutonomousPolicyDecisionType
from app.execution.autonomous_scenarios import (
    BUILTIN_AUTONOMOUS_SCENARIO_IDS,
    AutonomousScenarioConfig,
    AutonomousScenarioRunnerService,
    AutonomousScenarioStatus,
    load_builtin_scenarios,
)

PROJECT = Path(__file__).resolve().parents[1]


def _scenario(scenario_id: str):
    return next(s for s in load_builtin_scenarios() if s.scenario_id == scenario_id)


def test_builtin_scenario_list_loads():
    scenarios = load_builtin_scenarios()
    scenario_ids = [s.scenario_id for s in scenarios]
    assert scenario_ids == list(BUILTIN_AUTONOMOUS_SCENARIO_IDS)
    assert len(scenario_ids) == len(set(scenario_ids))


def test_every_builtin_scenario_has_required_fields():
    for scenario in load_builtin_scenarios():
        assert scenario.scenario_id
        assert scenario.title
        assert scenario.description
        assert scenario.mode.value in {"DRY_RUN", "READ_ONLY", "PAPER", "DIAGNOSTIC"}
        assert scenario.readiness_status
        assert scenario.evidence_status
        assert scenario.action
        assert scenario.expected.policy_decision.value in {"ALLOW", "WARN_ALLOW", "DENY"}
        assert scenario.expected.supervisor_behavior.value
        assert "autonomous_evidence_summary.json" in scenario.synthetic_reports
        assert "autonomous_readiness_report.json" in scenario.synthetic_reports


def test_dry_run_missing_evidence_scenario_passes(tmp_path: Path):
    service = AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=tmp_path, strict=True))
    result = service.run_scenario(_scenario("dry_run_missing_evidence_warn_allowed"))
    assert result.status == AutonomousScenarioStatus.PASS
    assert result.actual_decision in {"ALLOW", "WARN_ALLOW"}
    assert result.actual_supervisor_behavior == "WOULD_RUN_DRY_RUN"


def test_paper_missing_evidence_denies_supervisor_cycle(tmp_path: Path):
    result = AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=tmp_path, strict=True)).run_scenario(
        _scenario("paper_missing_evidence_denied")
    )
    assert result.status == AutonomousScenarioStatus.PASS
    assert result.actual_decision == AutonomousPolicyDecisionType.DENY.value
    assert result.actual_supervisor_behavior == "DENIED_BY_POLICY"
    assert any("evidence" in reason.lower() for reason in result.blocking_reasons)


def test_maintenance_and_degraded_scenarios_deny_supervisor_cycle(tmp_path: Path):
    service = AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=tmp_path, strict=True))
    for scenario_id in ["maintenance_mode_denied", "degraded_mode_denied"]:
        result = service.run_scenario(_scenario(scenario_id))
        assert result.status == AutonomousScenarioStatus.PASS
        assert result.actual_decision == "DENY"
        assert result.actual_supervisor_behavior == "DENIED_BY_POLICY"


def test_live_broker_and_order_paths_always_deny(tmp_path: Path):
    service = AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=tmp_path, strict=True))
    for scenario_id in ["live_trading_always_denied", "broker_live_always_denied", "order_send_path_always_denied"]:
        result = service.run_scenario(_scenario(scenario_id))
        assert result.status == AutonomousScenarioStatus.PASS
        assert result.actual_decision == "DENY"
        assert result.actual_supervisor_behavior == "DENIED_BY_POLICY"


def test_recovery_recommended_scenarios_do_not_execute_forbidden_actions(tmp_path: Path):
    service = AutonomousScenarioRunnerService(
        AutonomousScenarioConfig(reports_dir=tmp_path, strict=True, include_recovery_plan=True)
    )
    suite = service.run_scenario_suite([
        _scenario("failure_diagnostics_blocked_recovery_recommended"),
        _scenario("signal_anomaly_blocked_recovery_recommended"),
        _scenario("recovery_manual_action_not_auto_executed"),
    ])
    assert suite.final_status == AutonomousScenarioStatus.PASS
    for plan in service.recovery_plans.values():
        assert plan["executed_actions"] == []
        serialized = json.dumps(plan).lower()
        assert "live" not in serialized or "live_execution_allowed" in serialized
        assert "order_send(" not in serialized


def test_suite_report_schema_is_stable(tmp_path: Path):
    service = AutonomousScenarioRunnerService(
        AutonomousScenarioConfig(reports_dir=tmp_path, strict=True, include_policy_report=True, include_recovery_plan=True)
    )
    suite = service.run_scenario_suite()
    assert suite.final_status == AutonomousScenarioStatus.PASS
    payload = suite.model_dump(mode="json")
    assert set(payload) >= {
        "generated_at",
        "final_status",
        "scenarios_total",
        "scenarios_passed",
        "scenarios_failed",
        "scenarios_warned",
        "scenarios_skipped",
        "scenario_results",
        "safety_flags",
        "policy_decisions",
        "recovery_plans",
        "output_paths",
    }
    first = payload["scenario_results"][0]
    assert set(first) >= {
        "scenario_id",
        "status",
        "expected_decision",
        "actual_decision",
        "expected_supervisor_behavior",
        "actual_supervisor_behavior",
        "mismatches",
        "warnings",
        "blocking_reasons",
        "output_paths",
    }


def test_cli_list_works():
    completed = subprocess.run(
        [sys.executable, "scripts/autonomous_scenario_runner.py", "--list"],
        cwd=PROJECT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "total scenarios:" in completed.stdout
    assert "live trading" in completed.stdout.lower()


def test_cli_all_export_json_and_txt_works(tmp_path: Path):
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/autonomous_scenario_runner.py",
            "--all",
            "--reports-dir",
            str(tmp_path),
            "--export-json",
            "--export-txt",
            "--strict",
        ],
        cwd=PROJECT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "final_status: PASS" in completed.stdout
    assert (tmp_path / "autonomous_scenario_suite.json").exists()
    assert (tmp_path / "autonomous_scenario_suite.txt").exists()


def test_no_env_mutation_mt5_order_execution_or_network_dependency(tmp_path: Path):
    env_path = PROJECT / ".env"
    before = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    service = AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=tmp_path, strict=True))
    suite = service.run_scenario_suite()
    after = env_path.read_text(encoding="utf-8") if env_path.exists() else None
    assert before == after
    assert suite.safety_flags["mt5_required"] is False
    assert suite.safety_flags["external_network_required"] is False
    assert suite.safety_flags["env_mutation_performed"] is False
    module_text = (PROJECT / "app/execution/autonomous_scenarios.py").read_text(encoding="utf-8")
    assert "requests." not in module_text
    assert "MetaTrader5" not in module_text
    assert "order_send(" not in module_text
