"""Read-only paper/demo session history ledger.

Appends compact snapshots of completed paper/demo session reviews to a local
JSONL ledger and aggregates them into JSON/TXT history reports. The ledger is
report-based and offline only: it reads existing review/report artifacts,
writes history artifacts only under the reports directory, never imports the
MT5 terminal API, never calls ``order_send``, never mutates ``.env``, and
never runs trading logic, a daemon, or an infinite loop.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_JSONL = "paper_session_history.jsonl"
DEFAULT_HISTORY_JSON = "paper_session_history_summary.json"
DEFAULT_HISTORY_TXT = "paper_session_history_report.txt"
DEFAULT_SESSION_NAME = "paper-session-review"

REVIEW_SUMMARY_FILENAME = "paper_session_review_summary.json"
PERFORMANCE_SUMMARY_FILENAME = "paper_performance_summary.json"
DASHBOARD_SUMMARY_FILENAME = "operator_dashboard_summary.json"

STATUS_READY = "PAPER_SESSION_HISTORY_READY"
STATUS_EMPTY = "PAPER_SESSION_HISTORY_EMPTY"
STATUS_WARN = "PAPER_SESSION_HISTORY_WARN"
STATUS_INCOMPLETE = "PAPER_SESSION_HISTORY_INCOMPLETE"
STATUS_BLOCKED = "PAPER_SESSION_HISTORY_BLOCKED"

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
    "read_only_history": True,
    "paper_demo_only": True,
    "history_ledger_only": True,
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

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RECURRING_TOP_N = 10


@dataclass(frozen=True)
class PaperSessionHistoryConfig:
    """Configuration for one bounded history ledger run."""

    reports_dir: Path
    append_latest: bool = False
    session_name: str = DEFAULT_SESSION_NAME
    export_json: bool = False
    export_txt: bool = False
    strict: bool = False
    now: datetime | None = None

    def __post_init__(self) -> None:
        if not _SESSION_NAME_RE.match(self.session_name):
            raise ValueError("session name must contain only letters, digits, '.', '_' or '-' and not start with a separator")


class PaperSessionHistoryService:
    """Append review snapshots to the JSONL ledger and build aggregate summaries."""

    def __init__(self, config: PaperSessionHistoryConfig) -> None:
        self.config = config
        self.reports_dir = Path(config.reports_dir)
        self.now = config.now or datetime.now(timezone.utc)

    def run(self) -> dict[str, Any]:
        warnings: list[str] = []
        blocking: list[str] = []
        append_result: str | None = None

        if self.config.append_latest:
            entry, entry_warnings = build_history_entry(self.reports_dir, session_name=self.config.session_name, now=self.now)
            warnings.extend(entry_warnings)
            if entry is None:
                append_result = "REVIEW_MISSING"
            else:
                appended, reason = append_history_entry(self.reports_dir, entry)
                append_result = "APPENDED" if appended else "DUPLICATE_SKIPPED"
                if not appended:
                    warnings.append(reason)

        entries, load_warnings = load_history_entries(self.reports_dir)
        warnings.extend(load_warnings)

        summary = build_history_summary(
            entries,
            reports_dir=self.reports_dir,
            now=self.now,
            append_requested=self.config.append_latest,
            append_result=append_result,
            extra_warnings=warnings,
            extra_blocking=blocking,
        )

        if self.config.export_json:
            summary["output_paths"]["summary_json"] = str(self.reports_dir / DEFAULT_HISTORY_JSON)
        if self.config.export_txt:
            summary["output_paths"]["summary_txt"] = str(self.reports_dir / DEFAULT_HISTORY_TXT)
        if self.config.export_json:
            export_history_json(summary, self.reports_dir)
        if self.config.export_txt:
            export_history_txt(summary, self.reports_dir)
        return summary


def build_history_entry(reports_dir: Path, *, session_name: str = DEFAULT_SESSION_NAME, now: datetime | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Build one compact ledger entry from the latest local review artifacts."""
    now = now or datetime.now(timezone.utc)
    reports_dir = Path(reports_dir)
    warnings: list[str] = []
    source_paths: dict[str, str] = {}

    review = _read_json_dict(reports_dir / REVIEW_SUMMARY_FILENAME)
    if review is None:
        warnings.append(f"review summary not found or unreadable: {REVIEW_SUMMARY_FILENAME}; run scripts/paper_session_review.py first")
        return None, warnings
    source_paths["review"] = str(reports_dir / REVIEW_SUMMARY_FILENAME)

    performance = _read_json_dict(reports_dir / PERFORMANCE_SUMMARY_FILENAME)
    if performance is None:
        warnings.append(f"performance summary not found or unreadable: {PERFORMANCE_SUMMARY_FILENAME}; trade metrics recorded as null")
        performance = {}
    else:
        source_paths["performance"] = str(reports_dir / PERFORMANCE_SUMMARY_FILENAME)

    dashboard = _read_json_dict(reports_dir / DASHBOARD_SUMMARY_FILENAME)
    if dashboard is not None:
        source_paths["dashboard"] = str(reports_dir / DASHBOARD_SUMMARY_FILENAME)

    manifest_path = reports_dir / "bundles" / f"{session_name}_manifest.json"
    if _read_json_dict(manifest_path) is not None:
        source_paths["bundle_manifest"] = str(manifest_path)

    entry: dict[str, Any] = {
        "recorded_at": now.isoformat(),
        "session_name": session_name,
        "review_generated_at": _optional_str(review.get("generated_at")),
        "final_review_status": _optional_str(review.get("final_review_status")),
        "operator_status": _optional_str(review.get("operator_status")),
        "performance_status": _optional_str(review.get("performance_status")),
        "bundle_status": _optional_str(review.get("bundle_status")),
        "total_paper_trades": _to_int(performance.get("total_paper_trades")),
        "closed_count": _to_int(performance.get("closed_count")),
        "win_count": _to_int(performance.get("win_count")),
        "loss_count": _to_int(performance.get("loss_count")),
        "breakeven_count": _to_int(performance.get("breakeven_count")),
        "win_rate": _to_float(performance.get("win_rate")),
        "realized_r_total": _to_float(performance.get("realized_r_total")),
        "average_r": _to_float(performance.get("average_r")),
        "realized_pnl_total": _to_float(performance.get("realized_pnl_total")),
        "max_drawdown": _to_float(performance.get("max_drawdown")),
        "symbols_traded": _str_list(performance.get("symbols_traded")),
        "blocking_reasons": _str_list(review.get("blocking_reasons")),
        "warnings": _str_list(review.get("warnings")),
        "safety_flags": review.get("safety_flags") if isinstance(review.get("safety_flags"), dict) else {},
        "source_paths": source_paths,
    }
    return entry, warnings


