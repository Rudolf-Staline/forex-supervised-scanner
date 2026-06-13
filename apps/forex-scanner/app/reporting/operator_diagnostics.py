"""Read-only operator diagnostics over local paper/demo report artifacts.

This module never runs trading logic, never imports the MT5 terminal API, never
calls ``order_send``, never mutates ``.env``, and works fully offline from files
in ``reports/``. It tolerates a missing reports directory, missing files, empty
files, malformed JSON, and partially invalid JSONL.

It produces a single normalized diagnostic that powers the operator CLIs:
``decision_doctor.py``, ``next_safe_bot_command.py``, ``explain_last_block.py``,
and ``explain_last_decision.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAFETY_BANNER = (
    "SAFETY: read-only operator diagnostics; paper/demo only; "
    "no live trading; no broker-live execution; no order_send; no .env mutation."
)

# --------------------------------------------------------------------------- #
# Recommended safe, bounded commands (paper/demo only). None authorize live trading.
# --------------------------------------------------------------------------- #
CMD_EVIDENCE = "python scripts/autonomous_evidence_builder.py --mode read-only --include-readiness --export-json --export-txt"
CMD_READINESS = "python scripts/autonomous_readiness_report.py --build-evidence-first --evidence-mode read-only --export-json --export-txt"
CMD_MT5_VALIDATION = "python scripts/local_mt5_realtime_validation.py --symbols EUR/USD --timeframes M1 M5 --duration-minutes 0 --interval-seconds 0 --export-json --export-txt --export-csv"
CMD_SYNTHETIC = "python scripts/run_one_cycle.py --provider synthetic --broker paper --symbols EUR/USD"
CMD_REALTIME_DRY_RUN = (
    "python scripts/realtime_paper_supervisor.py --provider mt5 --symbols EUR/USD --timeframe M1 "
    "--interval-seconds 60 --max-cycles 5 --dry-run --build-evidence-first --plan-recovery-on-block "
    "--manage-positions --export-json --export-txt"
)
CMD_REALTIME_PAPER = (
    "python scripts/realtime_paper_supervisor.py --provider mt5 --symbols EUR/USD --timeframe M1 "
    "--interval-seconds 60 --max-cycles 5 --build-evidence-first --plan-recovery-on-block "
    "--manage-positions --export-json --export-txt"
)
CMD_FAILURE_DIAGNOSTICS = "python scripts/failure_diagnostics_report.py --export-json --export-txt"
STOP_AND_REVIEW = "STOP_AND_REVIEW"

# --------------------------------------------------------------------------- #
# Overall status values
# --------------------------------------------------------------------------- #
STATUS_STOP_AND_REVIEW = "STOP_AND_REVIEW"
STATUS_BLOCKED = "BLOCKED"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
STATUS_WARN = "WARN"
STATUS_REPORTS_MISSING = "REPORTS_MISSING"
STATUS_READY = "READY_FOR_PAPER_DIAGNOSTIC"

# Severity ranking for sorting blockers (lower is more severe).
_SEVERITY_RANK = {"blocked": 0, "review": 1, "warning": 2, "info": 3}

# Category priority for picking the primary blocker (lower is higher priority).
_CATEGORY_PRIORITY = {
    "safety": 0,
    "stale_data": 1,
    "spread": 2,
    "data_health": 3,
    "readiness": 4,
    "operator": 4,
    "session_health": 5,
    "evidence": 6,
    "policy": 6,
    "supervisor": 7,
    "mt5_data": 8,
    "command_center": 9,
    "symbol_mapping": 10,
    "failure_diagnostics": 11,
    "paper_review": 12,
    "trends": 13,
    "score": 14,
    "reports_missing": 15,
    "unknown": 16,
}

# Report registry: key -> filename. Superset of the issue's inspect list plus a
# few artifacts used only for explaining the last decision/block.
REPORT_FILES: dict[str, str] = {
    "decision_trace": "decision_trace.json",
    "score_decomposition": "score_decomposition.json",
    "min_score_policy": "min_score_policy_report.json",
    "readiness": "autonomous_readiness_report.json",
    "evidence": "autonomous_evidence_summary.json",
    "recovery": "autonomous_recovery_plan.json",
    "data_health": "data_health_report.json",
    "session_health": "session_health_summary.json",
    "failure_diagnostics": "failure_diagnostics_summary.json",
    "mt5_validation": "local_mt5_realtime_validation.json",
    "supervisor": "realtime_paper_supervisor_summary.json",
    "command_center": "realtime_command_center_summary.json",
    "heartbeat": "realtime_heartbeat.jsonl",
    "paper_review": "paper_session_review_summary.json",
    "paper_history": "paper_session_history_summary.json",
    "paper_trends": "paper_session_trends_summary.json",
    "symbol_mapping": "mt5_symbol_mapping_audit.json",
    "signal_journal": "signal_journal.jsonl",
    "autonomous_supervisor": "autonomous_supervisor_summary.json",
}

_JSONL_REPORTS = {"heartbeat", "signal_journal"}

# Reports whose absence is worth flagging (drives REPORTS_MISSING + confidence).
_KEY_REPORTS = ("readiness", "evidence", "data_health", "mt5_validation", "supervisor")

_TIMESTAMP_KEYS = ("completed_at", "generated_at", "heartbeat_at", "recorded_at", "timestamp", "started_at")

_UNSAFE_SOURCE_FLAGS = (
    "live_execution_allowed",
    "live_trading_enabled",
    "broker_live_execution_allowed",
    "broker_order_submission_allowed",
    "mt5_order_execution_allowed",
    "order_send_called",
    "env_mutation_performed",
    "env_mutated",
    "hidden_daemon_created",
)


def _blocker(code: str, category: str, severity: str, source: str, raw: str, explanation: str, action: str) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "severity": severity,
        "source_report": source,
        "raw_blocker": raw,
        "human_explanation": explanation,
        "safe_next_action": action,
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def build_operator_diagnostics(reports_dir: Path | str, *, now: datetime | None = None, max_age_hours: float = 24.0) -> dict[str, Any]:
    """Inspect ``reports/`` and return a normalized operator diagnostic."""

    now = now or datetime.now(timezone.utc)
    reports_dir = Path(reports_dir)

    payloads: dict[str, Any] = {}
    available: list[str] = []
    missing: list[str] = []
    unreadable: list[str] = []
    stale: list[str] = []
    report_ages: dict[str, float | None] = {}

    for key, filename in REPORT_FILES.items():
        path = reports_dir / filename
        if not path.is_file():
            payloads[key] = None
            missing.append(filename)
            continue
        payload = _read_jsonl(path) if key in _JSONL_REPORTS else _read_json(path)
        payloads[key] = payload
        if payload is None or (isinstance(payload, list) and not payload and key in _JSONL_REPORTS):
            # File exists but is empty or malformed.
            if payload is None:
                unreadable.append(filename)
            else:
                available.append(filename)
            report_ages[filename] = None
            continue
        available.append(filename)
        timestamp = _report_timestamp(payload)
        if timestamp is not None:
            age_hours = round((now - timestamp).total_seconds() / 3600.0, 4)
            report_ages[filename] = age_hours
            if age_hours > max_age_hours:
                stale.append(filename)
        else:
            report_ages[filename] = None

    blockers: list[dict[str, Any]] = []
    warnings: list[str] = []

    safety_summary = _build_safety_summary(payloads)
    if safety_summary["unsafe_flags_detected"]:
        for flag in safety_summary["unsafe_flags_detected"]:
            blockers.append(
                _blocker(
                    "UNSAFE_SAFETY_FLAG",
                    "safety",
                    "blocked",
                    flag["source_report"],
                    f"{flag['flag']}=true",
                    f"An unsafe safety flag ({flag['flag']}) is true in {flag['source_report']}; paper/demo-only operation may be violated.",
                    "Stop and review. Only run read-only diagnostics until the unsafe flag is resolved.",
                )
            )

    # Per-report evaluators (most informative first).
    _eval_mt5_validation(payloads.get("mt5_validation"), blockers, warnings)
    _eval_data_health(payloads.get("data_health"), blockers, warnings)
    _eval_readiness(payloads.get("readiness"), blockers, warnings)
    _eval_session_health(payloads.get("session_health"), blockers, warnings)
    _eval_evidence(payloads.get("evidence"), blockers, warnings)
    _eval_supervisor(payloads.get("supervisor"), blockers, warnings)
    _eval_command_center(payloads.get("command_center"), blockers, warnings)
    _eval_symbol_mapping(payloads.get("symbol_mapping"), blockers, warnings)
    _eval_failure_diagnostics(payloads.get("failure_diagnostics"), blockers, warnings)
    _eval_paper_review(payloads.get("paper_review"), blockers, warnings)
    _eval_paper_trends(payloads.get("paper_trends"), blockers, warnings)
    _eval_decision_trace(payloads.get("decision_trace"), payloads.get("score_decomposition"), blockers, warnings)

    for filename in unreadable:
        warnings.append(f"report could not be parsed (ignored): {filename}")
    for filename in stale:
        warnings.append(f"report is stale (older than {max_age_hours:g}h): {filename}")

    blockers = _dedupe_blockers(blockers)
    blockers.sort(key=lambda b: (_SEVERITY_RANK.get(b["severity"], 9), _CATEGORY_PRIORITY.get(b["category"], 99)))

    hard_blockers = [b for b in blockers if b["severity"] in ("blocked", "review")]
    primary = blockers[0] if blockers else None

    overall_status = _overall_status(safety_summary, hard_blockers, blockers, warnings, available, missing)
    next_command, next_reason = _decide_next_safe_command(
        safety_summary=safety_summary,
        blockers=blockers,
        payloads=payloads,
        missing=missing,
        stale=stale,
    )
    confidence = _confidence(available, primary, safety_summary)

    return {
        "generated_at": now.isoformat(),
        "reports_dir": str(reports_dir),
        "max_age_hours": max_age_hours,
        "overall_status": overall_status,
        "primary_blocker": primary["raw_blocker"] if primary else None,
        "blocker_category": primary["category"] if primary else "none",
        "blockers": blockers,
        "warnings": _dedupe(warnings),
        "missing_reports": missing,
        "stale_reports": stale,
        "unreadable_reports": unreadable,
        "available_reports": available,
        "report_ages_hours": report_ages,
        "safety_summary": safety_summary,
        "next_safe_command": next_command,
        "next_safe_command_reason": next_reason,
        "confidence": confidence,
    }


def build_last_block(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Summarize the most recent hard blocker from a diagnostic object."""

    blockers = diagnostics.get("blockers") or []
    hard = [b for b in blockers if b.get("severity") in ("blocked", "review")]
    primary = hard[0] if hard else (blockers[0] if blockers else None)
    return {
        "generated_at": diagnostics.get("generated_at"),
        "reports_dir": diagnostics.get("reports_dir"),
        "overall_status": diagnostics.get("overall_status"),
        "has_block": primary is not None,
        "last_block": primary,
        "all_blockers": blockers,
        "safe_next_action": (primary or {}).get("safe_next_action") or diagnostics.get("next_safe_command_reason"),
        "next_safe_command": diagnostics.get("next_safe_command"),
        "safety_summary": diagnostics.get("safety_summary"),
    }


