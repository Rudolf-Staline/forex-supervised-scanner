"""Central index for generated reports under a reports directory.

Read-only utility: it inspects report files, summarizes their state, and can
export an index JSON and TXT file. It never runs strategies, never calls MT5,
and never places orders.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

EXPECTED_REPORTS = [
    "readiness_report.json",
    "local_validation_summary.json",
    "signal_quality_summary.json",
    "safety_env_doctor.json",
    "mt5_readonly_validation.json",
    "forward_test_summary.json",
    "forward_test_paper.csv",
    "paper_fill_summary.json",
    "paper_fill_report.csv",
    "threshold_optimizer_summary.json",
    "threshold_optimizer_report.csv",
    "multi_asset_signal_report_summary.json",
    "multi_asset_signal_report.csv",
    "signal_journal.jsonl",
    "backtest_multi_asset_summary.json",
    "backtest_multi_asset.csv",
]


@dataclass(frozen=True)
class ReportIndexOptions:
    reports_dir: Path
    show_missing: bool = False
    show_stale: bool = False
    max_age_hours: int = 48


def build_report_index(options: ReportIndexOptions) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    reports_found: list[str] = []
    reports_missing: list[str] = []
    stale_reports: list[str] = []
    report_sizes: dict[str, int] = {}
    json_validity: dict[str, str] = {}
    csv_row_counts: dict[str, int] = {}
    jsonl_line_counts: dict[str, int] = {}
    timestamps: list[datetime] = []

    stale_before = now - timedelta(hours=options.max_age_hours)

    for filename in EXPECTED_REPORTS:
        path = options.reports_dir / filename
        if not path.exists():
            reports_missing.append(filename)
            continue

        reports_found.append(filename)
        stat = path.stat()
        report_sizes[filename] = stat.st_size
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        timestamps.append(modified_at)

        if modified_at < stale_before:
            stale_reports.append(filename)

        suffix = path.suffix.lower()
        if suffix == ".json":
            json_validity[filename] = "valid" if _is_valid_json(path) else "invalid"
        elif suffix == ".csv":
            csv_row_counts[filename] = _csv_row_count(path)
        elif suffix == ".jsonl":
            jsonl_line_counts[filename] = _jsonl_line_count(path)

    latest_report_timestamp = max(timestamps).isoformat() if timestamps else None

    result = {
        "generated_at": now.isoformat(),
        "reports_found": sorted(reports_found),
        "reports_missing": sorted(reports_missing),
        "stale_reports": sorted(stale_reports),
        "latest_report_timestamp": latest_report_timestamp,
        "report_sizes": report_sizes,
        "json_validity": json_validity,
        "csv_row_counts": csv_row_counts,
        "jsonl_line_counts": jsonl_line_counts,
        "recommended_next_commands": _recommended_next_commands(options),
    }

    if not options.show_missing:
        result["reports_missing"] = []
    if not options.show_stale:
        result["stale_reports"] = []

    return result


def export_report_index_json(index_payload: dict[str, object], reports_dir: Path) -> Path:
    output_path = reports_dir / "report_index.json"
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(index_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def export_report_index_txt(index_payload: dict[str, object], reports_dir: Path) -> Path:
    output_path = reports_dir / "report_index.txt"
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_text(index_payload), encoding="utf-8")
    return output_path


def _is_valid_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def _csv_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        return sum(1 for _ in reader)


def _jsonl_line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _recommended_next_commands(options: ReportIndexOptions) -> list[str]:
    base = "python scripts/report_index.py --show-missing --show-stale --export-json --export-txt"
    follow_up = f"python scripts/report_index.py --reports-dir {options.reports_dir.as_posix()} --max-age-hours {options.max_age_hours}"
    return [base, follow_up]


def _render_text(index_payload: dict[str, object]) -> str:
    lines = [
        "# Report Index",
        f"generated_at: {index_payload['generated_at']}",
        f"latest_report_timestamp: {index_payload['latest_report_timestamp']}",
        "",
        f"reports_found ({len(index_payload['reports_found'])}):",
    ]
    lines.extend([f"- {name}" for name in index_payload["reports_found"]])

    lines.append("")
    lines.append(f"reports_missing ({len(index_payload['reports_missing'])}):")
    lines.extend([f"- {name}" for name in index_payload["reports_missing"]])

    lines.append("")
    lines.append(f"stale_reports ({len(index_payload['stale_reports'])}):")
    lines.extend([f"- {name}" for name in index_payload["stale_reports"]])

    lines.append("")
    lines.append("recommended_next_commands:")
    lines.extend([f"- {cmd}" for cmd in index_payload["recommended_next_commands"]])
    lines.append("")
    return "\n".join(lines)
