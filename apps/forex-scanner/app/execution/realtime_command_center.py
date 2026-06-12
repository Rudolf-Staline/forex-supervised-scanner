"""Unified safe Realtime Paper Command Center.

The command center is an operator-facing orchestration layer for the bounded
paper/demo realtime stack.  It is diagnostic/paper only: it never enables live
trading, never submits broker orders, never mutates ``.env``, never creates a
daemon, and delegates realtime work only to bounded foreground services.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

from app.config.settings import AppSettings, PROJECT_ROOT
from app.core.types import Timeframe
from app.data.providers import MarketDataProvider
from app.execution.autonomous_evidence import AutonomousEvidenceConfig, AutonomousEvidenceMode, build_evidence
from app.execution.autonomous_policy import AutonomousPolicyContext, AutonomousPolicyEngine, AutonomousPolicyMode
from app.execution.autonomous_readiness import AutonomousReadinessConfig, build_readiness_report
from app.execution.autonomous_recovery import AutonomousRecoveryConfig, build_recovery_plan, export_autonomous_recovery_json, export_autonomous_recovery_txt
from app.execution.autonomous_scenarios import AutonomousScenarioConfig, AutonomousScenarioRunnerService, load_builtin_scenarios
from app.execution.realtime_data_health import RealtimeDataHealthConfig, RealtimeDataHealthService
from app.execution.realtime_paper_positions import RealtimePaperPositionConfig, RealtimePaperPositionManagerService
from app.execution.realtime_paper_supervisor import (
    RealtimePaperSupervisorConfig,
    RealtimePaperSupervisorService,
    realtime_safety_drift_reasons,
    realtime_safety_flags,
    symbols_from_args,
)
from app.storage.database import Database

DEFAULT_REALTIME_COMMAND_CENTER_JSON = "realtime_command_center_summary.json"
DEFAULT_REALTIME_COMMAND_CENTER_TXT = "realtime_command_center_report.txt"
DEFAULT_REALTIME_COMMAND_CENTER_REPORTS_DIR = PROJECT_ROOT / "reports"


class RealtimeCommandCenterFinalStatus(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    WARN = "WARN"


class RealtimeCommandCenterConfig(BaseModel):
    provider: str = "auto"
    symbols: list[str]
    watchlist: str | None = None
    timeframe: Timeframe = Timeframe.M1
    interval_seconds: float = Field(default=60.0, ge=0.0, le=86_400.0)
    max_cycles: int = Field(default=5, ge=1, le=100)
    max_runtime_minutes: float | None = Field(default=None, gt=0.0, le=24 * 60)
    dry_run: bool = True
    build_evidence_first: bool = False
    run_scenarios: bool = False
    manage_positions: bool = False
    plan_recovery_on_block: bool = False
    export_json: bool = False
    export_txt: bool = False
    reports_dir: Path = DEFAULT_REALTIME_COMMAND_CENTER_REPORTS_DIR
    strict: bool = False
    max_data_age_seconds: float | None = Field(default=None, gt=0.0)
    min_data_quality_score: float = Field(default=75.0, ge=0.0, le=100.0)
    warn_data_quality_score: float = Field(default=90.0, ge=0.0, le=100.0)
    max_spread_atr_ratio: float = Field(default=0.25, gt=0.0, le=10.0)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        symbols = [symbol.strip().upper() for symbol in value if symbol.strip()]
        if not symbols:
            raise ValueError("at least one symbol is required")
        return symbols

    @field_validator("timeframe", mode="before")
    @classmethod
    def normalize_timeframe(cls, value: object) -> object:
        return value.value if isinstance(value, Timeframe) else str(value).upper() if isinstance(value, str) else value


class RealtimeCommandCenterStageResult(BaseModel):
    name: str
    status: str
    started_at: datetime
    completed_at: datetime
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    output_paths: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] | None = None


class RealtimeCommandCenterReport(BaseModel):
    started_at: datetime
    completed_at: datetime
    provider: str
    symbols: list[str]
    timeframe: Timeframe
    final_status: str
    data_health_status: str = "NOT_RUN"
    evidence_status: str = "NOT_RUN"
    readiness_status: str = "NOT_RUN"
    policy_decision: str = "NOT_RUN"
    recovery_plan_status: str = "NOT_RUN"
    scenario_suite_status: str | None = None
    supervisor_status: str = "NOT_RUN"
    position_manager_status: str | None = None
    paper_orders_created: int = 0
    paper_positions_updated: int = 0
    stop_reason: str = "UNKNOWN"
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    safety_flags: dict[str, object] = Field(default_factory=dict)
    output_paths: dict[str, str] = Field(default_factory=dict)
    stages: list[RealtimeCommandCenterStageResult] = Field(default_factory=list)


class RealtimeCommandCenterService:
    def __init__(
        self,
        settings: AppSettings,
        provider: MarketDataProvider,
        database: Database,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        data_health_service: RealtimeDataHealthService | None = None,
        supervisor_service: RealtimePaperSupervisorService | None = None,
        position_manager: RealtimePaperPositionManagerService | None = None,
        scenario_runner: AutonomousScenarioRunnerService | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.sleep_fn = sleep_fn
        self.data_health_service = data_health_service or RealtimeDataHealthService(provider, now_fn=self.now_fn)
        self.supervisor_service = supervisor_service
        self.position_manager = position_manager
        self.scenario_runner = scenario_runner

    def run(self, config: RealtimeCommandCenterConfig) -> RealtimeCommandCenterReport:
        started_at = self.now_fn()
        config.reports_dir.mkdir(parents=True, exist_ok=True)
        stages: list[RealtimeCommandCenterStageResult] = []
        blocking: list[str] = []
        warnings: list[str] = []
        output_paths: dict[str, str] = {}

        safety_flags = realtime_safety_flags(self.settings)
        drift = realtime_safety_drift_reasons(self.settings)
        stages.append(self._stage("runtime_safety_heartbeat", "BLOCKED" if drift else "PASS", blocking_reasons=drift, payload={"safety_flags": safety_flags}))
        blocking.extend(drift)

        data_report = self.data_health_service.check(RealtimeDataHealthConfig(
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            reports_dir=config.reports_dir,
            export_json=config.export_json,
            export_txt=config.export_txt,
            max_age_seconds=config.max_data_age_seconds,
            min_quality_score=config.min_data_quality_score,
            warn_quality_score=config.warn_data_quality_score,
            max_spread_atr_ratio=config.max_spread_atr_ratio,
        ))
        data_payload = data_report.model_dump(mode="json")
        stages.append(self._stage("realtime_data_health", data_report.status.value, blocking_reasons=data_report.blocking_reasons, warnings=data_report.warnings, output_paths=_paths_dict(data_report.output_paths), payload=data_payload))
        blocking.extend(data_report.blocking_reasons)
        warnings.extend(data_report.warnings)
        output_paths.update({f"data_health_{k}": v for k, v in _paths_dict(data_report.output_paths).items()})

        evidence_status = "SKIPPED"
        readiness_status = "SKIPPED"
        policy_decision = "SKIPPED"
        if config.build_evidence_first or config.dry_run or data_report.safe_for_realtime_paper:
            evidence = build_evidence(
                self.settings,
                self.database,
                AutonomousEvidenceConfig(
                    mode=AutonomousEvidenceMode.DRY_RUN if config.dry_run else AutonomousEvidenceMode.READ_ONLY,
                    reports_dir=config.reports_dir,
                    symbols=config.symbols,
                    export_json=config.export_json,
                    export_txt=config.export_txt,
                    fail_fast=False,
                ),
            )
            evidence_status = evidence.final_status.value
            stages.append(self._stage("evidence_builder", evidence_status, blocking_reasons=evidence.blocking_failures, warnings=[r.reason for r in evidence.task_results if r.status.value == "WARN"], output_paths=_paths_dict(evidence.output_paths), payload=evidence.model_dump(mode="json")))
            blocking.extend(evidence.blocking_failures)
            output_paths.update({f"evidence_{i}": path for i, path in enumerate(evidence.output_paths, start=1)})

            readiness = build_readiness_report(self.settings, self.database, AutonomousReadinessConfig(dry_run=config.dry_run, reports_dir=config.reports_dir))
            readiness_status = readiness.final_status.value
            stages.append(self._stage("readiness_gate", readiness_status, blocking_reasons=readiness.blocking_reasons, warnings=readiness.warning_reasons, payload=readiness.model_dump(mode="json")))
            blocking.extend(readiness.blocking_reasons)
            warnings.extend(readiness.warning_reasons)

            policy = AutonomousPolicyEngine().can_run_supervisor_cycle(AutonomousPolicyContext(
                mode=AutonomousPolicyMode.DRY_RUN if config.dry_run else AutonomousPolicyMode.PAPER,
                dry_run=config.dry_run,
                readiness_status=readiness_status,
                evidence_status=evidence_status,
                operator_mode="normal",
            ))
            policy_decision = policy.decision.value
            stages.append(self._stage("policy_engine", policy_decision, blocking_reasons=policy.blocking_reasons, warnings=policy.warnings, payload=policy.model_dump(mode="json")))
            blocking.extend(policy.blocking_reasons)
            warnings.extend(policy.warnings)
        else:
            stages.extend([
                self._stage("evidence_builder", evidence_status),
                self._stage("readiness_gate", readiness_status),
                self._stage("policy_engine", policy_decision),
            ])

        recovery_status = "NOT_RUN"
        if config.plan_recovery_on_block and blocking:
            plan = build_recovery_plan(AutonomousRecoveryConfig(reports_dir=config.reports_dir, dry_run=True, execute_safe_actions=False))
            recovery_status = plan.final_status.value
            paths: dict[str, str] = {}
            if config.export_json:
                paths["json"] = str(export_autonomous_recovery_json(plan, config.reports_dir))
            if config.export_txt:
                paths["txt"] = str(export_autonomous_recovery_txt(plan, config.reports_dir))
            stages.append(self._stage("recovery_planner", recovery_status, blocking_reasons=plan.blocking_reasons, output_paths=paths, payload=plan.model_dump(mode="json")))
            output_paths.update({f"recovery_{k}": v for k, v in paths.items()})
        else:
            stages.append(self._stage("recovery_planner", recovery_status))

        scenario_status: str | None = None
        if config.run_scenarios:
            runner = self.scenario_runner or AutonomousScenarioRunnerService(AutonomousScenarioConfig(reports_dir=config.reports_dir, strict=config.strict, include_policy_report=True, include_recovery_plan=config.plan_recovery_on_block))
            suite = runner.run_scenario_suite(load_builtin_scenarios(), fail_fast=config.strict)
            scenario_status = suite.final_status.value
            paths: dict[str, str] = {}
            if config.export_json:
                paths["json"] = str(runner.export_json(suite, config.reports_dir))
            if config.export_txt:
                paths["txt"] = str(runner.export_txt(suite, config.reports_dir))
            stages.append(self._stage("autonomous_scenario_runner", scenario_status, warnings=[r.scenario_id for r in suite.scenario_results if r.status.value == "WARN"], output_paths=paths, payload=suite.model_dump(mode="json")))
            output_paths.update({f"scenarios_{k}": v for k, v in paths.items()})

        supervisor = self._supervisor().run(RealtimePaperSupervisorConfig(
            provider=config.provider,
            symbols=config.symbols,
            watchlist=config.watchlist,
            timeframe=config.timeframe,
            interval_seconds=config.interval_seconds,
            max_cycles=config.max_cycles,
            max_runtime_minutes=config.max_runtime_minutes,
            dry_run=config.dry_run,
            build_evidence_first=config.build_evidence_first,
            plan_recovery_on_block=config.plan_recovery_on_block,
            export_json=config.export_json,
            export_txt=config.export_txt,
            reports_dir=config.reports_dir,
            max_data_age_seconds=config.max_data_age_seconds,
            min_data_quality_score=config.min_data_quality_score,
            warn_data_quality_score=config.warn_data_quality_score,
            max_spread_atr_ratio=config.max_spread_atr_ratio,
            manage_positions=False,
        ))
        stages.append(self._stage("realtime_paper_supervisor", supervisor.stop_reason, blocking_reasons=supervisor.blocking_reasons, output_paths=supervisor.output_paths, payload=supervisor.model_dump(mode="json")))
        blocking.extend(supervisor.blocking_reasons)
        output_paths.update({f"supervisor_{k}": v for k, v in supervisor.output_paths.items()})

        position_status: str | None = None
        paper_positions_updated = supervisor.positions_updated
        if config.manage_positions:
            if supervisor.cycles_completed > 0:
                position_report = self._position_manager().evaluate_position_lifecycle(RealtimePaperPositionConfig(
                    provider=config.provider,
                    symbols=config.symbols,
                    timeframe=config.timeframe,
                    dry_run=config.dry_run,
                    export_json=config.export_json,
                    export_txt=config.export_txt,
                    reports_dir=config.reports_dir,
                    max_age_seconds=config.max_data_age_seconds,
                    max_spread_atr_ratio=config.max_spread_atr_ratio,
                ))
                position_status = "COMPLETED" if not position_report.blocking_reasons else "BLOCKED"
                paper_positions_updated += position_report.positions_updated
                stages.append(self._stage("realtime_paper_position_manager", position_status, blocking_reasons=position_report.blocking_reasons, warnings=position_report.warnings, output_paths=position_report.output_paths, payload=position_report.model_dump(mode="json")))
                blocking.extend(position_report.blocking_reasons)
                warnings.extend(position_report.warnings)
                output_paths.update({f"position_manager_{k}": v for k, v in position_report.output_paths.items()})
            else:
                position_status = "SKIPPED_BLOCKED_SUPERVISOR"
                stages.append(self._stage("realtime_paper_position_manager", position_status, warnings=["position manager skipped because supervisor did not complete a safe cycle"]))

        stop_reason = supervisor.stop_reason if supervisor.stop_reason else ("BLOCKED" if blocking else "COMPLETED")
        final_status = _final_status(stop_reason, blocking, warnings)
        report = RealtimeCommandCenterReport(
            started_at=started_at,
            completed_at=self.now_fn(),
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            final_status=final_status.value,
            data_health_status=data_report.status.value,
            evidence_status=evidence_status,
            readiness_status=readiness_status,
            policy_decision=policy_decision,
            recovery_plan_status=recovery_status,
            scenario_suite_status=scenario_status,
            supervisor_status=supervisor.stop_reason,
            position_manager_status=position_status,
            paper_orders_created=supervisor.paper_orders_created,
            paper_positions_updated=paper_positions_updated,
            stop_reason=stop_reason,
            blocking_reasons=_dedupe(blocking),
            warnings=_dedupe(warnings),
            safety_flags=safety_flags | supervisor.safety_flags | {"command_center_paper_demo_only": True},
            output_paths=output_paths,
            stages=stages,
        )
        if config.export_json:
            output_paths["summary_json"] = str(config.reports_dir / DEFAULT_REALTIME_COMMAND_CENTER_JSON)
        if config.export_txt:
            output_paths["summary_txt"] = str(config.reports_dir / DEFAULT_REALTIME_COMMAND_CENTER_TXT)
        report.output_paths = output_paths
        if config.export_json:
            export_realtime_command_center_json(report, config.reports_dir)
        if config.export_txt:
            export_realtime_command_center_txt(report, config.reports_dir)
        return report

    def _supervisor(self) -> RealtimePaperSupervisorService:
        if self.supervisor_service is not None:
            return self.supervisor_service
        return RealtimePaperSupervisorService(
            self.settings,
            self.provider,
            self.database,
            now_fn=self.now_fn,
            sleep_fn=self.sleep_fn,
            data_health_service=self.data_health_service,
            position_manager=self.position_manager,
        )

    def _position_manager(self) -> RealtimePaperPositionManagerService:
        if self.position_manager is None:
            self.position_manager = RealtimePaperPositionManagerService(self.settings, self.provider, self.database, now_fn=self.now_fn)
        return self.position_manager

    def _stage(self, name: str, status: str, *, blocking_reasons: list[str] | None = None, warnings: list[str] | None = None, output_paths: dict[str, str] | None = None, payload: dict[str, Any] | None = None) -> RealtimeCommandCenterStageResult:
        now = self.now_fn()
        return RealtimeCommandCenterStageResult(name=name, status=status, started_at=now, completed_at=now, blocking_reasons=blocking_reasons or [], warnings=warnings or [], output_paths=output_paths or {}, payload=payload)


def export_realtime_command_center_json(report: RealtimeCommandCenterReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_COMMAND_CENTER_JSON
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_realtime_command_center_txt(report: RealtimeCommandCenterReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_COMMAND_CENTER_TXT
    lines = [
        "Realtime Paper Command Center Report",
        "SAFETY: paper/demo only; live execution is disabled; broker order submission is disabled.",
        f"final_status={report.final_status}",
        f"stop_reason={report.stop_reason}",
        f"data_health_status={report.data_health_status}",
        f"evidence_status={report.evidence_status}",
        f"readiness_status={report.readiness_status}",
        f"policy_decision={report.policy_decision}",
        f"recovery_plan_status={report.recovery_plan_status}",
        f"scenario_suite_status={report.scenario_suite_status}",
        f"supervisor_status={report.supervisor_status}",
        f"position_manager_status={report.position_manager_status}",
        f"paper_orders_created={report.paper_orders_created}",
        f"paper_positions_updated={report.paper_positions_updated}",
        "blocking_reasons:",
    ]
    lines.extend([f"- {reason}" for reason in report.blocking_reasons] or ["- none"])
    lines.append("warnings:")
    lines.extend([f"- {warning}" for warning in report.warnings] or ["- none"])
    lines.append("stages:")
    lines.extend([f"- {stage.name}: {stage.status}" for stage in report.stages])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def command_center_symbols_from_args(symbols: list[str] | None, watchlist: str | None) -> list[str]:
    return symbols_from_args(symbols, watchlist)


def _final_status(stop_reason: str, blocking: list[str], warnings: list[str]) -> RealtimeCommandCenterFinalStatus:
    if blocking or stop_reason.startswith("BLOCKED"):
        return RealtimeCommandCenterFinalStatus.BLOCKED
    if warnings:
        return RealtimeCommandCenterFinalStatus.WARN
    return RealtimeCommandCenterFinalStatus.COMPLETED


def _paths_dict(paths: list[str] | dict[str, str]) -> dict[str, str]:
    if isinstance(paths, dict):
        return {str(k): str(v) for k, v in paths.items()}
    result: dict[str, str] = {}
    for index, path in enumerate(paths, start=1):
        result[str(index)] = str(path)
    return result


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