def build_last_decision(reports_dir: Path | str) -> dict[str, Any]:
    """Explain the last opportunity decision, tolerating missing decision traces."""

    reports_dir = Path(reports_dir)
    available_sources: list[str] = []
    for key in ("decision_trace", "score_decomposition", "signal_journal", "autonomous_supervisor"):
        if (reports_dir / REPORT_FILES[key]).is_file():
            available_sources.append(REPORT_FILES[key])

    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports_dir": str(reports_dir),
        "source": None,
        "available_sources": available_sources,
        "decision": None,
        "human_explanation": "No decision artifacts are available yet. Run a synthetic paper diagnostic to generate one.",
        "next_safe_command": CMD_SYNTHETIC,
        "safety_summary_note": "paper/demo only; passing diagnostics never authorizes live trading",
    }

    traces = _read_json(reports_dir / REPORT_FILES["decision_trace"])
    if isinstance(traces, list) and traces:
        trace = traces[-1]
        if isinstance(trace, dict):
            failed = [g.get("name") for g in trace.get("gate_results", []) if isinstance(g, dict) and g.get("passed") is False]
            result.update(
                source="decision_trace.json",
                decision={
                    "symbol": trace.get("symbol"),
                    "style": trace.get("style"),
                    "status": trace.get("status"),
                    "accepted": trace.get("accepted"),
                    "final_score": trace.get("final_score"),
                    "active_min_score": trace.get("active_min_score"),
                    "primary_rejection_reason": trace.get("primary_rejection_reason"),
                    "rejection_reasons": trace.get("rejection_reasons") or [],
                    "failed_gates": failed,
                    "order_ids": trace.get("order_ids") or [],
                },
                human_explanation=_explain_trace(trace, failed),
                next_safe_command=CMD_SYNTHETIC,
            )
            return result

    decomp = _read_json(reports_dir / REPORT_FILES["score_decomposition"])
    if isinstance(decomp, list) and decomp and isinstance(decomp[-1], dict):
        item = decomp[-1]
        result.update(
            source="score_decomposition.json",
            decision={
                "symbol": item.get("symbol"),
                "status": item.get("status"),
                "final_score": item.get("final_score"),
                "active_min_score": item.get("active_min_score"),
                "rejection_reasons": item.get("rejection_reasons") or [],
            },
            human_explanation=(
                f"decision_trace.json was not available; using score_decomposition.json. "
                f"{item.get('symbol')} final_score={item.get('final_score')} vs active_min_score={item.get('active_min_score')}."
            ),
        )
        return result

    journal = _read_jsonl(reports_dir / REPORT_FILES["signal_journal"])
    if isinstance(journal, list) and journal:
        record = journal[-1]
        result.update(
            source="signal_journal.jsonl",
            decision={
                "symbol": record.get("logical_symbol"),
                "status": record.get("status"),
                "decision": record.get("decision"),
                "score": record.get("score"),
                "rejection_reasons": record.get("rejection_reasons") or [],
                "created_order": record.get("created_order"),
                "order_ids": record.get("order_ids") or [],
            },
            human_explanation=(
                f"decision_trace.json was not available; using signal_journal.jsonl. "
                f"{record.get('logical_symbol')} decision={record.get('decision')} score={record.get('score')}."
            ),
        )
        return result

    supervisor = _read_json(reports_dir / REPORT_FILES["autonomous_supervisor"])
    if isinstance(supervisor, dict):
        result.update(
            source="autonomous_supervisor_summary.json",
            decision={"final_status": supervisor.get("final_status") or supervisor.get("stop_reason")},
            human_explanation="decision_trace.json was not available; the autonomous supervisor summary is the only decision-related artifact.",
        )
        return result

    return result


