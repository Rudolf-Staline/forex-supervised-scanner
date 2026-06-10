"""Autonomous Evidence Builder for readiness gate inputs.

The builder orchestrates existing read-only report builders. It is deliberately
bounded, foreground-only, and paper/demo-safe: it does not call MT5, does not
submit orders, does not mutate `.env`, and does not require network access.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config.settings import AppSettings, PROJECT_ROOT
from app.execution.autonomous_readiness import (
    AutonomousReadinessConfig,
    AutonomousReadinessReport as ReadinessReport,
    build_readiness_report,
    export_autonomous_readiness_json,
    export_autonomous_readiness_txt,
)
from app.ops.failure_diagnostics import (
    FailureDiagnosticsOptions,
    build_failure_diagnostics_summary,
    export_failure_diagnostics_json,
    export_failure_diagnostics_txt,
)
from app.reporting.data_health import DataHealthOptions, build_data_health_report, render_text_report
from app.reporting.mt5_symbol_mapping_audit import MappingAuditOptions, export_audit_json, run_mapping_audit
from app.reporting.session_health import (
    build_session_health_summary,
    collect_session_health_records,
    export_session_health_json,
)
from app.reporting.signal_anomaly_detector import build_summary, collect_records, detect_anomalies
from app.storage.database import Database

DEFAULT_AUTONOMOUS_EVIDENCE_REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_AUTONOMOUS_EVIDENCE_JSON_REPORT = "autonomous_evidence_summary.json"
DEFAULT_AUTONOMOUS_EVIDENCE_TXT_REPORT = "autonomous_evidence_report.txt"


class AutonomousEvidenceMode(StrEnum):
    DRY_RUN = "dry_run"
    READ_ONLY = "read_only"
    REFRESH = "refresh"


class AutonomousEvidenceTaskStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class AutonomousEvidenceFinalStatus(StrEnum):
    READY_EVIDENCE = "READY_EVIDENCE"
    WARN_EVIDENCE = "WARN_EVIDENCE"
    BLOCKED_EVIDENCE = "BLOCKED_EVIDENCE"
    DRY_RUN_PLAN = "DRY_RUN_PLAN"


TaskRunner = Callable[["AutonomousEvidenceConfig"], tuple[AutonomousEvidenceTaskStatus, str, list[Path], dict[str, object]]]


class AutonomousEvidenceConfig(BaseModel):
    """Configuration for one bounded evidence-builder invocation."""

    reports_dir: Path = DEFAULT_AUTONOMOUS_EVIDENCE_REPORTS_DIR
    mode: AutonomousEvidenceMode = AutonomousEvidenceMode.READ_ONLY
    watchlist: str = "multi_asset_demo"
    symbols: list[str] = Field(default_factory=list)
    asset_class: str = "all"
    include_readiness: bool = False
    export_json: bool = False
    export_txt: bool = False
    fail_fast: bool = False
    allow_subprocess: bool = False
    dry_run_summary: bool = False

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().replace("-", "_")
        return value

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.replace(",", " ").split()
        else:
            raw = list(value)  # type: ignore[arg-type]
        return [str(symbol).strip().upper() for symbol in raw if str(symbol).strip()]


class AutonomousEvidenceTask(BaseModel):
    """A single bounded read-only evidence step."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    blocking: bool = False
    optional: bool = False
    safety_flags: dict[str, object] = Field(default_factory=dict)
    runner: TaskRunner | None = Field(default=None, exclude=True)
    subprocess_command: list[str] | None = None


class AutonomousEvidenceTaskResult(BaseModel):
    task_name: str
    status: AutonomousEvidenceTaskStatus
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    output_paths: list[str] = Field(default_factory=list)
    blocking: bool
    reason: str
    exception_class: str | None = None
    exception_message: str | None = None
    safety_flags: dict[str, object] = Field(default_factory=dict)


