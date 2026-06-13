"""Tests for the read-only operator diagnostics module (issue #120)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.reporting.operator_diagnostics import (
    CMD_EVIDENCE,
    CMD_MT5_VALIDATION,
    CMD_READINESS,
    CMD_REALTIME_DRY_RUN,
    CMD_REALTIME_PAPER,
    CMD_SYNTHETIC,
    STATUS_BLOCKED,
    STATUS_READY,
    STATUS_REPORTS_MISSING,
    STATUS_STOP_AND_REVIEW,
    STOP_AND_REVIEW,
    build_last_block,
    build_last_decision,
    build_operator_diagnostics,
)

SAFE_COMMANDS = {
    CMD_EVIDENCE,
    CMD_READINESS,
    CMD_MT5_VALIDATION,
    CMD_SYNTHETIC,
    CMD_REALTIME_DRY_RUN,
    CMD_REALTIME_PAPER,
    STOP_AND_REVIEW,
}

NOW = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
FRESH = "2026-06-13T11:55:00+00:00"


def _write(reports_dir: Path, filename: str, payload) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def _assert_never_live(command: str) -> None:
    assert command in SAFE_COMMANDS
    lowered = command.lower()
    assert "broker_live" not in lowered
    assert "enable_live" not in lowered
    assert "mt5_demo" not in lowered
    assert "--live" not in lowered


# --------------------------------------------------------------------------- #
# Missing / empty / malformed
# --------------------------------------------------------------------------- #
def test_missing_reports_directory(tmp_path):
    diag = build_operator_diagnostics(tmp_path / "does_not_exist", now=NOW)
    assert diag["overall_status"] == STATUS_REPORTS_MISSING
    assert diag["available_reports"] == []
    assert diag["blockers"] == []
    assert diag["confidence"] == "low"
    _assert_never_live(diag["next_safe_command"])


def test_empty_reports_directory(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["overall_status"] == STATUS_REPORTS_MISSING
    assert diag["next_safe_command"] == CMD_EVIDENCE
    _assert_never_live(diag["next_safe_command"])


def test_malformed_json_is_tolerated(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "autonomous_readiness_report.json").write_text("{ this is not valid json", encoding="utf-8")
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert "autonomous_readiness_report.json" in diag["unreadable_reports"]
    # No crash, no false blocker invented from a malformed file.
    assert all(b["source_report"] != "autonomous_readiness_report.json" for b in diag["blockers"])


def test_partial_invalid_jsonl_is_tolerated(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    good = {"logical_symbol": "EUR/USD", "decision": "rejected", "status": "watchlist", "score": 63.5, "rejection_reasons": ["score below minimum"]}
    (tmp_path / "signal_journal.jsonl").write_text("not-json\n" + json.dumps(good) + "\n", encoding="utf-8")
    decision = build_last_decision(tmp_path)
    assert decision["source"] == "signal_journal.jsonl"
    assert decision["decision"]["symbol"] == "EUR/USD"


# --------------------------------------------------------------------------- #
# Blocker scenarios
# --------------------------------------------------------------------------- #
def test_readiness_blocked(tmp_path):
    _write(tmp_path, "autonomous_readiness_report.json", {
        "final_status": "BLOCKED_BY_DATA_QUALITY",
        "blocking_reasons": ["data quality 40 below threshold"],
        "generated_at": FRESH,
    })
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["overall_status"] == STATUS_BLOCKED
    assert diag["blocker_category"] == "readiness"
    assert diag["next_safe_command"] == CMD_READINESS
    _assert_never_live(diag["next_safe_command"])


def test_mt5_stale_data_blocked(tmp_path):
    _write(tmp_path, "local_mt5_realtime_validation.json", {
        "final_status": "BLOCKED_STALE_DATA",
        "blocking_reasons": ["EUR/USD M1 candle age 900s exceeds limit"],
        "generated_at": FRESH,
    })
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["overall_status"] == STATUS_BLOCKED
    assert diag["blocker_category"] == "stale_data"
    assert diag["next_safe_command"] == CMD_SYNTHETIC
    block = build_last_block(diag)
    assert block["last_block"]["code"] == "BLOCKED_STALE_DATA"
    _assert_never_live(diag["next_safe_command"])


def test_mt5_spread_too_wide_blocked(tmp_path):
    _write(tmp_path, "local_mt5_realtime_validation.json", {
        "final_status": "BLOCKED_SPREAD_TOO_WIDE",
        "blocking_reasons": ["EUR/USD spread/ATR 0.6 above 0.25"],
        "generated_at": FRESH,
    })
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["blocker_category"] == "spread"
    assert diag["next_safe_command"] == CMD_SYNTHETIC
    _assert_never_live(diag["next_safe_command"])


def test_data_health_blocked_does_not_recommend_realtime(tmp_path):
    _write(tmp_path, "data_health_report.json", {"data_quality_status": "BLOCKED", "generated_at": FRESH})
    _write(tmp_path, "autonomous_readiness_report.json", {"final_status": "READY", "generated_at": FRESH})
    _write(tmp_path, "autonomous_evidence_summary.json", {"final_status": "READY_EVIDENCE", "generated_at": FRESH})
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["overall_status"] == STATUS_BLOCKED
    command = diag["next_safe_command"]
    assert command == CMD_SYNTHETIC
    assert command not in {CMD_REALTIME_PAPER, CMD_REALTIME_DRY_RUN}
    _assert_never_live(command)


# --------------------------------------------------------------------------- #
# Clean states
# --------------------------------------------------------------------------- #
def _clean_reports(tmp_path: Path, *, supervisor=None) -> None:
    _write(tmp_path, "autonomous_readiness_report.json", {"final_status": "READY", "generated_at": FRESH})
    _write(tmp_path, "autonomous_evidence_summary.json", {"final_status": "READY_EVIDENCE", "generated_at": FRESH})
    _write(tmp_path, "data_health_report.json", {"data_quality_status": "HEALTHY", "generated_at": FRESH})
    _write(tmp_path, "local_mt5_realtime_validation.json", {"final_status": "MT5_REALTIME_READY", "generated_at": FRESH})
    if supervisor is not None:
        _write(tmp_path, "realtime_paper_supervisor_summary.json", supervisor)


def test_clean_state_recommends_dry_run(tmp_path):
    _clean_reports(tmp_path)
    _write(tmp_path, "realtime_paper_supervisor_summary.json", {
        "stop_reason": "COMPLETED_MAX_CYCLES", "cycles_completed": 0, "dry_run": True,
        "safety_flags": {"live_execution_allowed": False}, "completed_at": FRESH,
    })
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    # cycles_completed == 0 yields a warning, so it is not the pristine READY path,
    # but no hard blocker exists and readiness/data health are clean.
    assert not any(b["severity"] in ("blocked", "review") for b in diag["blockers"])
    command = diag["next_safe_command"]
    assert command in {CMD_REALTIME_DRY_RUN, CMD_REALTIME_PAPER}
    _assert_never_live(command)


def test_clean_state_with_prior_dry_run_allows_paper_run(tmp_path):
    _clean_reports(tmp_path)
    _write(tmp_path, "realtime_paper_supervisor_summary.json", {
        "stop_reason": "COMPLETED_MAX_CYCLES", "cycles_completed": 5, "dry_run": True,
        "safety_flags": {"live_execution_allowed": False}, "completed_at": FRESH,
    })
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["overall_status"] == STATUS_READY
    assert diag["next_safe_command"] == CMD_REALTIME_PAPER
    _assert_never_live(diag["next_safe_command"])


def test_missing_mt5_validation_recommends_validation(tmp_path):
    _write(tmp_path, "autonomous_readiness_report.json", {"final_status": "READY", "generated_at": FRESH})
    _write(tmp_path, "autonomous_evidence_summary.json", {"final_status": "READY_EVIDENCE", "generated_at": FRESH})
    _write(tmp_path, "data_health_report.json", {"data_quality_status": "HEALTHY", "generated_at": FRESH})
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["next_safe_command"] == CMD_MT5_VALIDATION
    _assert_never_live(diag["next_safe_command"])


# --------------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------------- #
def test_unsafe_safety_flag_triggers_stop_and_review(tmp_path):
    _write(tmp_path, "realtime_paper_supervisor_summary.json", {
        "stop_reason": "COMPLETED_MAX_CYCLES",
        "safety_flags": {"order_send_called": True},
        "completed_at": FRESH,
    })
    diag = build_operator_diagnostics(tmp_path, now=NOW)
    assert diag["overall_status"] == STATUS_STOP_AND_REVIEW
    assert diag["next_safe_command"] == STOP_AND_REVIEW
    assert diag["safety_summary"]["unsafe_flags_detected"]


def test_next_safe_command_never_recommends_live_trading(tmp_path):
    scenarios = [
        {},
        {"autonomous_readiness_report.json": {"final_status": "BLOCKED_BY_RISK", "generated_at": FRESH}},
        {"local_mt5_realtime_validation.json": {"final_status": "BLOCKED_STALE_DATA", "generated_at": FRESH}},
        {"data_health_report.json": {"data_quality_status": "BLOCKED", "generated_at": FRESH}},
        {"realtime_paper_supervisor_summary.json": {"stop_reason": "BLOCKED_BY_SAFETY_DRIFT", "safety_flags": {"live_execution_allowed": True}, "completed_at": FRESH}},
    ]
    for scenario in scenarios:
        reports = tmp_path / f"s{scenarios.index(scenario)}"
        for filename, payload in scenario.items():
            _write(reports, filename, payload)
        diag = build_operator_diagnostics(reports, now=NOW)
        _assert_never_live(diag["next_safe_command"])


# --------------------------------------------------------------------------- #
# explain_last_decision
# --------------------------------------------------------------------------- #
def test_explain_last_decision_without_decision_trace(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "score_decomposition.json", [{
        "symbol": "EUR/USD", "status": "watchlist", "final_score": 63.5, "active_min_score": 75.0,
        "rejection_reasons": ["score below minimum"],
    }])
    decision = build_last_decision(tmp_path)
    assert decision["source"] == "score_decomposition.json"
    assert decision["decision"]["final_score"] == 63.5
    assert "decision_trace.json was not available" in decision["human_explanation"]


def test_explain_last_decision_uses_decision_trace_when_present(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    _write(tmp_path, "decision_trace.json", [{
        "symbol": "EUR/USD", "style": "day_trading", "status": "approved", "accepted": True,
        "final_score": 82.0, "active_min_score": 75.0, "order_ids": ["paper-1"],
        "gate_results": [{"name": "final score", "passed": True}],
        "rejection_reasons": [], "primary_rejection_reason": None,
    }])
    decision = build_last_decision(tmp_path)
    assert decision["source"] == "decision_trace.json"
    assert decision["decision"]["accepted"] is True
    assert "ACCEPTED" in decision["human_explanation"]


def test_explain_last_decision_no_artifacts(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    decision = build_last_decision(tmp_path)
    assert decision["source"] is None
    assert decision["next_safe_command"] == CMD_SYNTHETIC
