"""Bounded realtime paper supervisor readiness layer.

Foreground-only orchestration for realtime paper/demo checks.  This layer never
submits live broker orders, never calls ``order_send``, never mutates ``.env``,
and always stops at configured ``max_cycles`` or ``max_runtime_minutes``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator

from app.config.safety import demo_safety_status
from app.config.settings import AppSettings, PROJECT_ROOT
from app.config.watchlists import get_watchlist
from app.core.types import Timeframe, TradingStyle
from app.data.providers import MarketDataProvider
from app.execution.autonomous_evidence import AutonomousEvidenceConfig, AutonomousEvidenceFinalStatus, AutonomousEvidenceMode, build_evidence
from app.execution.autonomous_policy import AutonomousPolicyContext, AutonomousPolicyEngine, AutonomousPolicyMode
from app.execution.autonomous_readiness import AutonomousReadinessConfig, AutonomousReadinessFinalStatus, build_readiness_report
from app.execution.autonomous_recovery import AutonomousRecoveryConfig, build_recovery_plan
from app.execution.autonomous_supervisor import AutonomousSupervisorConfig, AutonomousSupervisorService
from app.execution.realtime_paper_positions import RealtimePaperPositionConfig, RealtimePaperPositionManagerService, RealtimePaperPositionReport
from app.execution.realtime_data_health import (
    RealtimeDataHealthConfig,
    RealtimeDataHealthReport,
    RealtimeDataHealthStatus,
    RealtimeDataHealthService,
)
from app.storage.database import Database

DEFAULT_REALTIME_SUPERVISOR_JSON = "realtime_paper_supervisor_summary.json"
DEFAULT_REALTIME_SUPERVISOR_TXT = "realtime_paper_supervisor_report.txt"
DEFAULT_REALTIME_HEARTBEAT_JSONL = "realtime_heartbeat.jsonl"
DEFAULT_REALTIME_REPORTS_DIR = PROJECT_ROOT / "reports"


class RealtimePaperStopReason(StrEnum):
    COMPLETED_MAX_CYCLES = "COMPLETED_MAX_CYCLES"
    COMPLETED_MAX_RUNTIME = "COMPLETED_MAX_RUNTIME"
    BLOCKED_BY_SAFETY_DRIFT = "BLOCKED_BY_SAFETY_DRIFT"
    BLOCKED_STALE_DATA = "BLOCKED_STALE_DATA"
    BLOCKED_SYNTHETIC_FALLBACK = "BLOCKED_SYNTHETIC_FALLBACK"
    BLOCKED_DATA_HEALTH = "BLOCKED_DATA_HEALTH"
    BLOCKED_BY_OPERATOR_CONTROL = "BLOCKED_BY_OPERATOR_CONTROL"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    BLOCKED_BY_READINESS = "BLOCKED_BY_READINESS"
    BLOCKED_BY_EVIDENCE = "BLOCKED_BY_EVIDENCE"
    BLOCKED_BY_PROVIDER_FAILURES = "BLOCKED_BY_PROVIDER_FAILURES"


class RealtimePaperSupervisorConfig(BaseModel):
    provider: str = "auto"
    symbols: list[str]
    watchlist: str | None = None
    timeframe: Timeframe = Timeframe.M1
    interval_seconds: float = Field(default=60.0, ge=0.0, le=86_400.0)
    max_cycles: int = Field(default=5, ge=1, le=100)
    max_runtime_minutes: float | None = Field(default=None, gt=0.0, le=24 * 60)
    dry_run: bool = True
    build_evidence_first: bool = False
    plan_recovery_on_block: bool = False
    export_json: bool = False
    export_txt: bool = False
    reports_dir: Path = DEFAULT_REALTIME_REPORTS_DIR
    max_consecutive_provider_failures: int = Field(default=2, ge=1, le=20)
    max_data_age_seconds: float | None = Field(default=None, gt=0.0)
    min_data_quality_score: float = Field(default=75.0, ge=0.0, le=100.0)
    warn_data_quality_score: float = Field(default=90.0, ge=0.0, le=100.0)
    max_spread_atr_ratio: float = Field(default=0.25, gt=0.0, le=10.0)
    manage_positions: bool = False

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


class RealtimeCycleRecord(BaseModel):
    cycle: int
    started_at: datetime
    completed_at: datetime
    data_health_status: str
    readiness_status: str
    policy_decision: str
    evidence_status: str = "UNKNOWN"
    paper_orders_created: int = 0
    positions_updated: int = 0
    positions_closed: int = 0
    partial_exits_created: int = 0
    stop_reason: str | None = None
    safety_flags: dict[str, object] = Field(default_factory=dict)
    operator_controls: dict[str, object] = Field(default_factory=dict)
    blocking_reasons: list[str] = Field(default_factory=list)


class RealtimePaperSupervisorReport(BaseModel):
    run_id: str
    started_at: datetime
    completed_at: datetime
    provider: str
    symbols: list[str]
    timeframe: Timeframe
    latest_data_age_seconds: float | None = None
    data_health_status: str
    readiness_status: str
    policy_decision: str
    evidence_status: str = "UNKNOWN"
    recovery_plan_summary: str | None = None
    cycles_attempted: int
    cycles_completed: int
    paper_orders_created: int
    positions_updated: int = 0
    positions_closed: int = 0
    partial_exits_created: int = 0
    stop_reason: str
    safety_flags: dict[str, object]
    operator_controls: dict[str, object]
    output_paths: dict[str, str] = Field(default_factory=dict)
    cycles: list[RealtimeCycleRecord] = Field(default_factory=list)
    data_health_report: dict[str, object] | None = None
    position_report: dict[str, object] | None = None
    position_lifecycle_summary: dict[str, object] = Field(default_factory=dict)
    readiness_report: dict[str, object] | None = None
    evidence_report: dict[str, object] | None = None
    blocking_reasons: list[str] = Field(default_factory=list)


class RealtimePaperSupervisorService:
    def __init__(
        self,
        settings: AppSettings,
        provider: MarketDataProvider,
        database: Database,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        data_health_service: RealtimeDataHealthService | None = None,
        autonomous_runner: Callable[[RealtimePaperSupervisorConfig], int] | None = None,
        position_manager: RealtimePaperPositionManagerService | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.sleep_fn = sleep_fn or time.sleep
        self.data_health_service = data_health_service or RealtimeDataHealthService(provider, now_fn=self.now_fn)
        self.autonomous_runner = autonomous_runner
        self.position_manager = position_manager

    def run(self, config: RealtimePaperSupervisorConfig) -> RealtimePaperSupervisorReport:
        started_at = self.now_fn()
        run_id = str(uuid.uuid4())
        config.reports_dir.mkdir(parents=True, exist_ok=True)
        heartbeat_path = config.reports_dir / DEFAULT_REALTIME_HEARTBEAT_JSONL
        cycles: list[RealtimeCycleRecord] = []
        stop_reason = RealtimePaperStopReason.COMPLETED_MAX_CYCLES.value
        recovery_summary: str | None = None
        readiness_status = "UNKNOWN"
        policy_label = "UNKNOWN"
        data_report: RealtimeDataHealthReport | None = None
        readiness_payload: dict[str, object] | None = None
        evidence_status = "UNKNOWN"
        evidence_payload: dict[str, object] | None = None
        provider_failures = 0
        orders_created = 0
        positions_updated = 0
        positions_closed = 0
        partial_exits_created = 0
        position_payload: dict[str, object] | None = None
        blocking_reasons: list[str] = []

        deadline_seconds = config.max_runtime_minutes * 60.0 if config.max_runtime_minutes is not None else None
        for cycle_number in range(1, config.max_cycles + 1):
            cycle_started = self.now_fn()
            drift_reasons = realtime_safety_drift_reasons(self.settings)
            controls = self._operator_controls()
            if drift_reasons:
                stop_reason = RealtimePaperStopReason.BLOCKED_BY_SAFETY_DRIFT.value
                blocking_reasons.extend(drift_reasons)
                record = self._record(cycle_number, cycle_started, "UNKNOWN", readiness_status, policy_label, stop_reason, drift_reasons, controls, 0, evidence_status=evidence_status)
                self._write_heartbeat(heartbeat_path, run_id, record)
                cycles.append(record)
                break
            op_reasons = _operator_block_reasons(controls)
            if op_reasons:
                stop_reason = RealtimePaperStopReason.BLOCKED_BY_OPERATOR_CONTROL.value
                blocking_reasons.extend(op_reasons)
                record = self._record(cycle_number, cycle_started, "UNKNOWN", readiness_status, policy_label, stop_reason, op_reasons, controls, 0, evidence_status=evidence_status)
                self._write_heartbeat(heartbeat_path, run_id, record)
                cycles.append(record)
                break

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
            if data_report.status == RealtimeDataHealthStatus.BLOCKED_PROVIDER_FAILURE:
                provider_failures += 1
            else:
                provider_failures = 0
            if provider_failures >= config.max_consecutive_provider_failures:
                stop_reason = RealtimePaperStopReason.BLOCKED_BY_PROVIDER_FAILURES.value
            elif data_report.status == RealtimeDataHealthStatus.BLOCKED_SYNTHETIC_FALLBACK:
                stop_reason = RealtimePaperStopReason.BLOCKED_SYNTHETIC_FALLBACK.value
            elif data_report.status == RealtimeDataHealthStatus.BLOCKED_STALE_DATA:
                stop_reason = RealtimePaperStopReason.BLOCKED_STALE_DATA.value
            elif not data_report.safe_for_realtime_paper:
                stop_reason = RealtimePaperStopReason.BLOCKED_DATA_HEALTH.value
            if not data_report.safe_for_realtime_paper or provider_failures >= config.max_consecutive_provider_failures:
                blocking_reasons.extend(data_report.blocking_reasons)
                if config.plan_recovery_on_block:
                    recovery_summary = _recovery_summary(build_recovery_plan(AutonomousRecoveryConfig(reports_dir=config.reports_dir)))
                record = self._record(cycle_number, cycle_started, data_report.status.value, readiness_status, policy_label, stop_reason, data_report.blocking_reasons, controls, 0, evidence_status=evidence_status)
                self._write_heartbeat(heartbeat_path, run_id, record)
                cycles.append(record)
                break

            evidence = build_evidence(
                self.settings,
                self.database,
                AutonomousEvidenceConfig(
                    mode=AutonomousEvidenceMode.READ_ONLY,
                    reports_dir=config.reports_dir,
                    symbols=config.symbols,
                    export_json=config.export_json,
                    export_txt=config.export_txt,
                    fail_fast=True,
                ),
            )
            evidence_status = evidence.final_status.value
            evidence_payload = evidence.model_dump(mode="json")
            if evidence.final_status == AutonomousEvidenceFinalStatus.BLOCKED_EVIDENCE:
                stop_reason = RealtimePaperStopReason.BLOCKED_BY_EVIDENCE.value
                blocking_reasons.extend(evidence.blocking_failures)
                if config.plan_recovery_on_block:
                    recovery_summary = _recovery_summary(build_recovery_plan(AutonomousRecoveryConfig(reports_dir=config.reports_dir)))
                record = self._record(cycle_number, cycle_started, data_report.status.value, readiness_status, policy_label, stop_reason, evidence.blocking_failures, controls, 0, evidence_status=evidence_status)
                self._write_heartbeat(heartbeat_path, run_id, record)
                cycles.append(record)
                break

            readiness = build_readiness_report(self.settings, self.database, AutonomousReadinessConfig(dry_run=config.dry_run, reports_dir=config.reports_dir))
            readiness_status = readiness.final_status.value
            readiness_payload = readiness.model_dump(mode="json")
            if readiness.final_status not in {AutonomousReadinessFinalStatus.READY, AutonomousReadinessFinalStatus.WARN_READY}:
                stop_reason = RealtimePaperStopReason.BLOCKED_BY_READINESS.value
                blocking_reasons.extend(readiness.blocking_reasons)
                if config.plan_recovery_on_block:
                    recovery_summary = _recovery_summary(build_recovery_plan(AutonomousRecoveryConfig(reports_dir=config.reports_dir)))
                record = self._record(cycle_number, cycle_started, data_report.status.value, readiness_status, policy_label, stop_reason, readiness.blocking_reasons, controls, 0, evidence_status=evidence_status)
                self._write_heartbeat(heartbeat_path, run_id, record)
                cycles.append(record)
                break

            policy = AutonomousPolicyEngine().can_run_supervisor_cycle(AutonomousPolicyContext(
                mode=AutonomousPolicyMode.DRY_RUN if config.dry_run else AutonomousPolicyMode.PAPER,
                dry_run=config.dry_run,
                readiness_status=readiness_status,
                evidence_status=evidence_status,
                operator_mode="normal",
            ))
            policy_label = policy.decision.value
            if not policy.allowed:
                stop_reason = RealtimePaperStopReason.BLOCKED_BY_POLICY.value
                blocking_reasons.extend(policy.blocking_reasons)
                record = self._record(cycle_number, cycle_started, data_report.status.value, readiness_status, policy_label, stop_reason, policy.blocking_reasons, controls, 0, evidence_status=evidence_status)
                self._write_heartbeat(heartbeat_path, run_id, record)
                cycles.append(record)
                break

            cycle_orders = self._run_autonomous_if_allowed(config)
            orders_created += cycle_orders
            cycle_positions_updated = 0
            cycle_positions_closed = 0
            cycle_partials = 0
            if config.manage_positions:
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
                position_payload = position_report.model_dump(mode="json")
                cycle_positions_updated = position_report.positions_updated
                cycle_positions_closed = position_report.positions_closed
                cycle_partials = position_report.partial_exits_created
                positions_updated += cycle_positions_updated
                positions_closed += cycle_positions_closed
                partial_exits_created += cycle_partials
            record = self._record(cycle_number, cycle_started, data_report.status.value, readiness_status, policy_label, None, [], controls, cycle_orders, evidence_status=evidence_status, positions_updated=cycle_positions_updated, positions_closed=cycle_positions_closed, partial_exits_created=cycle_partials)
            self._write_heartbeat(heartbeat_path, run_id, record)
            cycles.append(record)
            if deadline_seconds is not None and (self.now_fn() - started_at).total_seconds() >= deadline_seconds:
                stop_reason = RealtimePaperStopReason.COMPLETED_MAX_RUNTIME.value
                break
            if cycle_number < config.max_cycles and config.interval_seconds > 0:
                self.sleep_fn(config.interval_seconds)

        completed_at = self.now_fn()
        if not cycles:
            stop_reason = RealtimePaperStopReason.COMPLETED_MAX_CYCLES.value
        output_paths = {"heartbeat": str(heartbeat_path)}
        if config.export_json:
            output_paths["summary_json"] = str(config.reports_dir / DEFAULT_REALTIME_SUPERVISOR_JSON)
        if config.export_txt:
            output_paths["summary_txt"] = str(config.reports_dir / DEFAULT_REALTIME_SUPERVISOR_TXT)
        report = RealtimePaperSupervisorReport(
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            provider=config.provider,
            symbols=config.symbols,
            timeframe=config.timeframe,
            latest_data_age_seconds=data_report.latest_data_age_seconds if data_report else None,
            data_health_status=data_report.status.value if data_report else "UNKNOWN",
            readiness_status=readiness_status,
            policy_decision=policy_label,
            evidence_status=evidence_status,
            recovery_plan_summary=recovery_summary,
            cycles_attempted=len(cycles),
            cycles_completed=sum(1 for cycle in cycles if cycle.stop_reason is None),
            paper_orders_created=orders_created,
            positions_updated=positions_updated,
            positions_closed=positions_closed,
            partial_exits_created=partial_exits_created,
            stop_reason=stop_reason,
            safety_flags=realtime_safety_flags(self.settings),
            operator_controls=self._operator_controls(),
            output_paths=output_paths,
            cycles=cycles,
            data_health_report=data_report.model_dump(mode="json") if data_report else None,
            position_report=position_payload,
            position_lifecycle_summary={
                "manage_positions": config.manage_positions,
                "positions_updated": positions_updated,
                "positions_closed": positions_closed,
                "partial_exits_created": partial_exits_created,
            },
            readiness_report=readiness_payload,
            evidence_report=evidence_payload,
            blocking_reasons=blocking_reasons,
        )
        if config.export_json:
            export_realtime_paper_supervisor_json(report, config.reports_dir)
        if config.export_txt:
            export_realtime_paper_supervisor_txt(report, config.reports_dir)
        return report

    def _run_autonomous_if_allowed(self, config: RealtimePaperSupervisorConfig) -> int:
        if config.dry_run:
            return 0
        if self.autonomous_runner is not None:
            return int(self.autonomous_runner(config))
        result = AutonomousSupervisorService(self.settings, self.provider, self.database, sleep_fn=self.sleep_fn).run_once(
            AutonomousSupervisorConfig(
                enabled=True,
                style=TradingStyle.DAY_TRADING,
                symbols=config.symbols,
                max_cycles=1,
                interval_seconds=0,
                dry_run=False,
                reports_dir=config.reports_dir,
            )
        )
        return int(result.orders_created)

    def _operator_controls(self) -> dict[str, object]:
        try:
            return self.database.load_operator_controls().model_dump(mode="json")
        except Exception:
            return {
                "maintenance_mode": _env_bool("OPERATOR_MAINTENANCE_MODE"),
                "degraded_mode": _env_bool("OPERATOR_DEGRADED_MODE"),
            }

    def _record(self, cycle: int, started_at: datetime, data_status: str, readiness: str, policy: str, stop: str | None, reasons: list[str], controls: dict[str, object], orders: int, *, evidence_status: str = "UNKNOWN", positions_updated: int = 0, positions_closed: int = 0, partial_exits_created: int = 0) -> RealtimeCycleRecord:
        return RealtimeCycleRecord(
            cycle=cycle,
            started_at=started_at,
            completed_at=self.now_fn(),
            data_health_status=data_status,
            readiness_status=readiness,
            policy_decision=policy,
            evidence_status=evidence_status,
            paper_orders_created=orders,
            positions_updated=positions_updated,
            positions_closed=positions_closed,
            partial_exits_created=partial_exits_created,
            stop_reason=stop,
            safety_flags=realtime_safety_flags(self.settings),
            operator_controls=controls,
            blocking_reasons=reasons,
        )

    def _position_manager(self) -> RealtimePaperPositionManagerService:
        if self.position_manager is None:
            self.position_manager = RealtimePaperPositionManagerService(self.settings, self.provider, self.database, now_fn=self.now_fn)
        return self.position_manager

    def _write_heartbeat(self, path: Path, run_id: str, record: RealtimeCycleRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = record.model_dump(mode="json") | {
            "run_id": run_id,
            "heartbeat_at": self.now_fn().isoformat(),
            "heartbeat_sequence": record.cycle,
            "runtime_safety_heartbeat": True,
            "paper_demo_only": True,
            "live_execution_allowed": False,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def realtime_safety_flags(settings: AppSettings) -> dict[str, object]:
    return demo_safety_status(settings) | {
        "paper_demo_only": True,
        "live_trading_enabled": False,
        "live_execution_allowed": False,
        "broker_live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "mt5_order_execution_allowed": False,
        "order_send_called": False,
        "env_mutation_performed": False,
    }


def realtime_safety_drift_reasons(settings: AppSettings) -> list[str]:
    reasons: list[str] = []
    env_checks = {
        "EXECUTION_MODE": {"paper"},
        "ALLOW_LIVE_TRADING": {"false"},
        "AUTO_BOT_ENABLED": {"false"},
        "BROKER_MODE": {"paper", "mt5_demo"},
    }
    for name, allowed in env_checks.items():
        raw = os.getenv(name)
        if raw is None or raw.strip().lower() not in allowed:
            reasons.append(f"{name} drifted; expected one of {sorted(allowed)}, got {raw}")
    broker_mode = os.getenv("BROKER_MODE", "").strip().lower()
    if broker_mode == "mt5_demo":
        if os.getenv("MT5_DEMO_ONLY", "").strip().lower() != "true":
            reasons.append("BROKER_MODE=mt5_demo requires MT5_DEMO_ONLY=true")
        if os.getenv("MT5_SERVER", "").strip() != "Deriv-Demo":
            reasons.append("BROKER_MODE=mt5_demo requires MT5_SERVER=Deriv-Demo")
    if settings.broker.live_enabled:
        reasons.append("broker.live_enabled drifted true")
    if settings.execution_capabilities.broker_live_enabled:
        reasons.append("execution_capabilities.broker_live_enabled drifted true")
    confirmation = os.getenv(settings.broker.live_confirmation_env)
    if confirmation and confirmation.strip():
        reasons.append(f"{settings.broker.live_confirmation_env} must remain unset")
    if settings.execution.mode == "broker_live":
        reasons.append("execution.mode drifted to broker_live")
    return reasons


def export_realtime_paper_supervisor_json(report: RealtimePaperSupervisorReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_SUPERVISOR_JSON
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_realtime_paper_supervisor_txt(report: RealtimePaperSupervisorReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_REALTIME_SUPERVISOR_TXT
    lines = [
        f"realtime_paper_supervisor_stop_reason={report.stop_reason}",
        f"started_at={report.started_at.isoformat()}",
        f"completed_at={report.completed_at.isoformat()}",
        f"provider={report.provider}",
        f"symbols={','.join(report.symbols)}",
        f"timeframe={report.timeframe.value}",
        f"latest_data_age_seconds={report.latest_data_age_seconds}",
        f"data_health_status={report.data_health_status}",
        f"readiness_status={report.readiness_status}",
        f"policy_decision={report.policy_decision}",
        f"evidence_status={report.evidence_status}",
        f"cycles_attempted={report.cycles_attempted}",
        f"cycles_completed={report.cycles_completed}",
        f"paper_orders_created={report.paper_orders_created}",
        f"positions_updated={report.positions_updated}",
        f"positions_closed={report.positions_closed}",
        f"partial_exits_created={report.partial_exits_created}",
    ]
    for reason in report.blocking_reasons:
        lines.append(f"block={reason}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def symbols_from_args(symbols: list[str] | None, watchlist: str | None) -> list[str]:
    if symbols:
        return [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if watchlist:
        return get_watchlist(watchlist)
    return ["EUR/USD"]


def _operator_block_reasons(controls: dict[str, object]) -> list[str]:
    reasons = []
    if bool(controls.get("maintenance_mode")):
        reasons.append("operator maintenance mode is active")
    if bool(controls.get("degraded_mode")):
        reasons.append("operator degraded mode is active")
    return reasons


def _recovery_summary(plan: Any) -> str:
    status = getattr(getattr(plan, "final_status", None), "value", getattr(plan, "final_status", "UNKNOWN"))
    causes = len(getattr(plan, "causes", []) or [])
    actions = len(getattr(plan, "actions", []) or [])
    return f"{status}: causes={causes} actions={actions}"


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
