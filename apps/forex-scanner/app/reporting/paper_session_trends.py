"""Offline paper/demo session trend analyzer.

Reads the append-only Paper Session History Ledger and produces multi-session
insights. This module is report-based and paper/demo only: it reads existing
``paper_session_history.jsonl`` records, writes trend artifacts only under the
reports directory, never imports MT5, never calls ``order_send``, never mutates
``.env``, and never starts daemons or infinite loops.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_JSONL = "paper_session_history.jsonl"
DEFAULT_TRENDS_JSON = "paper_session_trends_summary.json"
DEFAULT_TRENDS_TXT = "paper_session_trends_report.txt"
DEFAULT_WINDOW = 10

STATUS_READY = "PAPER_SESSION_TRENDS_READY"
STATUS_EMPTY = "PAPER_SESSION_TRENDS_EMPTY"
STATUS_WARN = "PAPER_SESSION_TRENDS_WARN"
STATUS_BLOCKED = "PAPER_SESSION_TRENDS_BLOCKED"

UNSAFE_FLAG_KEYS = {
    "live_trading_enabled",
    "live_execution_allowed",
    "broker_live_execution_allowed",
    "broker_order_submission_allowed",
    "mt5_order_execution_allowed",
    "order_send_called",
    "env_mutation_performed",
}

SAFETY_FLAGS: dict[str, object] = {
    "read_history_artifacts_only": True,
    "write_trend_reports_only": True,
    "paper_demo_only": True,
    "live_trading_enabled": False,
    "live_execution_allowed": False,
    "broker_live_execution_allowed": False,
    "broker_order_submission_allowed": False,
    "order_send_called": False,
    "env_mutation_performed": False,
    "mt5_required": False,
    "daemon_started": False,
    "infinite_loop_started": False,
}

_RECURRING_TOP_N = 10
_SYMBOL_TOP_N = 10
_STABLE_EPSILON = 1e-9


@dataclass(frozen=True)
class PaperSessionTrendsConfig:
    """Configuration for one bounded offline trend analysis run."""

    reports_dir: Path
    window: int = DEFAULT_WINDOW
    export_json: bool = False
    export_txt: bool = False
    strict: bool = False
    now: datetime | None = None

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be at least 1")


class PaperSessionTrendsService:
    """Load paper session history and build trend reports."""

    def __init__(self, config: PaperSessionTrendsConfig) -> None:
        self.config = config
        self.reports_dir = Path(config.reports_dir)
        self.now = config.now or datetime.now(timezone.utc)

    def run(self) -> dict[str, Any]:
        entries, load_warnings, history_state = load_history_entries(self.reports_dir)
        summary = build_trends_summary(
            entries,
            reports_dir=self.reports_dir,
            window=self.config.window,
            now=self.now,
            extra_warnings=load_warnings,
            history_state=history_state,
        )
        if self.config.export_json:
            summary["output_paths"]["summary_json"] = str(self.reports_dir / DEFAULT_TRENDS_JSON)
        if self.config.export_txt:
            summary["output_paths"]["summary_txt"] = str(self.reports_dir / DEFAULT_TRENDS_TXT)
        if self.config.export_json:
            export_trends_json(summary, self.reports_dir)
        if self.config.export_txt:
            export_trends_txt(summary, self.reports_dir)
        return summary


def load_history_entries(reports_dir: Path) -> tuple[list[dict[str, Any]], list[str], str]:
    """Load JSONL ledger records without mutating the source history file."""
    path = _trend_path(Path(reports_dir), DEFAULT_HISTORY_JSONL)
    if not path.is_file():
        return [], [], "missing"

    warnings: list[str] = []
    entries: list[dict[str, Any]] = []
    skipped = 0
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return [], [], "empty"

    for line_number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            warnings.append(f"unreadable history line {line_number} skipped in {DEFAULT_HISTORY_JSONL}")
            continue
        if isinstance(record, dict):
            entries.append(record)
        else:
            skipped += 1
            warnings.append(f"non-object history line {line_number} skipped in {DEFAULT_HISTORY_JSONL}")
    if skipped and not warnings:
        warnings.append(f"{skipped} unreadable history line(s) skipped in {DEFAULT_HISTORY_JSONL}")
    return entries, _dedupe(warnings), "loaded"


def build_trends_summary(
    entries: list[dict[str, Any]],
    *,
    reports_dir: Path,
    window: int = DEFAULT_WINDOW,
    now: datetime | None = None,
    extra_warnings: list[str] | None = None,
    history_state: str = "loaded",
) -> dict[str, Any]:
    """Build aggregate trend insights from existing paper session history."""
    if window < 1:
        raise ValueError("window must be at least 1")
    now = now or datetime.now(timezone.utc)
    warnings = list(extra_warnings or [])
    sorted_entries = sorted(entries, key=_entry_sort_key)
    window_entries = sorted_entries[-window:]
    latest = window_entries[-1] if window_entries else None
    previous_entries = window_entries[:-1]

    status_counts: Counter[str] = Counter(_status(entry) for entry in window_entries)
    status_ratios = _status_ratios(status_counts, len(window_entries))
    status_trend = _status_trend(window_entries)
    status_direction = status_trend["direction"]

    latest_warnings = set(_str_list(latest.get("warnings"))) if latest else set()
    latest_blocking = set(_str_list(latest.get("blocking_reasons"))) if latest else set()
    previous_warnings = set().union(*[set(_str_list(entry.get("warnings"))) for entry in previous_entries]) if previous_entries else set()
    previous_blocking = set().union(*[set(_str_list(entry.get("blocking_reasons"))) for entry in previous_entries]) if previous_entries else set()

    warning_counter: Counter[str] = Counter()
    blocking_counter: Counter[str] = Counter()
    symbol_counter: Counter[str] = Counter()
    unsafe_detected: set[str] = set()
    for entry in window_entries:
        warning_counter.update(_str_list(entry.get("warnings")))
        blocking_counter.update(_str_list(entry.get("blocking_reasons")))
        symbol_counter.update(_str_list(entry.get("symbols_traded")))
        flags = entry.get("safety_flags")
        if isinstance(flags, dict):
            unsafe_detected.update(key for key in UNSAFE_FLAG_KEYS if flags.get(key) is True)

    closed = _int_values(window_entries, "closed_count")
    wins = _int_values(window_entries, "win_count")
    losses = _int_values(window_entries, "loss_count")
    breakevens = _int_values(window_entries, "breakeven_count")
    win_rates = _float_values(window_entries, "win_rate")
    realized_r = _float_values(window_entries, "realized_r_total")
    realized_pnl = _float_values(window_entries, "realized_pnl_total")
    drawdowns = _float_values(window_entries, "max_drawdown")

    blocking_reasons: list[str] = []
    if unsafe_detected:
        blocking_reasons.append("unsafe safety flags detected in recorded sessions: " + ", ".join(sorted(unsafe_detected)))

    final_status = _final_status(window_entries=window_entries, warnings=warnings, blocking=blocking_reasons)
    recommendations = _recommended_actions(
        final_status=final_status,
        history_state=history_state,
        warnings=warnings,
        unsafe_detected=unsafe_detected,
        recurring_warnings=_recurring(warning_counter),
        recurring_blocking=_recurring(blocking_counter),
        win_rate_trend=_numeric_trend(win_rates),
        realized_r_trend=_numeric_trend(realized_r),
        status_direction=status_direction,
    )

    summary: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "reports_dir": str(Path(reports_dir)),
        "final_trends_status": final_status,
        "history_state": history_state,
        "total_sessions_available": len(sorted_entries),
        "total_sessions_analyzed": len(window_entries),
        "analysis_window_size": window,
        "latest_session": _session_ref(latest),
        "latest_final_review_status": _status(latest) if latest else None,
        "status_counts": dict(sorted(status_counts.items())),
        "status_ratios": status_ratios,
        "status_trend": status_trend,
        "status_direction": status_direction,
        "ready_ratio": status_ratios.get("ready", 0.0),
        "warn_ratio": status_ratios.get("warn", 0.0),
        "incomplete_ratio": status_ratios.get("incomplete", 0.0),
        "blocked_ratio": status_ratios.get("blocked", 0.0),
        "recurring_warnings": _recurring(warning_counter),
        "recurring_blocking_reasons": _recurring(blocking_counter),
        "new_warnings_latest": sorted(latest_warnings - previous_warnings),
        "new_blocking_reasons_latest": sorted(latest_blocking - previous_blocking),
        "resolved_warnings": sorted(previous_warnings - latest_warnings),
        "resolved_blocking_reasons": sorted(previous_blocking - latest_blocking),
        "total_closed_trades": sum(closed),
        "win_total": sum(wins),
        "loss_total": sum(losses),
        "breakeven_total": sum(breakevens),
        "average_win_rate": round(sum(win_rates) / len(win_rates), 8) if win_rates else None,
        "win_rate_trend": _numeric_trend(win_rates),
        "realized_r_trend": _numeric_trend(realized_r),
        "aggregate_realized_r": round(sum(realized_r), 8) if realized_r else None,
        "aggregate_realized_pnl": round(sum(realized_pnl), 8) if realized_pnl else None,
        "max_drawdown_worst": min(drawdowns) if drawdowns else None,
        "distinct_symbols_traded": sorted(symbol_counter),
        "symbol_concentration": _symbol_concentration(symbol_counter),
        "safety_flags_summary": dict(SAFETY_FLAGS) | {"unsafe_source_flags_detected": sorted(unsafe_detected)},
        "unsafe_flag_detections": sorted(unsafe_detected),
        "recommended_next_actions": recommendations,
        "blocking_reasons": _dedupe(blocking_reasons),
        "warnings": _dedupe(warnings),
        "output_paths": {"history_jsonl": str(Path(reports_dir) / DEFAULT_HISTORY_JSONL)},
    }
    return summary


def export_trends_json(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = _trend_path(Path(reports_dir), DEFAULT_TRENDS_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_trends_txt(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = _trend_path(Path(reports_dir), DEFAULT_TRENDS_TXT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_trends_txt(summary), encoding="utf-8")
    return path


def render_trends_txt(summary: dict[str, Any]) -> str:
    lines = [
        "PAPER SESSION TRENDS (offline, paper/demo history only)",
        f"generated_at={summary['generated_at']}",
        f"reports_dir={summary['reports_dir']}",
        f"final_trends_status={summary['final_trends_status']}",
        f"history_state={summary['history_state']}",
        f"analysis_window_size={summary['analysis_window_size']}",
        f"total_sessions_available={summary['total_sessions_available']}",
        f"total_sessions_analyzed={summary['total_sessions_analyzed']}",
        f"latest_final_review_status={summary['latest_final_review_status'] or '(none)'}",
        "",
        "status counts and trend:",
    ]
    if summary["status_counts"]:
        lines.extend(f"  {status}={count}" for status, count in summary["status_counts"].items())
    else:
        lines.append("  (none)")
    lines.append(f"  direction={summary['status_direction']}")
    lines.append(f"  sequence={', '.join(summary['status_trend']['sequence']) or '(none)'}")
    lines.extend([
        "",
        "status ratios:",
        f"  ready={summary['ready_ratio']}",
        f"  warn={summary['warn_ratio']}",
        f"  incomplete={summary['incomplete_ratio']}",
        f"  blocked={summary['blocked_ratio']}",
        "",
        "trade aggregates:",
        f"  closed_trades={summary['total_closed_trades']}",
        f"  wins={summary['win_total']} losses={summary['loss_total']} breakevens={summary['breakeven_total']}",
        f"  average_win_rate={summary['average_win_rate']}",
        f"  win_rate_trend={summary['win_rate_trend']}",
        f"  aggregate_realized_r={summary['aggregate_realized_r']}",
        f"  realized_r_trend={summary['realized_r_trend']}",
        f"  aggregate_realized_pnl={summary['aggregate_realized_pnl']}",
        f"  max_drawdown_worst={summary['max_drawdown_worst']}",
        "",
        f"distinct_symbols={', '.join(summary['distinct_symbols_traded']) or '(none)'}",
        "symbol concentration:",
    ])
    if summary["symbol_concentration"]:
        lines.extend(f"  - {item['symbol']}: {item['count']} ({item['ratio']})" for item in summary["symbol_concentration"])
    else:
        lines.append("  (none)")

    for label, key in (
        ("recurring warnings", "recurring_warnings"),
        ("recurring blocking reasons", "recurring_blocking_reasons"),
        ("new warnings in latest session", "new_warnings_latest"),
        ("new blocking reasons in latest session", "new_blocking_reasons_latest"),
        ("resolved warnings", "resolved_warnings"),
        ("resolved blocking reasons", "resolved_blocking_reasons"),
        ("warnings", "warnings"),
        ("blocking reasons", "blocking_reasons"),
        ("recommended next actions", "recommended_next_actions"),
    ):
        lines.append("")
        lines.append(f"{label}:")
        values = summary[key]
        if not values:
            lines.append("  (none)")
        elif values and isinstance(values[0], dict):
            lines.extend(f"  - ({item['count']}x) {item['message']}" for item in values)
        else:
            lines.extend(f"  - {value}" for value in values)

    lines.append("")
    lines.append("safety flags summary:")
    for key, value in sorted(summary["safety_flags_summary"].items()):
        lines.append(f"  {key}={json.dumps(value, sort_keys=True)}")
    lines.append("")
    lines.append("output paths:")
    for key, value in sorted(summary["output_paths"].items()):
        lines.append(f"  {key}={value}")
    lines.append("")
    return "\n".join(lines)


def _final_status(*, window_entries: list[dict[str, Any]], warnings: list[str], blocking: list[str]) -> str:
    if blocking:
        return STATUS_BLOCKED
    latest_status = _status(window_entries[-1]) if window_entries else ""
    if "BLOCKED" in latest_status:
        return STATUS_BLOCKED
    if warnings:
        return STATUS_WARN
    if not window_entries:
        return STATUS_EMPTY
    if "WARN" in latest_status or _str_list(window_entries[-1].get("warnings")):
        return STATUS_WARN
    return STATUS_READY


def _status(entry: dict[str, Any] | None) -> str:
    if not entry:
        return "UNKNOWN"
    return str(entry.get("final_review_status") or "UNKNOWN")


def _status_bucket(status: str) -> str:
    upper = status.upper()
    if "READY" in upper:
        return "ready"
    if "WARN" in upper:
        return "warn"
    if "INCOMPLETE" in upper:
        return "incomplete"
    if "BLOCKED" in upper:
        return "blocked"
    return "unknown"


def _status_score(status: str) -> int:
    bucket = _status_bucket(status)
    return {"blocked": 0, "incomplete": 0, "warn": 1, "ready": 2}.get(bucket, 1)


def _status_ratios(status_counts: Counter[str], total: int) -> dict[str, float]:
    buckets = Counter[str]()
    for status, count in status_counts.items():
        buckets[_status_bucket(status)] += count
    return {bucket: round(buckets.get(bucket, 0) / total, 8) if total else 0.0 for bucket in ("ready", "warn", "incomplete", "blocked", "unknown")}


def _status_trend(entries: list[dict[str, Any]]) -> dict[str, Any]:
    sequence = [_status(entry) for entry in entries]
    scores = [_status_score(status) for status in sequence]
    return {"sequence": sequence, "scores": scores, "direction": _direction(scores)}


def _numeric_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient_data"
    return _direction(values, insufficient_label="insufficient_data")


def _direction(values: list[float | int], *, insufficient_label: str = "stable") -> str:
    if len(values) < 2:
        return insufficient_label
    deltas = [float(values[index]) - float(values[index - 1]) for index in range(1, len(values))]
    positives = [delta for delta in deltas if delta > _STABLE_EPSILON]
    negatives = [delta for delta in deltas if delta < -_STABLE_EPSILON]
    if not positives and not negatives:
        return "stable"
    if positives and not negatives:
        return "improving"
    if negatives and not positives:
        return "degrading"
    return "mixed"


def _recurring(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"message": message, "count": count} for message, count in counter.most_common(_RECURRING_TOP_N) if count >= 2]


def _symbol_concentration(counter: Counter[str]) -> list[dict[str, Any]]:
    total = sum(counter.values())
    if not total:
        return []
    return [
        {"symbol": symbol, "count": count, "ratio": round(count / total, 8)}
        for symbol, count in counter.most_common(_SYMBOL_TOP_N)
    ]


def _recommended_actions(
    *,
    final_status: str,
    history_state: str,
    warnings: list[str],
    unsafe_detected: set[str],
    recurring_warnings: list[dict[str, Any]],
    recurring_blocking: list[dict[str, Any]],
    win_rate_trend: str,
    realized_r_trend: str,
    status_direction: str,
) -> list[str]:
    actions: list[str] = []
    if history_state in {"missing", "empty"}:
        actions.append("Run paper session review/history after paper demo sessions, then re-run trends.")
    if unsafe_detected:
        actions.append("Investigate unsafe flags before relying on paper trend reports.")
    if recurring_blocking:
        actions.append("Resolve recurring blocking reasons before extending paper/demo evaluation.")
    if recurring_warnings:
        actions.append("Review recurring warnings and address repeated report-quality issues.")
    if warnings:
        actions.append("Inspect skipped/corrupt history lines and repair the ledger from trusted artifacts if needed.")
    if status_direction == "degrading":
        actions.append("Compare recent sessions with earlier ready sessions to identify operational regressions.")
    if win_rate_trend == "degrading" or realized_r_trend == "degrading":
        actions.append("Review paper trade outcomes before changing any strategy or safety defaults.")
    if final_status == STATUS_READY and not actions:
        actions.append("Continue collecting paper/demo history and monitor trend stability.")
    return _dedupe(actions)


def _session_ref(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    return {
        "session_name": entry.get("session_name"),
        "recorded_at": entry.get("recorded_at"),
        "review_generated_at": entry.get("review_generated_at"),
        "final_review_status": entry.get("final_review_status"),
    }


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    return (str(entry.get("recorded_at") or ""), str(entry.get("review_generated_at") or ""))


def _int_values(entries: list[dict[str, Any]], key: str) -> list[int]:
    return [value for value in (_to_int(entry.get(key)) for entry in entries) if value is not None]


def _float_values(entries: list[dict[str, Any]], key: str) -> list[float]:
    return [value for value in (_to_float(entry.get(key)) for entry in entries) if value is not None]


def _trend_path(reports_dir: Path, filename: str) -> Path:
    if Path(filename).name != filename:
        raise ValueError(f"trend artifact filename must not contain path separators: {filename}")
    reports_root = reports_dir.resolve(strict=False)
    path = reports_dir / filename
    parent = path.parent.resolve(strict=False)
    if parent != reports_root:
        raise ValueError(f"trend artifact parent escapes reports directory: {path}")
    if (path.exists() or path.is_symlink()) and not path.resolve(strict=False).is_relative_to(reports_root):
        raise ValueError(f"trend artifact path escapes reports directory: {path}")
    return path


def _to_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _str_list(value: object) -> list[str]:
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