# --------------------------------------------------------------------------- #
# Per-report evaluators
# --------------------------------------------------------------------------- #
def _eval_mt5_validation(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("final_status") or "UNKNOWN")
    reasons = "; ".join(_str_list(payload.get("blocking_reasons"))) or status
    src = REPORT_FILES["mt5_validation"]
    if status == "MT5_REALTIME_READY":
        return
    if status == "MT5_REALTIME_WARN":
        warnings.append(f"mt5 validation warnings: {'; '.join(_str_list(payload.get('warnings'))) or 'see report'}")
        return
    if status == "BLOCKED_STALE_DATA":
        blockers.append(_blocker("BLOCKED_STALE_DATA", "stale_data", "blocked", src, reasons,
            "MT5 realtime candles are stale (last candle too old). Live-quality data is not available right now.",
            "Wait for the market to open, or run a synthetic paper diagnostic. Do not bypass the staleness check."))
        return
    if status == "BLOCKED_SPREAD_TOO_WIDE":
        blockers.append(_blocker("BLOCKED_SPREAD_TOO_WIDE", "spread", "blocked", src, reasons,
            "MT5 spread/ATR is above the configured maximum. Execution friction is too high for paper/demo entries.",
            "Wait for spreads to normalize (e.g. main session), or run a synthetic paper diagnostic. Do not relax the spread gate."))
        return
    blockers.append(_blocker(status, "mt5_data", "blocked", src, reasons,
        f"MT5 realtime validation is blocked: {status}.",
        "Run the local MT5 realtime validation again, or use a synthetic paper diagnostic if MT5 is unavailable."))