def append_history_entry(reports_dir: Path, entry: dict[str, Any]) -> tuple[bool, str]:
    """Append one entry to the JSONL ledger.

    Duplicate policy (deterministic, documented): an entry whose
    ``session_name`` and ``review_generated_at`` both match an existing ledger
    record is skipped; the first recorded snapshot is kept unchanged.
    """
    reports_dir = Path(reports_dir)
    path = _history_output_path(reports_dir, DEFAULT_HISTORY_JSONL)
    existing, _ = load_history_entries(reports_dir)
    key = (entry.get("session_name"), entry.get("review_generated_at"))
    for record in existing:
        if (record.get("session_name"), record.get("review_generated_at")) == key:
            return False, f"duplicate history entry skipped for session_name={key[0]} review_generated_at={key[1]} (first snapshot kept)"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return True, "appended"


def load_history_entries(reports_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = _history_output_path(Path(reports_dir), DEFAULT_HISTORY_JSONL)
    if not path.is_file():
        return [], []
    warnings: list[str] = []
    entries: list[dict[str, Any]] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(record, dict):
            entries.append(record)
        else:
            skipped += 1
    if skipped:
        warnings.append(f"{skipped} unreadable history line(s) skipped in {DEFAULT_HISTORY_JSONL}")
    return entries, warnings


def build_history_summary(
    entries: list[dict[str, Any]],
    *,
    reports_dir: Path,
    now: datetime | None = None,
    append_requested: bool = False,
    append_result: str | None = None,
    extra_warnings: list[str] | None = None,
    extra_blocking: list[str] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    warnings = list(extra_warnings or [])
    blocking = list(extra_blocking or [])

    status_counts: Counter[str] = Counter(str(entry.get("final_review_status") or "UNKNOWN") for entry in entries)
    closed = [_to_int(entry.get("closed_count")) for entry in entries]
    wins = [_to_int(entry.get("win_count")) for entry in entries]
    losses = [_to_int(entry.get("loss_count")) for entry in entries]
    breakevens = [_to_int(entry.get("breakeven_count")) for entry in entries]
    win_rates = [_to_float(entry.get("win_rate")) for entry in entries]
    win_rates = [value for value in win_rates if value is not None]
    realized_r = [_to_float(entry.get("realized_r_total")) for entry in entries]
    realized_r = [value for value in realized_r if value is not None]
    realized_pnl = [_to_float(entry.get("realized_pnl_total")) for entry in entries]
    realized_pnl = [value for value in realized_pnl if value is not None]

    symbols: set[str] = set()
    warning_counter: Counter[str] = Counter()
    blocking_counter: Counter[str] = Counter()
    unsafe_detected: set[str] = set()
    for entry in entries:
        symbols.update(_str_list(entry.get("symbols_traded")))
        warning_counter.update(_str_list(entry.get("warnings")))
        blocking_counter.update(_str_list(entry.get("blocking_reasons")))
        flags = entry.get("safety_flags")
        if isinstance(flags, dict):
            unsafe_detected.update(key for key in UNSAFE_FLAG_KEYS if flags.get(key) is True)

    if unsafe_detected:
        blocking.append("unsafe safety flags detected in recorded sessions: " + ", ".join(sorted(unsafe_detected)))

    latest = entries[-1] if entries else None
    final_status = _final_status(
        entries=entries,
        latest=latest,
        blocking=blocking,
        append_requested=append_requested,
        append_result=append_result,
        warnings=warnings,
    )

    summary: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "reports_dir": str(Path(reports_dir)),
        "final_history_status": final_status,
        "append_result": append_result,
        "total_sessions": len(entries),
        "status_counts": dict(sorted(status_counts.items())),
        "latest_session": _session_ref(latest),
        "latest_ready_session": _latest_matching(entries, "READY"),
        "latest_warn_session": _latest_matching(entries, "WARN"),
        "latest_incomplete_session": _latest_matching(entries, "INCOMPLETE"),
        "latest_blocked_session": _latest_matching(entries, "BLOCKED"),
        "aggregate_closed_trades": sum(value for value in closed if value is not None),
        "aggregate_wins": sum(value for value in wins if value is not None),
        "aggregate_losses": sum(value for value in losses if value is not None),
        "aggregate_breakevens": sum(value for value in breakevens if value is not None),
        "average_win_rate": round(sum(win_rates) / len(win_rates), 8) if win_rates else None,
        "aggregate_realized_r": round(sum(realized_r), 8) if realized_r else None,
        "aggregate_realized_pnl": round(sum(realized_pnl), 8) if realized_pnl else None,
        "distinct_symbols_traded": sorted(symbols),
        "recurring_warnings": _recurring(warning_counter),
        "recurring_blocking_reasons": _recurring(blocking_counter),
        "safety_flags_summary": dict(SAFETY_FLAGS) | {"unsafe_source_flags_detected": sorted(unsafe_detected)},
        "blocking_reasons": _dedupe(blocking),
        "warnings": _dedupe(warnings),
        "output_paths": {"history_jsonl": str(Path(reports_dir) / DEFAULT_HISTORY_JSONL)},
    }
    return summary


def export_history_json(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = _history_output_path(Path(reports_dir), DEFAULT_HISTORY_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_history_txt(summary: dict[str, Any], reports_dir: Path) -> Path:
    path = _history_output_path(Path(reports_dir), DEFAULT_HISTORY_TXT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_history_txt(summary), encoding="utf-8")
    return path


def render_history_txt(summary: dict[str, Any]) -> str:
    lines = [
        "PAPER SESSION HISTORY (read-only, paper/demo only)",
        f"generated_at={summary['generated_at']}",
        f"reports_dir={summary['reports_dir']}",
        f"final_history_status={summary['final_history_status']}",
        f"append_result={summary['append_result'] or 'NOT_REQUESTED'}",
        f"total_sessions={summary['total_sessions']}",
        "",
        "status counts:",
    ]
    if summary["status_counts"]:
        lines.extend(f"  {status}={count}" for status, count in summary["status_counts"].items())
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("latest sessions:")
    for label, key in (
        ("latest", "latest_session"),
        ("latest ready", "latest_ready_session"),
        ("latest warn", "latest_warn_session"),
        ("latest incomplete", "latest_incomplete_session"),
        ("latest blocked", "latest_blocked_session"),
    ):
        ref = summary[key]
        lines.append(f"  {label}: " + (f"{ref['session_name']} @ {ref['recorded_at']} ({ref['final_review_status']})" if ref else "(none)"))
    lines.extend(
        [
            "",
            "aggregates:",
            f"  closed_trades={summary['aggregate_closed_trades']}",
            f"  wins={summary['aggregate_wins']} losses={summary['aggregate_losses']} breakevens={summary['aggregate_breakevens']}",
            f"  average_win_rate={summary['average_win_rate']}",
            f"  realized_r={summary['aggregate_realized_r']}",
            f"  realized_pnl={summary['aggregate_realized_pnl']}",
            f"  distinct_symbols={', '.join(summary['distinct_symbols_traded']) or '(none)'}",
        ]
    )
    for label, key in (("recurring warnings", "recurring_warnings"), ("recurring blocking reasons", "recurring_blocking_reasons")):
        lines.append("")
        lines.append(f"{label}:")
        values = summary[key]
        if values:
            lines.extend(f"  - ({item['count']}x) {item['message']}" for item in values)
        else:
            lines.append("  (none)")
    for label, key in (("blocking reasons", "blocking_reasons"), ("warnings", "warnings")):
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
    for key, value in sorted(summary["output_paths"].items()):
        lines.append(f"  {key}={value}")
    lines.append("")
    return "\n".join(lines)


def _final_status(
    *,
    entries: list[dict[str, Any]],
    latest: dict[str, Any] | None,
    blocking: list[str],
    append_requested: bool,
    append_result: str | None,
    warnings: list[str],
) -> str:
    if blocking:
        return STATUS_BLOCKED
    latest_status = str(latest.get("final_review_status") or "") if latest else ""
    if "BLOCKED" in latest_status:
        return STATUS_BLOCKED
    if not entries:
        return STATUS_EMPTY
    if append_requested and append_result == "REVIEW_MISSING":
        return STATUS_INCOMPLETE
    if "INCOMPLETE" in latest_status:
        return STATUS_INCOMPLETE
    if warnings or "WARN" in latest_status or (latest is not None and _str_list(latest.get("warnings"))):
        return STATUS_WARN
    return STATUS_READY


def _session_ref(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    return {
        "session_name": entry.get("session_name"),
        "recorded_at": entry.get("recorded_at"),
        "review_generated_at": entry.get("review_generated_at"),
        "final_review_status": entry.get("final_review_status"),
    }


def _latest_matching(entries: list[dict[str, Any]], token: str) -> dict[str, Any] | None:
    for entry in reversed(entries):
        if token in str(entry.get("final_review_status") or ""):
            return _session_ref(entry)
    return None


def _recurring(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"message": message, "count": count} for message, count in counter.most_common(_RECURRING_TOP_N) if count >= 2]


def _history_output_path(reports_dir: Path, filename: str) -> Path:
    """Return a history artifact path that cannot escape reports_dir.

    History filenames are constants, but this guard also rejects pre-existing
    symlinks that point outside the reports directory before any write occurs.
    """
    if Path(filename).name != filename:
        raise ValueError(f"history output filename must not contain path separators: {filename}")
    reports_root = reports_dir.resolve(strict=False)
    path = reports_dir / filename
    parent = path.parent.resolve(strict=False)
    if parent != reports_root:
        raise ValueError(f"history output parent escapes reports directory: {path}")
    if (path.exists() or path.is_symlink()) and not path.resolve(strict=False).is_relative_to(
        reports_root
    ):
        raise ValueError(f"history output path escapes reports directory: {path}")
    return path


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


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
