"""Tests for the Autonomous Policy Engine.

Cloud-safe — no MT5, no network, no live trading required.

These tests verify that every autonomous action passes through the centralized
policy engine and that the safety-first invariants (no live trading, no broker-live,
no MT5 order execution, no order_send, etc.) are upheld across all modes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.execution.autonomous_policy import (
    AutonomousPolicyConfig,
    AutonomousPolicyContext,
    AutonomousPolicyDecision,
    AutonomousPolicyDecisionType,
    AutonomousPolicyEngine,
    AutonomousPolicyMode,
    AutonomousPolicyRuleResult,
    AutonomousPolicyRuleStatus,
    AutonomousPolicySeverity,
    SAFETY_INVARIANT_NAMES,
    export_autonomous_policy_json,
    export_autonomous_policy_txt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine(
    mode: AutonomousPolicyMode = AutonomousPolicyMode.DRY_RUN,
    dry_run: bool = True,
    **kwargs,
) -> AutonomousPolicyEngine:
    """Create a policy engine with the given config overrides."""
    return AutonomousPolicyEngine(
        config=AutonomousPolicyConfig(mode=mode, dry_run=dry_run, **kwargs),
    )


def _ctx(
    action: str = "",
    mode: AutonomousPolicyMode = AutonomousPolicyMode.DRY_RUN,
    dry_run: bool = True,
    **kwargs,
) -> AutonomousPolicyContext:
    """Build a policy context with the given overrides."""
    return AutonomousPolicyContext(action=action, mode=mode, dry_run=dry_run, **kwargs)


def _rule_names(decision: AutonomousPolicyDecision) -> list[str]:
    """Extract rule names from a decision for easy assertion."""
    return [r.rule_name for r in decision.rule_results]


def _rule_map(decision: AutonomousPolicyDecision) -> dict[str, AutonomousPolicyRuleResult]:
    """Map rule_name → result for a decision."""
    return {r.rule_name: r for r in decision.rule_results}


# ---------------------------------------------------------------------------
# 1. Evidence Building
# ---------------------------------------------------------------------------


def test_dry_run_evidence_build_allowed():
    """Dry-run evidence build is allowed under DRY_RUN mode."""
    engine = _engine(mode=AutonomousPolicyMode.DRY_RUN, dry_run=True)
    decision = engine.can_build_evidence()
    assert decision.allowed is True
    assert decision.decision == AutonomousPolicyDecisionType.ALLOW
    assert "evidence_dry_run_allowed" in _rule_names(decision)


def test_read_only_evidence_build_allowed():
    """Read-only evidence build is allowed under READ_ONLY mode."""
    engine = _engine(mode=AutonomousPolicyMode.READ_ONLY, dry_run=True)
    ctx = _ctx(mode=AutonomousPolicyMode.READ_ONLY, dry_run=True)
    decision = engine.can_build_evidence(ctx)
    assert decision.allowed is True
    assert "evidence_read_only_allowed" in _rule_names(decision)


def test_refresh_denied_when_mt5_required():
    """In PAPER mode with require_mt5=True, evidence refresh is denied."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.PAPER,
        dry_run=False,
        require_mt5=True,
    )
    engine = _engine(mode=AutonomousPolicyMode.PAPER, dry_run=False, require_mt5=True)
    decision = engine.can_build_evidence(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "evidence_refresh_mt5_denied" in rm
    assert rm["evidence_refresh_mt5_denied"].status == AutonomousPolicyRuleStatus.FAIL


def test_subprocess_fallback_denied_by_default():
    """Subprocess fallback is denied by default (safe behavior)."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.PAPER,
        dry_run=False,
        allow_subprocess_fallback=False,
    )
    engine = _engine(mode=AutonomousPolicyMode.PAPER, dry_run=False)
    decision = engine.can_build_evidence(ctx)
    rm = _rule_map(decision)
    assert "evidence_subprocess_denied" in rm
    assert rm["evidence_subprocess_denied"].status == AutonomousPolicyRuleStatus.PASS


# ---------------------------------------------------------------------------
# 2. Readiness Skip
# ---------------------------------------------------------------------------


def test_readiness_skip_allowed_only_for_dry_run_diagnostic():
    """Readiness skip is allowed only for dry-run diagnostic modes."""
    engine = _engine(mode=AutonomousPolicyMode.DRY_RUN, dry_run=True)
    ctx = _ctx(mode=AutonomousPolicyMode.DRY_RUN, dry_run=True)
    decision = engine.can_skip_readiness_gate(ctx)
    assert decision.allowed is True
    rm = _rule_map(decision)
    assert "readiness_skip_dry_run_diagnostic" in rm
    assert rm["readiness_skip_dry_run_diagnostic"].status == AutonomousPolicyRuleStatus.PASS

    # Also verify DIAGNOSTIC mode works
    ctx_diag = _ctx(mode=AutonomousPolicyMode.DIAGNOSTIC, dry_run=True)
    decision_diag = engine.can_skip_readiness_gate(ctx_diag)
    assert decision_diag.allowed is True
    assert "readiness_skip_dry_run_diagnostic" in _rule_names(decision_diag)


def test_readiness_skip_denied_for_paper_supervisor_cycles():
    """Readiness skip is denied for non-dry-run paper supervisor cycles."""
    engine = _engine(mode=AutonomousPolicyMode.PAPER, dry_run=False)
    ctx = _ctx(mode=AutonomousPolicyMode.PAPER, dry_run=False)
    decision = engine.can_skip_readiness_gate(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "readiness_skip_denied" in rm
    assert rm["readiness_skip_denied"].status == AutonomousPolicyRuleStatus.FAIL


# ---------------------------------------------------------------------------
# 3. Recovery Actions
# ---------------------------------------------------------------------------


def test_safe_recovery_action_allowed_when_safe():
    """Safe recovery actions are allowed under DRY_RUN mode."""
    engine = _engine(
        mode=AutonomousPolicyMode.DRY_RUN,
        dry_run=True,
        recovery_action_safe=True,
    )
    ctx = _ctx(
        mode=AutonomousPolicyMode.DRY_RUN,
        dry_run=True,
        recovery_action_safe=True,
        recovery_action_manual=False,
    )
    decision = engine.can_execute_recovery_action(ctx)
    assert decision.allowed is True
    rm = _rule_map(decision)
    assert "recovery_safe_action_allowed" in rm
    assert rm["recovery_safe_action_allowed"].status == AutonomousPolicyRuleStatus.PASS


def test_manual_recovery_action_denied_for_auto_execution():
    """Manual-review recovery actions must never execute automatically."""
    engine = _engine(
        mode=AutonomousPolicyMode.DRY_RUN,
        dry_run=True,
        recovery_action_manual=True,
    )
    ctx = _ctx(
        mode=AutonomousPolicyMode.DRY_RUN,
        dry_run=True,
        recovery_action_manual=True,
    )
    decision = engine.can_execute_recovery_action(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "recovery_manual_action_denied" in rm
    assert rm["recovery_manual_action_denied"].status == AutonomousPolicyRuleStatus.FAIL


# ---------------------------------------------------------------------------
# 4. Supervisor
# ---------------------------------------------------------------------------


def test_supervisor_dry_run_allowed_under_safe_mode():
    """Supervisor invocation is allowed for dry-run under safe mode."""
    engine = _engine(mode=AutonomousPolicyMode.DRY_RUN, dry_run=True)
    decision = engine.can_run_supervisor()
    assert decision.allowed is True
    assert decision.decision == AutonomousPolicyDecisionType.ALLOW
    rm = _rule_map(decision)
    assert "supervisor_safe_invocation" in rm
    assert rm["supervisor_safe_invocation"].status == AutonomousPolicyRuleStatus.PASS


def test_supervisor_paper_cycle_denied_when_readiness_blocks():
    """Non-dry-run PAPER supervisor denied when readiness is BLOCKED_BY_SAFETY."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.PAPER,
        dry_run=False,
        readiness_status="BLOCKED_BY_SAFETY",
        evidence_status="OK",
        operator_mode="normal",
    )
    engine = _engine(mode=AutonomousPolicyMode.PAPER, dry_run=False)
    decision = engine.can_run_supervisor(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "supervisor_readiness_required" in rm
    assert rm["supervisor_readiness_required"].status == AutonomousPolicyRuleStatus.FAIL


def test_supervisor_paper_cycle_denied_when_evidence_blocks():
    """Non-dry-run PAPER supervisor denied when evidence is BLOCKED_EVIDENCE."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.PAPER,
        dry_run=False,
        readiness_status="READY",
        evidence_status="BLOCKED_EVIDENCE",
        operator_mode="normal",
    )
    engine = _engine(mode=AutonomousPolicyMode.PAPER, dry_run=False)
    decision = engine.can_run_supervisor(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "supervisor_evidence_blocking" in rm
    assert rm["supervisor_evidence_blocking"].status == AutonomousPolicyRuleStatus.FAIL


def test_supervisor_paper_cycle_denied_under_operator_maintenance():
    """Supervisor cycles are denied under operator maintenance mode."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.DRY_RUN,
        dry_run=True,
        operator_mode="maintenance",
    )
    engine = _engine(mode=AutonomousPolicyMode.DRY_RUN, dry_run=True)
    decision = engine.can_run_supervisor(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "supervisor_operator_mode_denied" in rm
    assert rm["supervisor_operator_mode_denied"].status == AutonomousPolicyRuleStatus.FAIL


def test_supervisor_paper_cycle_denied_under_operator_degraded():
    """Supervisor cycles are denied under operator degraded mode."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.DRY_RUN,
        dry_run=True,
        operator_mode="degraded",
    )
    engine = _engine(mode=AutonomousPolicyMode.DRY_RUN, dry_run=True)
    decision = engine.can_run_supervisor(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "supervisor_operator_mode_denied" in rm
    assert rm["supervisor_operator_mode_denied"].status == AutonomousPolicyRuleStatus.FAIL


# ---------------------------------------------------------------------------
# 5. Safety Invariants (live trading, broker-live, MT5, order_send)
# ---------------------------------------------------------------------------


def test_live_trading_always_denied():
    """The no_live_trading safety invariant always passes (i.e. live trading is blocked)."""
    engine = _engine()
    for action_method in [
        engine.can_build_evidence,
        engine.can_run_readiness,
        engine.can_execute_recovery_action,
        engine.can_run_supervisor,
        engine.can_run_supervisor_cycle,
        engine.can_skip_readiness_gate,
    ]:
        decision = action_method()
        rm = _rule_map(decision)
        assert "no_live_trading" in rm
        assert rm["no_live_trading"].status == AutonomousPolicyRuleStatus.PASS


def test_broker_live_always_denied():
    """The no_broker_live safety invariant always passes."""
    engine = _engine()
    decision = engine.can_run_supervisor()
    rm = _rule_map(decision)
    assert "no_broker_live" in rm
    assert rm["no_broker_live"].status == AutonomousPolicyRuleStatus.PASS
    assert decision.safety_flags["broker_live_execution_allowed"] is False
    assert decision.safety_flags["live_trading_enabled"] is False


def test_order_send_mt5_execution_denied():
    """The no_mt5_order_execution and no_order_send invariants always pass."""
    engine = _engine()
    decision = engine.can_run_supervisor()
    rm = _rule_map(decision)
    assert "no_mt5_order_execution" in rm
    assert rm["no_mt5_order_execution"].status == AutonomousPolicyRuleStatus.PASS
    assert "no_order_send" in rm
    assert rm["no_order_send"].status == AutonomousPolicyRuleStatus.PASS
    assert decision.safety_flags["mt5_order_execution_allowed"] is False
    assert decision.safety_flags["order_send_called"] is False
    assert decision.safety_flags["broker_order_submission_allowed"] is False


# ---------------------------------------------------------------------------
# 6. Serialization
# ---------------------------------------------------------------------------


def test_policy_decisions_serialize_to_json(tmp_path: Path):
    """Policy decision exports to JSON, and the JSON is valid and re-parseable."""
    engine = _engine()
    decision = engine.can_build_evidence()

    json_path = export_autonomous_policy_json(decision, tmp_path)
    assert json_path.exists()
    assert json_path.name == "autonomous_policy_report.json"

    raw = json_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)

    assert parsed["allowed"] is True
    assert parsed["action"] == "build_evidence"
    assert parsed["mode"] == "DRY_RUN"
    assert parsed["decision"] == "ALLOW"
    assert "rule_results" in parsed
    assert "safety_flags" in parsed
    assert isinstance(parsed["rule_results"], list)
    assert isinstance(parsed["safety_flags"], dict)


def test_policy_decisions_serialize_to_txt(tmp_path: Path):
    """Policy decision exports to a human-readable TXT report."""
    engine = _engine()
    decision = engine.can_run_supervisor()

    txt_path = export_autonomous_policy_txt(decision, tmp_path)
    assert txt_path.exists()
    assert txt_path.name == "autonomous_policy_report.txt"

    content = txt_path.read_text(encoding="utf-8")
    assert "Autonomous Policy Engine Report" in content
    assert "paper/demo/read-only" in content
    assert "action: run_supervisor" in content
    assert "mode: DRY_RUN" in content
    assert "decision: ALLOW" in content
    assert "allowed: true" in content
    assert "rule_results:" in content
    assert "safety_flags:" in content
    assert "paper_demo_only: True" in content
    assert "live_trading_enabled: False" in content


# ---------------------------------------------------------------------------
# 7. evaluate_action dispatch
# ---------------------------------------------------------------------------


def test_evaluate_action_dispatches_correctly():
    """evaluate_action dispatches to the correct domain rules for known actions."""
    engine = _engine()
    known_actions = [
        "build_evidence",
        "run_readiness",
        "execute_recovery_action",
        "run_supervisor",
        "run_supervisor_cycle",
        "skip_readiness_gate",
    ]
    for action in known_actions:
        decision = engine.evaluate_action(action)
        assert decision.action == action
        assert decision.mode == AutonomousPolicyMode.DRY_RUN
        # Every decision has safety invariants
        rn = _rule_names(decision)
        for invariant in SAFETY_INVARIANT_NAMES:
            assert invariant in rn, f"missing invariant {invariant} for action {action}"

    # Unknown action — should still produce a valid decision with safety checks
    decision_unknown = engine.evaluate_action("totally_unknown_action")
    assert decision_unknown.action == "totally_unknown_action"
    assert decision_unknown.allowed is True  # no domain rules => no failures
    for invariant in SAFETY_INVARIANT_NAMES:
        assert invariant in _rule_names(decision_unknown)


# ---------------------------------------------------------------------------
# 8. Invariants always present / always safe
# ---------------------------------------------------------------------------


def test_safety_invariants_always_present():
    """Every decision contains all 11 safety invariants in its rule_results."""
    engine = _engine()
    decisions = [
        engine.can_build_evidence(),
        engine.can_run_readiness(),
        engine.can_execute_recovery_action(),
        engine.can_run_supervisor(),
        engine.can_run_supervisor_cycle(),
        engine.can_skip_readiness_gate(),
    ]
    for decision in decisions:
        rn = _rule_names(decision)
        for invariant in SAFETY_INVARIANT_NAMES:
            assert invariant in rn, (
                f"invariant '{invariant}' missing from decision for action '{decision.action}'"
            )
    # The constant itself must have exactly 11 entries
    assert len(SAFETY_INVARIANT_NAMES) == 11


def test_safety_flags_always_safe():
    """Every decision has the correct safety_flags dict blocking live operations."""
    engine = _engine()
    decisions = [
        engine.can_build_evidence(),
        engine.can_run_readiness(),
        engine.can_execute_recovery_action(),
        engine.can_run_supervisor(),
        engine.can_run_supervisor_cycle(),
        engine.can_skip_readiness_gate(),
    ]
    expected_flags = {
        "paper_demo_only": True,
        "live_trading_enabled": False,
        "live_execution_allowed": False,
        "broker_live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_order_execution_allowed": False,
        "order_send_called": False,
        "env_mutation_performed": False,
        "credentials_printed": False,
        "hidden_daemon_created": False,
        "infinite_loop_default": False,
        "readiness_bypass_for_non_dry_run": False,
        "recovery_overrides_readiness": False,
    }
    for decision in decisions:
        for key, expected_value in expected_flags.items():
            assert key in decision.safety_flags, (
                f"missing safety flag '{key}' in decision for '{decision.action}'"
            )
            assert decision.safety_flags[key] == expected_value, (
                f"safety flag '{key}' is {decision.safety_flags[key]} != {expected_value} "
                f"for action '{decision.action}'"
            )


# ---------------------------------------------------------------------------
# 9. Readiness bypass for non-dry-run denied
# ---------------------------------------------------------------------------


def test_readiness_bypass_non_dry_run_denied():
    """skip_readiness_gate=True with dry_run=False triggers the invariant FAIL."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.PAPER,
        dry_run=False,
        skip_readiness_gate=True,
    )
    engine = _engine(mode=AutonomousPolicyMode.PAPER, dry_run=False)
    decision = engine.can_skip_readiness_gate(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    # Safety invariant fires
    assert "no_readiness_bypass_for_non_dry_run" in rm
    assert rm["no_readiness_bypass_for_non_dry_run"].status == AutonomousPolicyRuleStatus.FAIL
    # Domain rule also fires
    assert "readiness_skip_denied" in rm
    assert rm["readiness_skip_denied"].status == AutonomousPolicyRuleStatus.FAIL


# ---------------------------------------------------------------------------
# 10. Recovery cannot override readiness
# ---------------------------------------------------------------------------


def test_recovery_cannot_override_readiness():
    """recovery_can_override_readiness=True triggers the invariant FAIL."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.DRY_RUN,
        dry_run=True,
        recovery_can_override_readiness=True,
    )
    engine = _engine(mode=AutonomousPolicyMode.DRY_RUN, dry_run=True)
    decision = engine.can_execute_recovery_action(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    assert "recovery_cannot_override_readiness" in rm
    assert rm["recovery_cannot_override_readiness"].status == AutonomousPolicyRuleStatus.FAIL


# ---------------------------------------------------------------------------
# 11. Missing evidence blocks non-dry-run paper
# ---------------------------------------------------------------------------


def test_missing_evidence_blocks_non_dry_run_paper():
    """evidence_status=BLOCKED_EVIDENCE with dry_run=False + mode=PAPER triggers invariant."""
    ctx = _ctx(
        mode=AutonomousPolicyMode.PAPER,
        dry_run=False,
        evidence_status="BLOCKED_EVIDENCE",
        readiness_status="READY",
        operator_mode="normal",
    )
    engine = _engine(mode=AutonomousPolicyMode.PAPER, dry_run=False)
    decision = engine.can_run_supervisor(ctx)
    assert decision.allowed is False
    assert decision.decision == AutonomousPolicyDecisionType.DENY
    rm = _rule_map(decision)
    # Safety invariant fires
    assert "missing_evidence_cannot_permit_non_dry_run_paper" in rm
    assert rm["missing_evidence_cannot_permit_non_dry_run_paper"].status == AutonomousPolicyRuleStatus.FAIL
    # Domain rule also fires
    assert "supervisor_evidence_blocking" in rm
    assert rm["supervisor_evidence_blocking"].status == AutonomousPolicyRuleStatus.FAIL


# ---------------------------------------------------------------------------
# 12. CLI report script
# ---------------------------------------------------------------------------


def test_cli_produces_reports(tmp_path: Path):
    """The autonomous_policy_report.py CLI produces JSON and TXT reports."""
    script = Path("scripts/autonomous_policy_report.py")
    if not script.exists():
        pytest.skip("scripts/autonomous_policy_report.py not found")

    import os

    env = os.environ.copy()
    env.update({
        "EXECUTION_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "BROKER_MODE": "paper",
        "AUTO_BOT_ENABLED": "false",
        "PYTHONPATH": str(Path.cwd()),
    })
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--action", "run_supervisor",
            "--mode", "dry-run",
            "--export-json",
            "--export-txt",
            "--reports-dir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, (
        f"CLI failed with code {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    json_file = tmp_path / "autonomous_policy_report.json"
    txt_file = tmp_path / "autonomous_policy_report.txt"
    assert json_file.exists(), f"JSON report not found at {json_file}"
    assert txt_file.exists(), f"TXT report not found at {txt_file}"

    # Verify JSON is valid
    parsed = json.loads(json_file.read_text(encoding="utf-8"))
    assert parsed["action"] == "run_supervisor"
    assert parsed["allowed"] is True

    # Verify TXT has expected header
    txt_content = txt_file.read_text(encoding="utf-8")
    assert "Autonomous Policy Engine Report" in txt_content