def _eval_data_health(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("data_quality_status") or payload.get("status") or "UNKNOWN")
    src = REPORT_FILES["data_health"]
    if status in ("HEALTHY", "UNKNOWN"):
        return
    if status in ("WARN", "DEGRADED"):
        warnings.append(f"data health {status}: {'; '.join(_str_list(payload.get('recommendations'))) or 'see report'}")
        return
    blockers.append(_blocker("BLOCKED_DATA_HEALTH", "data_health", "blocked", src, status,
        "Data health is blocked (stale/empty/missing or malformed report inputs).",
        "Regenerate clean reports and re-check data health; do not run realtime non-dry-run while data health is blocked."))


def _eval_readiness(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("final_status") or "UNKNOWN")
    reasons = "; ".join(_str_list(payload.get("blocking_reasons"))) or status
    src = REPORT_FILES["readiness"]
    if status == "READY":
        return
    if status in ("WARN_READY", "UNKNOWN"):
        warnings.append(f"readiness requires review: {status}")
        return
    if status == "BLOCKED_BY_SESSION_HEALTH":
        blockers.append(_blocker("BLOCKED_BY_SESSION_HEALTH", "session_health", "blocked", src, reasons,
            "Readiness is blocked by session health (likely off-hours or low-quality session).",
            "Wait for a healthy trading session, then rebuild evidence read-only and re-check readiness."))
        return
    if status == "BLOCKED_BY_NO_EVIDENCE":
        blockers.append(_blocker("BLOCKED_BY_NO_EVIDENCE", "evidence", "blocked", src, reasons,
            "Readiness is blocked because no readiness evidence is available.",
            "Build evidence read-only first, then re-run the readiness report."))
        return
    blockers.append(_blocker(status, "readiness", "blocked", src, reasons,
        f"Readiness gate is blocked: {status}.",
        "Rebuild evidence read-only and re-run the readiness report; do not bypass readiness."))