class AutonomousEvidenceReport(BaseModel):
    generated_at: datetime
    mode: AutonomousEvidenceMode
    final_status: AutonomousEvidenceFinalStatus
    tasks_total: int
    tasks_passed: int
    tasks_warned: int
    tasks_failed: int
    tasks_skipped: int
    blocking_failures: list[str] = Field(default_factory=list)
    task_results: list[AutonomousEvidenceTaskResult] = Field(default_factory=list)
    output_paths: list[str] = Field(default_factory=list)
    readiness_report: dict[str, Any] | None = None
    safety_flags: dict[str, object] = Field(default_factory=dict)


class AutonomousEvidenceBuilderService:
    """Build local readiness evidence without broker, MT5, network, or daemon side effects."""

    def __init__(self, settings: AppSettings | None = None, database: Database | None = None) -> None:
        self.settings = settings
        self.database = database

    def build(self, config: AutonomousEvidenceConfig | None = None, tasks: list[AutonomousEvidenceTask] | None = None) -> AutonomousEvidenceReport:
        selected = config or AutonomousEvidenceConfig()
        plan = tasks or build_default_task_plan(selected)
        results: list[AutonomousEvidenceTaskResult] = []
        output_paths: list[str] = []
        readiness: ReadinessReport | None = None

        for task in plan:
            result = self._run_task(task, selected)
            results.append(result)
            output_paths.extend(result.output_paths)
            if selected.fail_fast and result.blocking and result.status == AutonomousEvidenceTaskStatus.FAIL:
                break

        if selected.include_readiness and selected.mode != AutonomousEvidenceMode.DRY_RUN:
            readiness_result = self._run_readiness(selected)
            readiness = readiness_result[0]
            output_paths.extend(str(path) for path in readiness_result[1])

        report = AutonomousEvidenceReport(
            generated_at=datetime.now(timezone.utc),
            mode=selected.mode,
            final_status=_final_status(selected, results),
            tasks_total=len(results),
            tasks_passed=sum(1 for result in results if result.status == AutonomousEvidenceTaskStatus.PASS),
            tasks_warned=sum(1 for result in results if result.status == AutonomousEvidenceTaskStatus.WARN),
            tasks_failed=sum(1 for result in results if result.status == AutonomousEvidenceTaskStatus.FAIL),
            tasks_skipped=sum(1 for result in results if result.status == AutonomousEvidenceTaskStatus.SKIP),
            blocking_failures=[result.reason for result in results if result.blocking and result.status == AutonomousEvidenceTaskStatus.FAIL],
            task_results=results,
            output_paths=sorted(set(output_paths)),
            readiness_report=readiness.model_dump(mode="json") if readiness is not None else None,
            safety_flags=_safety_flags(selected),
        )
        exports: list[Path] = []
        if selected.export_json or (selected.mode == AutonomousEvidenceMode.DRY_RUN and selected.dry_run_summary):
            exports.append(export_autonomous_evidence_json(report, selected.reports_dir))
        if selected.export_txt or (selected.mode == AutonomousEvidenceMode.DRY_RUN and selected.dry_run_summary):
            exports.append(export_autonomous_evidence_txt(report, selected.reports_dir))
        if exports:
            report.output_paths = sorted(set(report.output_paths + [str(path) for path in exports]))
        return report

    def _run_task(self, task: AutonomousEvidenceTask, config: AutonomousEvidenceConfig) -> AutonomousEvidenceTaskResult:
        started = datetime.now(timezone.utc)
        if config.mode == AutonomousEvidenceMode.DRY_RUN:
            completed = datetime.now(timezone.utc)
            return AutonomousEvidenceTaskResult(
                task_name=task.name,
                status=AutonomousEvidenceTaskStatus.SKIP,
                started_at=started,
                completed_at=completed,
                duration_ms=_duration_ms(started, completed),
                output_paths=[],
                blocking=task.blocking,
                reason="dry-run plan only; report generation skipped",
                safety_flags=_task_safety_flags(task),
            )
        try:
            if task.runner is not None:
                status, reason, paths, extra_flags = task.runner(config)
            elif task.subprocess_command and config.allow_subprocess:
                status, reason, paths, extra_flags = _run_subprocess_task(task, config)
            elif task.subprocess_command:
                status, reason, paths, extra_flags = (
                    AutonomousEvidenceTaskStatus.SKIP,
                    "subprocess fallback is disabled; pass --allow-subprocess to enable it",
                    [],
                    {"subprocess_used": False, "subprocess_blocked": True},
                )
            else:
                status, reason, paths, extra_flags = AutonomousEvidenceTaskStatus.SKIP, "task has no runner", [], {}
            completed = datetime.now(timezone.utc)
            return AutonomousEvidenceTaskResult(
                task_name=task.name,
                status=status,
                started_at=started,
                completed_at=completed,
                duration_ms=_duration_ms(started, completed),
                output_paths=[str(path) for path in paths],
                blocking=task.blocking,
                reason=reason,
                safety_flags=_task_safety_flags(task) | extra_flags,
            )
        except Exception as exc:  # noqa: BLE001 - task failures are reported, not hidden
            completed = datetime.now(timezone.utc)
            status = AutonomousEvidenceTaskStatus.WARN if task.optional else AutonomousEvidenceTaskStatus.FAIL
            return AutonomousEvidenceTaskResult(
                task_name=task.name,
                status=status,
                started_at=started,
                completed_at=completed,
                duration_ms=_duration_ms(started, completed),
                output_paths=[],
                blocking=task.blocking,
                reason=f"{task.name} failed: {exc}",
                exception_class=exc.__class__.__name__,
                exception_message=str(exc),
                safety_flags=_task_safety_flags(task),
            )

    def _run_readiness(self, config: AutonomousEvidenceConfig) -> tuple[ReadinessReport, list[Path]]:
        if self.settings is None or self.database is None:
            return None, []  # type: ignore[return-value]
        readiness = build_readiness_report(
            self.settings,
            self.database,
            AutonomousReadinessConfig.from_environment(reports_dir=config.reports_dir, dry_run=True),
        )
        paths = [export_autonomous_readiness_json(readiness, config.reports_dir), export_autonomous_readiness_txt(readiness, config.reports_dir)]
        return readiness, paths


