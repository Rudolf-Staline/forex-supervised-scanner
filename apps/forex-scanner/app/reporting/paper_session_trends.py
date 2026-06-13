"""Trend analysis for local paper/demo session history."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.reporting.paper_session_history import DEFAULT_HISTORY_JSONL, UNSAFE_FLAG_KEYS

DEFAULT_TRENDS_JSON = "paper_session_trends_summary.json"
DEFAULT_TRENDS_TXT = "paper_session_trends_report.txt"

STATUS_READY = "PAPER_SESSION_TRENDS_READY"
STATUS_EMPTY = "PAPER_SESSION_TRENDS_EMPTY"
STATUS_WARN = "PAPER_SESSION_TRENDS_WARN"
STATUS_BLOCKED = "PAPER_SESSION_TRENDS_BLOCKED"

SAFETY_FLAGS: dict[str, object] = {
    "read_only_trends": True,
    "paper_demo_only": True,
    "history_analysis_only": True,
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

_STATUS_SCORE = {"BLOCKED": 0, "EMPTY": 1, "INCOMPLETE": 1, "WARN": 2, "READY": 3}
_TOP_N = 10
_EPSILON = 1e-9


@dataclass(frozen=True)
class PaperSessionTrendsConfig:
    reports_dir: Path
    window: int = 10
    export_json: bool = False
    export_txt: bool = False
    strict: bool = False
    now: datetime | None = None

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be >= 1")


class PaperSessionTrendsService:
    def __init__(self, config: PaperSessionTrendsConfig) -> None:
        self.config = config
        self.reports_dir = Path(config.reports_dir)
        self.now = config.now or datetime.now(timezone.utc)

    def run(self) -> dict[str, Any]:
        entries, load_warnings = load_trend_history_entries(self.reports_dir)
        summary = build_trends_summary(
            entries,
            reports_dir=self.reports_dir,
            window=self.config.window,
            now=self.now,
            extra_warnings=load_warnings,
        )
        if self.config.export_json:
            summary["output_paths"]["trends_json"] = str(_trends_output_path(self.reports_dir, DEFAULT_TRENDS_JSON))
        if self.config.export_txt:
            summary["output_paths"]["trends_txt"] = str(_trends_output_path(self.reports_dir, DEFAULT_TRENDS_TXT))
        if self.config.export_json:
            export_trends_json(summary, self.reports_dir)
        if self.config.export_txt:
            export_trends_txt(summary, self.reports_dir)
        return summary


def load_trend_history_entries(reports_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = _history_input_path(Path(reports_dir))
    if not path.is_file():
        return [], [f"history ledger not found: {DEFAULT_HISTORY_JSONL}"]
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(payload, dict):
            entries.append(payload)
        else:
            skipped += 1
    if skipped:
        warnings.append(f"{skipped} unreadable history line(s) skipped in {DEFAULT_HISTORY_JSONL}")
    return entries, warnings


def build_trends_summary(
    entries: list[dict[str, Any]],
    *,
    reports_dir: Path,
    window: int = 10,
    now: datetime | None = None,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    if window < 1:
        raise ValueError("window must be >= 1")
    now = now or datetime.now(timezone.utc)
    all_entries = [entry for entry in entries if isinstance(entry, dict)]
    recent = all_entries[-window:]
    warnings = list(extra_warnings or [])
    status_sequence = [str(entry.get("final_review_status") or "UNKNOWN") for entry in recent]
    status_counts = Counter(status_sequence)
    status_scores = [_status_score(status) for status in status_sequence]
    latest = recent[-1] if recent else None
    previous = recent[:-1]
    latest_warnings = set(_str_list(latest.get("warnings"))) if latest else set()
    previous_warnings = _set_from_entries(previous, "warnings")
    latest_blocking = set(_str_list(latest.get("blocking_reasons"))) if latest else set()
    previous_blocking = _set_from_entries(previous, "blocking_reasons")
    warning_counter: Counter[str] = Counter()
    blocking_counter: Counter[str] = Counter()
    symbol_counter: Counter[str] = Counter()
    unsafe_flags: set[str] = set()
    for entry in recent:
        warning_counter.update(_str_list(entry.get("warnings")))
        blocking_counter.update(_str_list(entry.get("blocking_reasons")))
        symbol_counter.update(_str_list(entry.get("symbols_traded")))
        flags = entry.get("safety_flags")
        if isinstance(flags, dict):
            unsafe_flags.update(flag for flag in UNSAFE_FLAG_KEYS if flags.get(flag) is True)
    total_closed = _sum_ints(entry.get("closed_count") for entry in recent)
    total_wins = _sum_ints(entry.get("win_count") for entry in recent)
    total_losses = _sum_ints(entry.get("loss_count") for entry in recent)
    total_breakevens = _sum_ints(entry.get("breakeven_count") for entry in recent)
    win_rates = _float_series(entry.get("win_rate") for entry in recent)
    realized_r_values = _float_series(entry.get("realized_r_total") for entry in recent)
    realized_pnl_values = _float_series(entry.get("realized_pnl_total") for entry in recent)
    drawdowns = _float_series(entry.get("max_drawdown") for entry in recent)
    blocking_reasons = []
    if unsafe_flags:
        blocking_reasons.append("unsafe safety flags detected in history trend window: " + ", ".join(sorted(unsafe_flags)))
    if latest and "BLOCKED" in str(latest.get("final_review_status") or ""):
        blocking_reasons.append("latest session is blocked")
    final_status = _final_status(recent, warnings, blocking_reasons)
    return {
        "generated_at": now.isoformat(),
        "reports_dir": str(Path(reports_dir)),
        "final_trends_status": final_status,
        "history_path": str(Path(reports_dir) / DEFAULT_HISTORY_JSONL),
        "analysis_window_size": window,
        "total_available_sessions": len(all_entries),
        "total_sessions_analyzed": len(recent),
        "latest_session": _session_ref(latest),
        "latest_final_review_status": str(latest.get("final_review_status")) if latest else None,
        "status_sequence": status_sequence,
        "status_counts": dict(sorted(status_counts.items())),
        "status_trend": _trend_label(status_scores),
        "ready_ratio": _status_ratio(status_sequence, "READY"),
        "warn_ratio": _status_ratio(status_sequence, "WARN"),
        "incomplete_ratio": _status_ratio(status_sequence, "INCOMPLETE"),
        "blocked_ratio": _status_ratio(status_sequence, "BLOCKED"),
        "recurring_warnings": _recurring(warning_counter),
        "recurring_blocking_reasons": _recurring(blocking_counter),
        "new_warnings_latest": sorted(latest_warnings - previous_warnings),
        "new_blocking_reasons_latest": sorted(latest_blocking - previous_blocking),
        "resolved_warnings_latest": sorted(previous_warnings - latest_warnings) if latest else [],
        "resolved_blocking_reasons_latest": sorted(previous_blocking - latest_blocking) if latest else [],
        "aggregate_closed_trades": total_closed,
        "aggregate_wins": total_wins,
        "aggregate_losses": total_losses,
        "aggregate_breakevens": total_breakevens,
        "average_win_rate": _average(win_rates),
        "win_rate_trend": _numeric_trend(win_rates),
        "realized_r_trend": _numeric_trend(realized_r_values),
        "aggregate_realized_r": _round_or_none(sum(realized_r_values)) if realized_r_values else None,
        "aggregate_realized_pnl": _round_or_none(sum(realized_pnl_values)) if realized_pnl_values else None,
        "worst_max_drawdown": _round_or_none(min(drawdowns)) if drawdowns else None,
        "distinct_symbols_traded": sorted(symbol_counter),
        "symbol_concentration": _symbol_concentration(symbol_counter),
        "safety_flags_summary": dict(SAFETY_FLAGS) | {"unsafe_source_flags_detected": sorted(unsafe_flags)},
        "unsafe_flag_detections": sorted(unsafe_flags),
        "blocking_reasons": _dedupe(blocking_reasons),
        "warnings": _dedupe(warnings),
        "recommended_next_actions": _recommendations(final_status, recent, unsafe_flags, warnings, status_sequence),
        "output_paths": {},
    }


def export_trends_json(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = _trends_output_path(Path(reports_dir), DEFAULT_TRENDS_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_trends_txt(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = _trends_output_path(Path(reports_dir), DEFAULT_TRENDS_TXT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_trends_txt(summary), encoding="utf-8")
    return path


def render_trends_txt(summary: dict[str, Any]) -> str:
    lines = [
        "PAPER SESSION TRENDS (read-only, paper/demo only)",
        f"generated_at={summary['generated_at']}",
        f"reports_dir={summary['reports_dir']}",
        f"final_trends_status={summary['final_trends_status']}",
        f"analysis_window_size={summary['analysis_window_size']}",
        f"total_available_sessions={summary['total_available_sessions']}",
        f"total_sessions_analyzed={summary['total_sessions_analyzed']}",
        f"latest_final_review_status={summary['latest_final_review_status'] or 'UNAVAILABLE'}",
        f"status_trend={summary['status_trend']}",
        f"win_rate_trend={summary['win_rate_trend']}",
        f"realized_r_trend={summary['realized_r_trend']}",
        "",
        "status counts:",
    ]
    if summary["status_counts"]:
        lines.extend(f"  {status}={count}" for status, count in summary["status_counts"].items())
    else:
        lines.append("  (none)")
    lines.extend([
        "",
        "aggregates:",
        f"  closed_trades={summary['aggregate_closed_trades']}",
        f"  wins={summary['aggregate_wins']} losses={summary['aggregate_losses']} breakevens={summary['aggregate_breakevens']}",
        f"  average_win_rate={summary['average_win_rate']}",
        f"  aggregate_realized_r={summary['aggregate_realized_r']}",
        f"  aggregate_realized_pnl={summary['aggregate_realized_pnl']}",
        f"  worst_max_drawdown={summary['worst_max_drawdown']}",
        f"  distinct_symbols={', '.join(summary['distinct_symbols_traded']) or '(none)'}",
    ])
    for label, key in (("symbol concentration", "symbol_concentration"), ("recurring warnings", "recurring_warnings"), ("recurring blocking reasons", "recurring_blocking_reasons")):
        lines.append("")
        lines.append(f"{label}:")
        values = summary[key]
        if values:
            for item in values:
                if "symbol" in item:
                    lines.append(f"  - {item['symbol']}: count={item['count']} ratio={item['ratio']}")
                else:
                    lines.append(f"  - ({item['count']}x) {item['message']}")
        else:
            lines.append("  (none)")
    for label, key in (("new warnings latest", "new_warnings_latest"), ("new blocking reasons latest", "new_blocking_reasons_latest"), ("resolved warnings latest", "resolved_warnings_latest"), ("resolved blocking reasons latest", "resolved_blocking_reasons_latest"), ("blocking reasons", "blocking_reasons"), ("warnings", "warnings"), ("recommended next actions", "recommended_next_actions")):
        lines.append("")
        lines.append(f"{label}:")
        values = summary[key]
        if values:
            lines.extend(f"  - {value}" for value in values)
        else:
            lines.append("  (none)")
    lines.append("")
    lines.append("safety flags summary:")
    for key, value in sorted(summary["safety_flags_summary"].items()):
        lines.append(f"  {key}={json.dumps(value, sort_keys=True)}")
    lines.append("")
    lines.append("output paths:")
    if summary["output_paths"]:
        for key, value in sorted(summary["output_paths"].items()):
            lines.append(f"  {key}={value}")
    else:
        lines.append("  (none)")
    lines.append("")
    return "\n".join(lines)


def _history_input_path(reports_dir: Path) -> Path:
    reports_root = reports_dir.resolve(strict=False)
    path = reports_dir / DEFAULT_HISTORY_JSONL
    if (path.exists() or path.is_symlink()) and not path.resolve(strict=False).is_relative_to(reports_root):
        raise ValueError(f"history input path escapes reports directory: {path}")
    return path


def _trends_output_path(reports_dir: Path, filename: str) -> Path:
    if Path(filename).name != filename:
        raise ValueError(f"trend output filename must not contain path separators: {filename}")
    reports_root = reports_dir.resolve(strict=False)
    path = reports_dir / filename
    if path.parent.resolve(strict=False) != reports_root:
        raise ValueError(f"trend output parent escapes reports directory: {path}")
    if (path.exists() or path.is_symlink()) and not path.resolve(strict=False).is_relative_to(reports_root):
        raise ValueError(f"trend output path escapes reports directory: {path}")
    return path


def _final_status(entries: list[dict[str, Any]], warnings: list[str], blocking: list[str]) -> str:
    if blocking:
        return STATUS_BLOCKED
    if not entries:
        return STATUS_EMPTY
    latest_status = str(entries[-1].get("final_review_status") or "")
    if "BLOCKED" in latest_status:
        return STATUS_BLOCKED
    if warnings or "WARN" in latest_status or "INCOMPLETE" in latest_status:
        return STATUS_WARN
    return STATUS_READY


def _status_score(status: str) -> int | None:
    for token, score in _STATUS_SCORE.items():
        if token in status:
            return score
    return None


def _trend_label(values: list[int | None]) -> str:
    series = [value for value in values if value is not None]
    if len(series) < 2:
        return "insufficient_data"
    deltas = [current - previous for previous, current in zip(series, series[1:])]
    if all(delta == 0 for delta in deltas):
        return "stable"
    if all(delta >= 0 for delta in deltas) and any(delta > 0 for delta in deltas):
        return "improving"
    if all(delta <= 0 for delta in deltas) and any(delta < 0 for delta in deltas):
        return "degrading"
    return "mixed"


def _numeric_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient_data"
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    if all(abs(delta) <= _EPSILON for delta in deltas):
        return "stable"
    if all(delta >= -_EPSILON for delta in deltas) and any(delta > _EPSILON for delta in deltas):
        return "improving"
    if all(delta <= _EPSILON for delta in deltas) and any(delta < -_EPSILON for delta in deltas):
        return "degrading"
    return "mixed"


def _status_ratio(statuses: list[str], token: str) -> float | None:
    if not statuses:
        return None
    return round(sum(1 for status in statuses if token in status) / len(statuses), 8)


def _set_from_entries(entries: list[dict[str, Any]], key: str) -> set[str]:
    values: set[str] = set()
    for entry in entries:
        values.update(_str_list(entry.get(key)))
    return values


def _sum_ints(values: Any) -> int:
    total = 0
    for value in values:
        converted = _to_int(value)
        if converted is not None:
            total += converted
    return total


def _float_series(values: Any) -> list[float]:
    result: list[float] = []
    for value in values:
        converted = _to_float(value)
        if converted is not None:
            result.append(converted)
    return result


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return _round_or_none(sum(values) / len(values))


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 8)


def _recurring(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"message": message, "count": count} for message, count in counter.most_common(_TOP_N) if count >= 2]


def _symbol_concentration(counter: Counter[str]) -> list[dict[str, Any]]:
    total = sum(counter.values())
    if total <= 0:
        return []
    return [{"symbol": symbol, "count": count, "ratio": round(count / total, 8)} for symbol, count in counter.most_common(_TOP_N)]


def _recommendations(final_status: str, entries: list[dict[str, Any]], unsafe_flags: set[str], warnings: list[str], statuses: list[str]) -> list[str]:
    if final_status == STATUS_EMPTY:
        return ["run paper_session_history.py --append-latest after generating a paper session review"]
    if unsafe_flags:
        return ["resolve unsafe safety flags before treating recent paper/demo sessions as review-ready"]
    if statuses and "BLOCKED" in statuses[-1]:
        return ["resolve the latest blocked session before continuing paper/demo operation"]
    if warnings:
        return ["review trend warnings and confirm the history ledger is complete"]
    if entries and len(entries) < 3:
        return ["record more paper/demo sessions before relying on trend direction"]
    return ["review trend report and continue paper/demo validation only"]


def _session_ref(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    return {
        "session_name": entry.get("session_name"),
        "recorded_at": entry.get("recorded_at"),
        "review_generated_at": entry.get("review_generated_at"),
        "final_review_status": entry.get("final_review_status"),
    }


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