def _eval_session_health(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("overall_status") or "UNKNOWN")
    src = REPORT_FILES["session_health"]
    if status in ("HEALTHY", "UNKNOWN"):
        return
    if status in ("WARN", "DEGRADED"):
        warnings.append(f"session health {status}")
        return
    blockers.append(_blocker("BLOCKED_BY_SESSION_HEALTH", "session_health", "blocked", src, status,
        "Session health is blocked (off-hours or unhealthy session distribution).",
        "Wait for a healthy session window before paper/demo operation."))


def _eval_evidence(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("final_status") or "UNKNOWN")
    src = REPORT_FILES["evidence"]
    if status in ("READY_EVIDENCE", "UNKNOWN"):
        return
    if status in ("WARN_EVIDENCE", "DRY_RUN_PLAN"):
        warnings.append(f"evidence requires review: {status}")
        return
    blockers.append(_blocker("BLOCKED_EVIDENCE", "evidence", "blocked", src, "; ".join(_str_list(payload.get("blocking_failures"))) or status,
        "Readiness evidence is blocked or incomplete.",
        "Rebuild evidence read-only with the evidence builder before continuing."))


def _eval_supervisor(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    stop = str(payload.get("stop_reason") or "UNKNOWN")
    reasons = "; ".join(_str_list(payload.get("blocking_reasons"))) or stop
    src = REPORT_FILES["supervisor"]
    if stop in ("COMPLETED_MAX_CYCLES", "COMPLETED_MAX_RUNTIME", "UNKNOWN"):
        if int(payload.get("cycles_completed", 0) or 0) == 0 and stop != "UNKNOWN":
            warnings.append("last supervisor run completed 0 cycles")
        return
    category = {
        "BLOCKED_STALE_DATA": "stale_data",
        "BLOCKED_DATA_HEALTH": "data_health",
        "BLOCKED_BY_READINESS": "readiness",
        "BLOCKED_BY_EVIDENCE": "evidence",
        "BLOCKED_BY_SAFETY_DRIFT": "safety",
        "BLOCKED_BY_OPERATOR_CONTROL": "operator",
        "BLOCKED_BY_POLICY": "policy",
        "BLOCKED_SYNTHETIC_FALLBACK": "mt5_data",
        "BLOCKED_BY_PROVIDER_FAILURES": "mt5_data",
    }.get(stop, "supervisor")
    blockers.append(_blocker(stop, category, "blocked", src, reasons,
        f"The last realtime paper supervisor run stopped on {stop} before completing its cycles.",
        "Resolve the underlying blocker (evidence/readiness/data health), then re-run the supervisor in --dry-run mode."))


def _eval_command_center(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("final_status") or "UNKNOWN")
    src = REPORT_FILES["command_center"]
    if status in ("COMPLETED", "UNKNOWN"):
        return
    if status == "WARN":
        warnings.append(f"command center warnings: {'; '.join(_str_list(payload.get('warnings'))) or 'see report'}")
        return
    blockers.append(_blocker("COMMAND_CENTER_BLOCKED", "command_center", "blocked", src,
        "; ".join(_str_list(payload.get("blocking_reasons"))) or status,
        "The realtime command center reported a blocked stage.",
        "Review the command center blocking reasons and resolve them before realtime operation."))


def _eval_symbol_mapping(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("mapping_status") or "UNKNOWN")
    src = REPORT_FILES["symbol_mapping"]
    if status in ("OK", "CLEAN", "UNKNOWN"):
        return
    if status == "WARN":
        warnings.append("mt5 symbol mapping has warnings")
        return
    blockers.append(_blocker("MT5_SYMBOL_MAPPING_NEEDS_REVIEW", "symbol_mapping", "review", src, status,
        "MT5 symbol mapping needs review (missing or mismatched broker symbols).",
        "Run the MT5 symbol mapping audit and resolve mismatches before realtime MT5 operation."))


def _eval_failure_diagnostics(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    severity = str(payload.get("severity") or "UNKNOWN")
    src = REPORT_FILES["failure_diagnostics"]
    causes = "; ".join(_str_list(payload.get("likely_root_causes"))) or severity
    if severity in ("CLEAN", "UNKNOWN"):
        return
    if severity == "WARN":
        warnings.append(f"failure diagnostics warnings: {causes}")
        return
    sev = "blocked" if severity == "BLOCKED" else "review"
    blockers.append(_blocker("FAILURE_DIAGNOSTICS_NEEDS_REVIEW", "failure_diagnostics", sev, src, causes,
        "Failure diagnostics flagged recent command/test failures that need review.",
        "Open the failure diagnostics report and resolve the listed root causes."))


def _eval_paper_review(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("final_review_status") or "UNKNOWN")
    src = REPORT_FILES["paper_review"]
    if status in ("PAPER_SESSION_REVIEW_READY", "UNKNOWN"):
        return
    if status == "PAPER_SESSION_REVIEW_WARN":
        warnings.append("paper session review has warnings")
        return
    if status == "PAPER_SESSION_REVIEW_INCOMPLETE":
        blockers.append(_blocker("PAPER_SESSION_REVIEW_INCOMPLETE", "paper_review", "review", src, status,
            "The paper session review is incomplete (missing inputs).",
            "Regenerate the missing paper session reports, then re-run the paper session review."))
        return
    blockers.append(_blocker("PAPER_SESSION_REVIEW_BLOCKED", "paper_review", "blocked", src,
        "; ".join(_str_list(payload.get("blocking_reasons"))) or status,
        "The paper session review is blocked.",
        "Resolve the review blocking reasons before relying on the session review."))


def _eval_paper_trends(payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    status = str(payload.get("final_trends_status") or "UNKNOWN")
    src = REPORT_FILES["paper_trends"]
    if status in ("PAPER_SESSION_TRENDS_READY", "PAPER_SESSION_TRENDS_EMPTY", "UNKNOWN"):
        return
    if status == "PAPER_SESSION_TRENDS_WARN":
        blockers.append(_blocker("PAPER_SESSION_TRENDS_WARN", "trends", "review", src, status,
            "Paper session trends show a warning direction (e.g. declining win rate or realized R).",
            "Review the trend report; consider more paper sessions before changing anything. This never authorizes live trading."))
        return
    blockers.append(_blocker("PAPER_SESSION_TRENDS_BLOCKED", "trends", "blocked", src,
        "; ".join(_str_list(payload.get("unsafe_flag_detections"))) or status,
        "Paper session trends are blocked (unsafe flag detected in history).",
        "Investigate the unsafe-flag detection in the session history before continuing."))


def _eval_decision_trace(trace_payload: Any, decomp_payload: Any, blockers: list[dict[str, Any]], warnings: list[str]) -> None:
    item: dict[str, Any] | None = None
    source = None
    if isinstance(trace_payload, list) and trace_payload and isinstance(trace_payload[-1], dict):
        item = trace_payload[-1]
        source = REPORT_FILES["decision_trace"]
    elif isinstance(decomp_payload, list) and decomp_payload and isinstance(decomp_payload[-1], dict):
        item = decomp_payload[-1]
        source = REPORT_FILES["score_decomposition"]
    if item is None:
        return
    accepted = item.get("accepted")
    if accepted is True:
        return
    reason = item.get("primary_rejection_reason") or "; ".join(_str_list(item.get("rejection_reasons"))) or "score/min-score gate"
    final = item.get("final_score")
    active_min = item.get("active_min_score")
    score_blocked = isinstance(final, (int, float)) and isinstance(active_min, (int, float)) and final < active_min
    blockers.append(_blocker(
        "SCORE_OR_MIN_SCORE_BLOCK" if score_blocked else "OPPORTUNITY_NOT_ACCEPTED",
        "score", "warning", source, str(reason),
        f"The last scanned opportunity was not accepted (final_score={final}, active_min_score={active_min}).",
        "This is normal scanner behavior. Inspect score_decomposition / min_score_policy to understand the gate; do not lower thresholds."))


# --------------------------------------------------------------------------- #
# Safety summary
# --------------------------------------------------------------------------- #
def _build_safety_summary(payloads: dict[str, Any]) -> dict[str, Any]:
    unsafe: list[dict[str, str]] = []
    for key, payload in payloads.items():
        records = payload if isinstance(payload, list) else [payload]
        for record in records:
            if not isinstance(record, dict):
                continue
            flags = record.get("safety_flags")
            candidates: dict[str, Any] = {}
            if isinstance(flags, dict):
                candidates.update(flags)
            # Some heartbeat/records expose the flag at top level too.
            if "live_execution_allowed" in record:
                candidates.setdefault("live_execution_allowed", record.get("live_execution_allowed"))
            for flag in _UNSAFE_SOURCE_FLAGS:
                if candidates.get(flag) is True:
                    unsafe.append({"flag": flag, "source_report": REPORT_FILES.get(key, key)})
    return {
        "paper_demo_only": True,
        "live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_order_execution_allowed": False,
        "order_send_called": False,
        "env_mutation_performed": False,
        "hidden_daemon_created": False,
        "infinite_loop_default": False,
        "unsafe_flags_detected": unsafe,
    }


# --------------------------------------------------------------------------- #
# Decision logic
# --------------------------------------------------------------------------- #
def _overall_status(safety_summary: dict[str, Any], hard_blockers: list[dict[str, Any]], blockers: list[dict[str, Any]], warnings: list[str], available: list[str], missing: list[str]) -> str:
    if safety_summary["unsafe_flags_detected"]:
        return STATUS_STOP_AND_REVIEW
    if any(b["severity"] == "blocked" for b in hard_blockers):
        return STATUS_BLOCKED
    if any(b["severity"] == "review" for b in hard_blockers):
        return STATUS_NEEDS_REVIEW
    if not available:
        return STATUS_REPORTS_MISSING
    if warnings:
        return STATUS_WARN
    if any((REPORT_FILES[key] in missing) for key in _KEY_REPORTS):
        return STATUS_REPORTS_MISSING
    return STATUS_READY


def _decide_next_safe_command(*, safety_summary: dict[str, Any], blockers: list[dict[str, Any]], payloads: dict[str, Any], missing: list[str], stale: list[str]) -> tuple[str, str]:
    if safety_summary["unsafe_flags_detected"]:
        return STOP_AND_REVIEW, "An unsafe safety flag was detected; stop and run only read-only diagnostics until it is resolved."

    categories = [b["category"] for b in blockers if b["severity"] in ("blocked", "review")]

    if "stale_data" in categories:
        return CMD_SYNTHETIC, "MT5 realtime data is stale; wait for market open or run a synthetic paper diagnostic (this does not bypass safety)."
    if "spread" in categories:
        return CMD_SYNTHETIC, "MT5 spread/ATR is too wide; wait for spreads to normalize or run a synthetic paper diagnostic (this does not relax the spread gate)."
    if "data_health" in categories:
        return CMD_SYNTHETIC, "Data health is blocked; use a synthetic paper diagnostic and do not run realtime non-dry-run."
    if "evidence" in categories:
        return CMD_EVIDENCE, "Readiness evidence is blocked or missing; rebuild it read-only first."
    if "readiness" in categories or "session_health" in categories:
        return CMD_READINESS, "Readiness is blocked; rebuild evidence read-only and re-check readiness."
    if categories:
        return CMD_EVIDENCE, "A blocker needs review; rebuild evidence read-only and re-run diagnostics before any realtime operation."

    # No hard blockers below this point.
    evidence_missing = REPORT_FILES["evidence"] in missing or REPORT_FILES["evidence"] in stale
    if evidence_missing:
        return CMD_EVIDENCE, "No fresh readiness evidence is available; build it read-only first."

    readiness_ready = _status_is(payloads.get("readiness"), "final_status", "READY")
    data_health_ok = _status_is(payloads.get("data_health"), "data_quality_status", "HEALTHY")

    if REPORT_FILES["mt5_validation"] in missing:
        return CMD_MT5_VALIDATION, "MT5 realtime validation has not been run; run a bounded read-only validation to confirm live-quality data."

    if readiness_ready and data_health_ok:
        if _clean_dry_run_completed(payloads.get("supervisor")):
            return CMD_REALTIME_PAPER, "Readiness READY, data health HEALTHY, safety flags clean, and a prior dry-run supervisor completed; a bounded realtime paper run is appropriate (still paper/demo only, never live)."
        return CMD_REALTIME_DRY_RUN, "Readiness READY and data health HEALTHY; a bounded realtime paper DRY-RUN is the safe next step."

    return CMD_SYNTHETIC, "No blockers, but realtime readiness/data health are unconfirmed; run a safe synthetic paper diagnostic."


def _clean_dry_run_completed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    stop = str(payload.get("stop_reason") or "")
    if stop not in ("COMPLETED_MAX_CYCLES", "COMPLETED_MAX_RUNTIME"):
        return False
    flags = payload.get("safety_flags")
    if isinstance(flags, dict) and any(flags.get(flag) is True for flag in _UNSAFE_SOURCE_FLAGS):
        return False
    # Only treat as "dry-run completed" when the prior run was explicitly a dry run.
    return bool(payload.get("dry_run", True))


def _confidence(available: list[str], primary: dict[str, Any] | None, safety_summary: dict[str, Any]) -> str:
    if safety_summary["unsafe_flags_detected"]:
        return "high"
    if not available:
        return "low"
    if primary is not None and primary.get("severity") in ("blocked", "review"):
        return "high"
    if len(available) >= 3:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_decision_doctor_txt(diag: dict[str, Any]) -> str:
    safety = diag["safety_summary"]
    ready = diag["overall_status"] == STATUS_READY
    lines = [
        SAFETY_BANNER,
        "OPERATOR DECISION DOCTOR (read-only, paper/demo only)",
        f"generated_at={diag['generated_at']}",
        f"reports_dir={diag['reports_dir']}",
        f"overall_status={diag['overall_status']}",
        f"confidence={diag['confidence']}",
        "",
        f"Q: Is the bot ready to run a paper/demo diagnostic? -> {'yes' if ready else 'no'}",
        f"Q: What is the primary blocker? -> {diag['primary_blocker'] or 'none'} (category={diag['blocker_category']})",
        f"Q: Missing reports? -> {', '.join(diag['missing_reports']) or 'none'}",
        f"Q: Decision trace available? -> {'yes' if 'decision_trace.json' in diag['available_reports'] else 'no'}",
        f"Q: Stale reports? -> {', '.join(diag['stale_reports']) or 'none'}",
        f"Q: Next safe command -> {diag['next_safe_command']}",
        f"   reason: {diag['next_safe_command_reason']}",
        "",
        "blockers:",
    ]
    if diag["blockers"]:
        for blocker in diag["blockers"]:
            lines.append(f"  - [{blocker['severity']}/{blocker['category']}] {blocker['code']}: {blocker['human_explanation']}")
            lines.append(f"      source={blocker['source_report']} raw={blocker['raw_blocker']}")
            lines.append(f"      safe_next_action={blocker['safe_next_action']}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("warnings:")
    if diag["warnings"]:
        lines.extend(f"  - {warning}" for warning in diag["warnings"])
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("safety flags:")
    for name, value in sorted(safety.items()):
        if name == "unsafe_flags_detected":
            lines.append(f"  unsafe_flags_detected={len(value)}")
            continue
        lines.append(f"  {name}={str(value).lower()}")
    lines.append("")
    return "\n".join(lines)


def render_last_block_txt(block: dict[str, Any]) -> str:
    lines = [
        SAFETY_BANNER,
        "EXPLAIN LAST BLOCK (read-only, paper/demo only)",
        f"generated_at={block['generated_at']}",
        f"reports_dir={block['reports_dir']}",
        f"overall_status={block['overall_status']}",
        "",
    ]
    last = block.get("last_block")
    if not last:
        lines.append("No hard blocker found in available reports.")
        lines.append(f"next_safe_command={block['next_safe_command']}")
        lines.append("")
        return "\n".join(lines)
    lines.extend([
        f"last_block_code={last['code']}",
        f"category={last['category']}",
        f"severity={last['severity']}",
        f"source_report={last['source_report']}",
        f"raw_blocker={last['raw_blocker']}",
        f"explanation={last['human_explanation']}",
        f"safe_next_action={last['safe_next_action']}",
        f"next_safe_command={block['next_safe_command']}",
        "",
        "all blockers:",
    ])
    for blocker in block.get("all_blockers", []):
        lines.append(f"  - [{blocker['severity']}/{blocker['category']}] {blocker['code']} ({blocker['source_report']})")
    lines.append("")
    return "\n".join(lines)


def render_last_decision_txt(decision: dict[str, Any]) -> str:
    lines = [
        SAFETY_BANNER,
        "EXPLAIN LAST DECISION (read-only, paper/demo only)",
        f"generated_at={decision['generated_at']}",
        f"reports_dir={decision['reports_dir']}",
        f"source={decision['source'] or 'none'}",
        f"available_sources={', '.join(decision['available_sources']) or 'none'}",
        "",
        decision["human_explanation"],
    ]
    detail = decision.get("decision")
    if isinstance(detail, dict):
        lines.append("")
        lines.append("decision detail:")
        for key, value in detail.items():
            lines.append(f"  {key}={value}")
    lines.append("")
    lines.append(f"next_safe_command={decision['next_safe_command']}")
    lines.append("")
    return "\n".join(lines)


def _explain_trace(trace: dict[str, Any], failed: list[Any]) -> str:
    if trace.get("accepted") is True:
        return (
            f"{trace.get('symbol')} was ACCEPTED in paper/demo mode "
            f"(final_score={trace.get('final_score')} >= active_min_score={trace.get('active_min_score')}); "
            f"paper order ids={trace.get('order_ids') or []}."
        )
    reason = trace.get("primary_rejection_reason") or "; ".join(_str_list(trace.get("rejection_reasons"))) or "no single reason recorded"
    failed_text = ", ".join(str(name) for name in failed) or "none"
    return (
        f"{trace.get('symbol')} was NOT accepted (status={trace.get('status')}). "
        f"final_score={trace.get('final_score')} vs active_min_score={trace.get('active_min_score')}. "
        f"primary_rejection_reason: {reason}. failed gates: {failed_text}."
    )


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def _status_is(payload: Any, key: str, expected: str) -> bool:
    return isinstance(payload, dict) and str(payload.get(key) or "") == expected


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _report_timestamp(payload: Any) -> datetime | None:
    record = payload[-1] if isinstance(payload, list) and payload else payload
    if not isinstance(record, dict):
        return None
    for key in _TIMESTAMP_KEYS:
        parsed = _parse_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dedupe_blockers(blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for blocker in blockers:
        key = (blocker["code"], blocker["category"], blocker["source_report"])
        if key not in seen:
            seen.add(key)
            result.append(blocker)
    return result