def build_default_task_plan(config: AutonomousEvidenceConfig | None = None) -> list[AutonomousEvidenceTask]:
    """Return the default read-only evidence plan using existing builders."""

    safe = _base_task_flags()
    return [
        AutonomousEvidenceTask(
            name="session_health_summary",
            description="Build reports/session_health_summary.json from local paper/report artifacts.",
            blocking=True,
            safety_flags=safe,
            runner=_build_session_health,
        ),
        AutonomousEvidenceTask(
            name="data_health_report",
            description="Build reports/data_health_report.json and .txt from local report files.",
            blocking=True,
            safety_flags=safe,
            runner=_build_data_health,
        ),
        AutonomousEvidenceTask(
            name="failure_diagnostics_report",
            description="Build reports/failure_diagnostics_summary.json and .txt from existing artifacts.",
            blocking=True,
            safety_flags=safe,
            runner=_build_failure_diagnostics,
        ),
        AutonomousEvidenceTask(
            name="signal_anomaly_detector",
            description="Build reports/signal_anomaly_summary.json from local signal journals.",
            blocking=False,
            optional=True,
            safety_flags=safe,
            runner=_build_signal_anomaly,
        ),
        AutonomousEvidenceTask(
            name="mt5_symbol_mapping_audit_static",
            description="Build reports/mt5_symbol_mapping_audit.json in static/no-terminal mode.",
            blocking=False,
            optional=True,
            safety_flags=safe | {"mt5_terminal_required": False},
            runner=_build_mt5_mapping_static,
        ),
    ]


def build_evidence(
    settings: AppSettings | None = None,
    database: Database | None = None,
    config: AutonomousEvidenceConfig | None = None,
    tasks: list[AutonomousEvidenceTask] | None = None,
) -> AutonomousEvidenceReport:
    return AutonomousEvidenceBuilderService(settings, database).build(config=config, tasks=tasks)


