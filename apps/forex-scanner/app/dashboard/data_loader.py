"""Data loading helpers for the read-only monitoring dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DashboardData:
    signal_journal: list[dict[str, Any]]
    signal_report_summary: dict[str, Any]
    backtest_summary: dict[str, Any]
    threshold_optimizer_summary: dict[str, Any]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    except OSError:
        return []
    return rows


def load_dashboard_data(base_dir: Path | str = ".") -> DashboardData:
    root = Path(base_dir)
    reports = root / "reports"
    return DashboardData(
        signal_journal=_read_jsonl(reports / "signal_journal.jsonl"),
        signal_report_summary=_read_json(reports / "multi_asset_signal_report_summary.json"),
        backtest_summary=_read_json(reports / "backtest_multi_asset_summary.json"),
        threshold_optimizer_summary=_read_json(reports / "threshold_optimizer_summary.json"),
    )


def safe_get(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]
    return current