def export_autonomous_evidence_json(report: AutonomousEvidenceReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_EVIDENCE_JSON_REPORT
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_autonomous_evidence_txt(report: AutonomousEvidenceReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_EVIDENCE_TXT_REPORT
    path.write_text(format_autonomous_evidence_txt(report), encoding="utf-8")
    return path


def format_autonomous_evidence_txt(report: AutonomousEvidenceReport) -> str:
    lines = [
        "Autonomous Evidence Builder Report",
        "This report is read-only and does not authorize live trading or broker execution.",
        f"generated_at: {report.generated_at.isoformat()}",
        f"mode: {report.mode.value}",
        f"final_status: {report.final_status.value}",
        f"tasks_total: {report.tasks_total}",
        f"tasks_passed: {report.tasks_passed}",
        f"tasks_warned: {report.tasks_warned}",
        f"tasks_failed: {report.tasks_failed}",
        f"tasks_skipped: {report.tasks_skipped}",
        "blocking_failures:",
    ]
    lines.extend([f"- {reason}" for reason in report.blocking_failures] or ["- none"])
    lines.append("tasks:")
    for result in report.task_results:
        lines.append(
            f"- {result.task_name}: {result.status.value} blocking={str(result.blocking).lower()} "
            f"duration_ms={result.duration_ms} reason={result.reason}"
        )
    lines.append("output_paths:")
    lines.extend([f"- {path}" for path in report.output_paths] or ["- none"])
    if report.readiness_report:
        lines.append(f"readiness_report: {report.readiness_report.get('final_status')}")
    lines.append("safety_flags:")
    for key, value in report.safety_flags.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def _build_session_health(config: AutonomousEvidenceConfig) -> tuple[AutonomousEvidenceTaskStatus, str, list[Path], dict[str, object]]:
    paths = {
        "signal_journal": config.reports_dir / "signal_journal.jsonl",
        "forward_test": config.reports_dir / "forward_test_paper.csv",
        "backtest_summary": config.reports_dir / "backtest_multi_asset_summary.json",
        "multi_asset_summary": config.reports_dir / "multi_asset_signal_report_summary.json",
    }
    records = collect_session_health_records(paths, asset_class=config.asset_class, symbol=config.symbols[0] if len(config.symbols) == 1 else None)
    summary = build_session_health_summary(records)
    path = export_session_health_json(summary, config.reports_dir / "session_health_summary.json")
    status = AutonomousEvidenceTaskStatus.PASS if records else AutonomousEvidenceTaskStatus.WARN
    return status, f"session health summary generated from {len(records)} local record(s)", [path], {"records_read": len(records)}


def _build_data_health(config: AutonomousEvidenceConfig) -> tuple[AutonomousEvidenceTaskStatus, str, list[Path], dict[str, object]]:
    report = build_data_health_report(DataHealthOptions(reports_dir=config.reports_dir))
    text = render_text_report(report)
    json_path = config.reports_dir / "data_health_report.json"
    txt_path = config.reports_dir / "data_health_report.txt"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    status_text = str(report.get("data_quality_status") or "WARN").upper()
    status = AutonomousEvidenceTaskStatus.PASS if status_text == "HEALTHY" else AutonomousEvidenceTaskStatus.WARN
    return status, f"data health report generated with status {status_text}", [json_path, txt_path], {}


def _build_failure_diagnostics(config: AutonomousEvidenceConfig) -> tuple[AutonomousEvidenceTaskStatus, str, list[Path], dict[str, object]]:
    summary = build_failure_diagnostics_summary(FailureDiagnosticsOptions(reports_dir=config.reports_dir, show_suggestions=True))
    json_path = export_failure_diagnostics_json(summary, config.reports_dir)
    txt_path = export_failure_diagnostics_txt(summary, config.reports_dir)
    severity = str(summary.get("severity") or "WARN").upper()
    status = AutonomousEvidenceTaskStatus.PASS if severity == "CLEAN" else AutonomousEvidenceTaskStatus.WARN
    return status, f"failure diagnostics generated with severity {severity}", [json_path, txt_path], {}


def _build_signal_anomaly(config: AutonomousEvidenceConfig) -> tuple[AutonomousEvidenceTaskStatus, str, list[Path], dict[str, object]]:
    records = collect_records(config.reports_dir)
    if config.asset_class != "all":
        records = [record for record in records if str(record.get("asset_class") or "").lower() == config.asset_class]
    if len(config.symbols) == 1:
        records = [record for record in records if str(record.get("symbol") or "").upper() == config.symbols[0]]
    anomalies = detect_anomalies(records)
    summary = build_summary(records, anomalies, top_n=20)
    path = config.reports_dir / "signal_anomaly_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    integrity = str(summary.get("data_integrity_status") or "WARN").upper()
    status = AutonomousEvidenceTaskStatus.PASS if integrity in {"CLEAN", "OK"} else AutonomousEvidenceTaskStatus.WARN
    return status, f"signal anomaly summary generated with integrity {integrity}", [path], {"records_read": len(records)}


def _build_mt5_mapping_static(config: AutonomousEvidenceConfig) -> tuple[AutonomousEvidenceTaskStatus, str, list[Path], dict[str, object]]:
    report = run_mapping_audit(MappingAuditOptions(watchlist=config.watchlist, check_reports=False, check_static=True))
    path = export_audit_json(report, config.reports_dir / "mt5_symbol_mapping_audit.json")
    mapping_status = str(report.get("mapping_status") or "WARN").upper()
    status = AutonomousEvidenceTaskStatus.PASS if mapping_status in {"OK", "PASS"} else AutonomousEvidenceTaskStatus.WARN
    return status, f"static MT5 symbol mapping audit generated with status {mapping_status}", [path], {"mt5_called": False, "mt5_terminal_required": False}


def _run_subprocess_task(task: AutonomousEvidenceTask, config: AutonomousEvidenceConfig) -> tuple[AutonomousEvidenceTaskStatus, str, list[Path], dict[str, object]]:
    command = list(task.subprocess_command or [])
    if not command:
        return AutonomousEvidenceTaskStatus.SKIP, "empty subprocess command", [], {"subprocess_used": False}
    completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=60, check=False)  # noqa: S603
    status = AutonomousEvidenceTaskStatus.PASS if completed.returncode == 0 else AutonomousEvidenceTaskStatus.FAIL
    return status, f"subprocess exited with code {completed.returncode}", [], {"subprocess_used": True, "subprocess_command": command}


def _final_status(config: AutonomousEvidenceConfig, results: list[AutonomousEvidenceTaskResult]) -> AutonomousEvidenceFinalStatus:
    if config.mode == AutonomousEvidenceMode.DRY_RUN:
        return AutonomousEvidenceFinalStatus.DRY_RUN_PLAN
    if any(result.blocking and result.status == AutonomousEvidenceTaskStatus.FAIL for result in results):
        return AutonomousEvidenceFinalStatus.BLOCKED_EVIDENCE
    if any(result.status in {AutonomousEvidenceTaskStatus.WARN, AutonomousEvidenceTaskStatus.FAIL, AutonomousEvidenceTaskStatus.SKIP} for result in results):
        return AutonomousEvidenceFinalStatus.WARN_EVIDENCE
    return AutonomousEvidenceFinalStatus.READY_EVIDENCE


def _duration_ms(started: datetime, completed: datetime) -> int:
    return int((completed - started).total_seconds() * 1000)


def _base_task_flags() -> dict[str, object]:
    return {
        "paper_demo_only": True,
        "live_execution_allowed": False,
        "broker_live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_called": False,
        "orders_sent": False,
        "order_send_called": False,
        "env_mutation_performed": False,
        "credentials_printed": False,
        "hidden_daemon_created": False,
        "infinite_loop_default": False,
        "network_required": False,
        "subprocess_used": False,
    }


def _task_safety_flags(task: AutonomousEvidenceTask) -> dict[str, object]:
    return _base_task_flags() | task.safety_flags


def _safety_flags(config: AutonomousEvidenceConfig) -> dict[str, object]:
    return _base_task_flags() | {
        "mode": config.mode.value,
        "allow_subprocess": config.allow_subprocess,
        "read_only_local_artifacts_only": config.mode in {AutonomousEvidenceMode.READ_ONLY, AutonomousEvidenceMode.DRY_RUN},
        "refresh_still_paper_demo_only": config.mode == AutonomousEvidenceMode.REFRESH,
    }
