"""Conservative autonomous supervisor for bounded paper/demo operation.

Autonomous Supervisor v0 is deliberately foreground-only and paper/demo-only.
It never enables live trading, never starts a daemon, and delegates executable
paper decisions exclusively to :class:`DemoBotService` after the central demo
bot safety lock has passed.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field, field_validator

from app.config.safety import DemoSafetyError, demo_safety_status, ensure_demo_bot_safe_mode
from app.config.settings import AppSettings, PROJECT_ROOT
from app.config.watchlists import get_watchlist
from app.core.types import TradingStyle
from app.data.providers import MarketDataProvider
from app.execution.demo_bot import DemoBotCycleResult, DemoBotService
from app.execution.autonomous_evidence import (
    AutonomousEvidenceConfig,
    AutonomousEvidenceFinalStatus,
    AutonomousEvidenceMode,
    AutonomousEvidenceReport,
    build_evidence,
)
from app.execution.autonomous_recovery import (
    AutonomousRecoveryConfig,
    AutonomousRecoveryPlan,
    build_recovery_plan,
    export_autonomous_recovery_json,
    export_autonomous_recovery_txt,
)
from app.execution.autonomous_readiness import (
    AutonomousReadinessConfig,
    AutonomousReadinessFinalStatus,
    AutonomousReadinessReport,
    build_readiness_report,
    export_autonomous_readiness_json,
    export_autonomous_readiness_txt,
)
from app.risk.daily_limits import DailyRiskSummary
from app.storage.database import Database

DEFAULT_AUTONOMOUS_SUPERVISOR_ENABLED = False
DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_CYCLES = 3
DEFAULT_AUTONOMOUS_SUPERVISOR_INTERVAL_SECONDS = 300
DEFAULT_AUTONOMOUS_SUPERVISOR_DRY_RUN = True
DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_CONSECUTIVE_FAILURES = 2
DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_ZERO_ORDER_CYCLES = 3
DEFAULT_AUTONOMOUS_SUPERVISOR_SYMBOLS = ["EUR/USD", "GBP/USD", "USD/CHF"]
DEFAULT_AUTONOMOUS_SUPERVISOR_REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_AUTONOMOUS_SUPERVISOR_JSON_REPORT = "autonomous_supervisor_summary.json"
DEFAULT_AUTONOMOUS_SUPERVISOR_TXT_REPORT = "autonomous_supervisor_report.txt"


class AutonomousSupervisorFinalStatus(StrEnum):
    """Operator-visible final statuses for a bounded autonomous run."""

    COMPLETED = "COMPLETED"
    STOPPED_BY_RISK = "STOPPED_BY_RISK"
    STOPPED_BY_OPERATOR_CONTROL = "STOPPED_BY_OPERATOR_CONTROL"
    STOPPED_BY_FAILURES = "STOPPED_BY_FAILURES"
    DRY_RUN = "DRY_RUN"
    BLOCKED_BY_SAFETY = "BLOCKED_BY_SAFETY"
    BLOCKED_BY_READINESS = "BLOCKED_BY_READINESS"


class AutonomousSupervisorCycleStatus(StrEnum):
    """Compact cycle-level statuses."""

    COMPLETED = "COMPLETED"
    DRY_RUN = "DRY_RUN"
    ZERO_ORDERS = "ZERO_ORDERS"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class AutonomousSupervisorConfig(BaseModel):
    """Conservative bounds for one foreground autonomous supervisor invocation."""

    enabled: bool = DEFAULT_AUTONOMOUS_SUPERVISOR_ENABLED
    style: TradingStyle = TradingStyle.DAY_TRADING
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_AUTONOMOUS_SUPERVISOR_SYMBOLS))
    watchlist: str | None = None
    max_cycles: int = Field(default=DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_CYCLES, ge=1, le=100)
    interval_seconds: float = Field(default=float(DEFAULT_AUTONOMOUS_SUPERVISOR_INTERVAL_SECONDS), ge=0.0, le=86_400.0)
    dry_run: bool = DEFAULT_AUTONOMOUS_SUPERVISOR_DRY_RUN
    max_consecutive_failures: int = Field(default=DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_CONSECUTIVE_FAILURES, ge=1, le=25)
    max_zero_order_cycles: int = Field(default=DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_ZERO_ORDER_CYCLES, ge=1, le=100)
    cooldown_seconds: float | None = Field(default=None, ge=0.0, le=86_400.0)
    reports_dir: Path = DEFAULT_AUTONOMOUS_SUPERVISOR_REPORTS_DIR
    export_json: bool = False
    export_txt: bool = False
    skip_readiness_gate: bool = False
    readiness_only: bool = False
    export_readiness_json: bool = False
    export_readiness_txt: bool = False
    build_evidence_first: bool = False
    evidence_mode: AutonomousEvidenceMode = AutonomousEvidenceMode.READ_ONLY
    plan_recovery_on_block: bool = False
    export_recovery_json: bool = False
    export_recovery_txt: bool = False

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        symbols = [symbol.strip().upper() for symbol in value if symbol.strip()]
        if not symbols:
            raise ValueError("at least one symbol is required")
        return symbols

    @field_validator("evidence_mode", mode="before")
    @classmethod
    def normalize_evidence_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().replace("-", "_")
        return value

    @classmethod
    def from_environment(cls, **overrides: object) -> "AutonomousSupervisorConfig":
        """Build config from conservative AUTONOMOUS_SUPERVISOR_* defaults plus overrides."""

        values: dict[str, object] = {
            "enabled": _env_bool("AUTONOMOUS_SUPERVISOR_ENABLED", DEFAULT_AUTONOMOUS_SUPERVISOR_ENABLED),
            "max_cycles": _env_int("AUTONOMOUS_SUPERVISOR_MAX_CYCLES", DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_CYCLES),
            "interval_seconds": _env_float("AUTONOMOUS_SUPERVISOR_INTERVAL_SECONDS", DEFAULT_AUTONOMOUS_SUPERVISOR_INTERVAL_SECONDS),
            "dry_run": _env_bool("AUTONOMOUS_SUPERVISOR_DRY_RUN", DEFAULT_AUTONOMOUS_SUPERVISOR_DRY_RUN),
            "max_consecutive_failures": _env_int(
                "AUTONOMOUS_SUPERVISOR_MAX_CONSECUTIVE_FAILURES",
                DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_CONSECUTIVE_FAILURES,
            ),
            "max_zero_order_cycles": _env_int(
                "AUTONOMOUS_SUPERVISOR_MAX_ZERO_ORDER_CYCLES",
                DEFAULT_AUTONOMOUS_SUPERVISOR_MAX_ZERO_ORDER_CYCLES,
            ),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls.model_validate(values)

    @property
    def effective_cooldown_seconds(self) -> float:
        """Cooldown applied after blocked, rejected, or zero-paper-order cycles."""

        return self.interval_seconds if self.cooldown_seconds is None else self.cooldown_seconds

    def resolved_symbols(self) -> list[str]:
        """Return watchlist symbols when selected, otherwise explicit symbols."""

        if self.watchlist:
            return [symbol.strip().upper() for symbol in get_watchlist(self.watchlist)]
        return list(self.symbols)


class AutonomousSupervisorState(BaseModel):
    """Mutable counters for one bounded supervisor run."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cycles_attempted: int = 0
    consecutive_failures: int = 0
    consecutive_zero_order_cycles: int = 0
    stop_requested: bool = False
    stop_reason: str | None = None


class AutonomousSupervisorCycleRecord(BaseModel):
    """Audit-friendly record for one attempted autonomous cycle."""

    cycle_number: int
    started_at: datetime
    completed_at: datetime
    status: AutonomousSupervisorCycleStatus
    style: TradingStyle
    symbols: list[str]
    watchlist: str | None = None
    dry_run: bool
    opportunities: int = 0
    paper_orders_created: int = 0
    accepted_signals: int = 0
    rejected_signals: int = 0
    risk_summary: dict[str, object] = Field(default_factory=dict)
    safety_flags: dict[str, object] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    error: str | None = None


class AutonomousSupervisorRunResult(BaseModel):
    """Final result returned by run_once/run_loop and used for exports."""

    run_id: str
    started_at: datetime
    completed_at: datetime
    cycle_count: int
    style: TradingStyle
    symbols: list[str]
    watchlist: str | None = None
    dry_run: bool
    final_status: AutonomousSupervisorFinalStatus
    stop_reason: str | None = None
    orders_created: int = 0
    risk_summaries: list[dict[str, object]] = Field(default_factory=list)
    cycles: list[AutonomousSupervisorCycleRecord] = Field(default_factory=list)
    safety_flags: dict[str, object] = Field(default_factory=dict)
    export_paths: list[str] = Field(default_factory=list)
    readiness_report: AutonomousReadinessReport | None = None
    evidence_report: AutonomousEvidenceReport | None = None
    recovery_plan: AutonomousRecoveryPlan | None = None

    @property
    def paper_orders_created(self) -> int:
        """Backward-compatible alias for callers from early v0 prototypes."""

        return self.orders_created


class AutonomousSupervisorService:
    """Run conservative, bounded paper/demo supervisor cycles in the foreground."""

    def __init__(
        self,
        settings: AppSettings,
        provider: MarketDataProvider,
        database: Database,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database
        self.sleep_fn = sleep_fn

    def run_once(self, config: AutonomousSupervisorConfig | None = None) -> AutonomousSupervisorRunResult:
        """Run a single foreground cycle or a single dry-run safety validation."""

        selected = config or AutonomousSupervisorConfig.from_environment(max_cycles=1)
        selected = selected.model_copy(update={"max_cycles": 1})
        return self.run_loop(selected)

    def run_loop(self, config: AutonomousSupervisorConfig | None = None) -> AutonomousSupervisorRunResult:
        """Run bounded cycles, enforcing safety, cooldowns, and stop conditions."""

        selected = config or AutonomousSupervisorConfig.from_environment()
        state = AutonomousSupervisorState()
        symbols = selected.resolved_symbols()
        records: list[AutonomousSupervisorCycleRecord] = []
        final_status = AutonomousSupervisorFinalStatus.COMPLETED
        stop_reason: str | None = None

        readiness_report: AutonomousReadinessReport | None = None
        evidence_report: AutonomousEvidenceReport | None = None
        recovery_plan: AutonomousRecoveryPlan | None = None
        if selected.build_evidence_first:
            evidence_report = build_evidence(
                settings=self.settings,
                database=self.database,
                config=AutonomousEvidenceConfig(
                    reports_dir=selected.reports_dir,
                    mode=selected.evidence_mode,
                    watchlist=selected.watchlist or "multi_asset_demo",
                    symbols=symbols,
                    include_readiness=False,
                    export_json=selected.export_json or selected.export_readiness_json,
                    export_txt=selected.export_txt or selected.export_readiness_txt,
                    fail_fast=True,
                ),
            )
            if evidence_report.final_status == AutonomousEvidenceFinalStatus.BLOCKED_EVIDENCE and not selected.dry_run:
                final_status = AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS
                stop_reason = "; ".join(evidence_report.blocking_failures) or "evidence builder reported blocking failures"
                recovery_plan = self._maybe_build_recovery_plan(selected)
                result = self._build_result(selected, state, records, final_status, stop_reason, symbols, readiness_report, evidence_report, recovery_plan)
                result.export_paths = [str(path) for path in self._export_result_if_requested(selected, result)]
                return result
        if selected.skip_readiness_gate:
            if not selected.dry_run:
                final_status = AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS
                stop_reason = "--skip-readiness-gate is diagnostic-only and is allowed only with dry_run=true"
                recovery_plan = self._maybe_build_recovery_plan(selected)
                result = self._build_result(selected, state, records, final_status, stop_reason, symbols, readiness_report, evidence_report, recovery_plan)
                result.export_paths = [str(path) for path in self._export_result_if_requested(selected, result)]
                return result
        else:
            readiness_report = build_readiness_report(
                self.settings,
                self.database,
                AutonomousReadinessConfig.from_environment(reports_dir=selected.reports_dir, dry_run=selected.dry_run or not selected.enabled),
            )
            if selected.readiness_only:
                final_status = AutonomousSupervisorFinalStatus.DRY_RUN if readiness_report.dry_run_allowed else AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS
                stop_reason = f"readiness-only check completed with {readiness_report.final_status.value}"
                recovery_plan = self._maybe_build_recovery_plan(selected)
                result = self._build_result(selected, state, records, final_status, stop_reason, symbols, readiness_report, evidence_report, recovery_plan)
                result.export_paths = [str(path) for path in self._export_result_if_requested(selected, result)]
                return result
            if readiness_report.final_status == AutonomousReadinessFinalStatus.WARN_READY and selected.dry_run and readiness_report.dry_run_allowed:
                pass
            elif not readiness_report.paper_run_allowed:
                final_status = AutonomousSupervisorFinalStatus.BLOCKED_BY_READINESS
                stop_reason = "; ".join(readiness_report.blocking_reasons or readiness_report.warning_reasons) or readiness_report.final_status.value
                recovery_plan = self._maybe_build_recovery_plan(selected)
                result = self._build_result(selected, state, records, final_status, stop_reason, symbols, readiness_report, evidence_report, recovery_plan)
                result.export_paths = [str(path) for path in self._export_result_if_requested(selected, result)]
                return result

        if not selected.enabled:
            final_status = AutonomousSupervisorFinalStatus.DRY_RUN
            stop_reason = "AUTONOMOUS_SUPERVISOR_ENABLED is false; paper/demo dry-run validation only"
            record = self._dry_run_record(selected, symbols, 1, [stop_reason])
            records.append(record)
            result = self._build_result(selected, state, records, final_status, stop_reason, symbols, readiness_report, evidence_report)
            result.export_paths = [str(path) for path in self._export_result_if_requested(selected, result)]
            return result

        for cycle_number in range(1, selected.max_cycles + 1):
            controls = self.database.load_operator_controls()
            if controls.maintenance_mode or controls.degraded_mode:
                final_status = AutonomousSupervisorFinalStatus.STOPPED_BY_OPERATOR_CONTROL
                stop_reason = _operator_stop_reason(controls.maintenance_mode, controls.degraded_mode)
                break

            record = self._run_cycle(selected, symbols, cycle_number)
            records.append(record)
            state.cycles_attempted += 1

            if record.status == AutonomousSupervisorCycleStatus.FAILED:
                state.consecutive_failures += 1
            else:
                state.consecutive_failures = 0

            if record.status == AutonomousSupervisorCycleStatus.BLOCKED and any("safety" in reason.lower() for reason in record.reasons):
                final_status = AutonomousSupervisorFinalStatus.BLOCKED_BY_SAFETY
                stop_reason = "; ".join(record.reasons) or "demo safety lock blocked autonomous supervisor"
                break

            if record.status in {
                AutonomousSupervisorCycleStatus.ZERO_ORDERS,
                AutonomousSupervisorCycleStatus.REJECTED,
                AutonomousSupervisorCycleStatus.BLOCKED,
            }:
                state.consecutive_zero_order_cycles += 1
                self._cooldown(selected)
            else:
                state.consecutive_zero_order_cycles = 0

            if state.consecutive_failures >= selected.max_consecutive_failures:
                final_status = AutonomousSupervisorFinalStatus.STOPPED_BY_FAILURES
                stop_reason = f"stopped after {state.consecutive_failures} consecutive cycle failures"
                break

            if state.consecutive_zero_order_cycles >= selected.max_zero_order_cycles:
                final_status = AutonomousSupervisorFinalStatus.STOPPED_BY_RISK
                stop_reason = f"stopped after {state.consecutive_zero_order_cycles} consecutive zero-paper-order cycles"
                break

            if cycle_number < selected.max_cycles:
                self._sleep(selected.interval_seconds)

        if stop_reason is None:
            if selected.dry_run:
                final_status = AutonomousSupervisorFinalStatus.DRY_RUN
                stop_reason = f"completed bounded max_cycles={selected.max_cycles} dry-run validation"
            else:
                stop_reason = f"completed bounded max_cycles={selected.max_cycles} paper/demo run"
        result = self._build_result(selected, state, records, final_status, stop_reason, symbols, readiness_report, evidence_report)
        result.export_paths = [str(path) for path in self._export_result_if_requested(selected, result)]
        return result

    def _run_cycle(
        self,
        config: AutonomousSupervisorConfig,
        symbols: list[str],
        cycle_number: int,
    ) -> AutonomousSupervisorCycleRecord:
        started = datetime.now(timezone.utc)
        try:
            ensure_demo_bot_safe_mode(self.settings, context="autonomous supervisor cycle")
            if config.dry_run:
                return self._dry_run_record(config, symbols, cycle_number, ["dry_run=true; DemoBotService paper order creation skipped"])
            cycle_result = DemoBotService(self.settings, self.provider, self.database).run_cycle(
                config.style,
                symbols,
                watchlist=config.watchlist,
            )
            return _record_from_demo_result(cycle_number, config, cycle_result)
        except DemoSafetyError as exc:
            return AutonomousSupervisorCycleRecord(
                cycle_number=cycle_number,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                status=AutonomousSupervisorCycleStatus.BLOCKED,
                style=config.style,
                symbols=symbols,
                watchlist=config.watchlist,
                dry_run=config.dry_run,
                safety_flags=_safety_flags(self.settings),
                reasons=[str(exc)],
            )
        except Exception as exc:  # pragma: no cover - exact failures are tested with a fake service.
            return AutonomousSupervisorCycleRecord(
                cycle_number=cycle_number,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                status=AutonomousSupervisorCycleStatus.FAILED,
                style=config.style,
                symbols=symbols,
                watchlist=config.watchlist,
                dry_run=config.dry_run,
                safety_flags=_safety_flags(self.settings),
                reasons=["cycle execution failed"],
                error=f"{type(exc).__name__}: {exc}",
            )

    def _dry_run_record(
        self,
        config: AutonomousSupervisorConfig,
        symbols: list[str],
        cycle_number: int,
        reasons: list[str],
    ) -> AutonomousSupervisorCycleRecord:
        started = datetime.now(timezone.utc)
        try:
            ensure_demo_bot_safe_mode(self.settings, context="autonomous supervisor dry-run cycle")
        except DemoSafetyError as exc:
            return AutonomousSupervisorCycleRecord(
                cycle_number=cycle_number,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                status=AutonomousSupervisorCycleStatus.BLOCKED,
                style=config.style,
                symbols=symbols,
                watchlist=config.watchlist,
                dry_run=True,
                safety_flags=_safety_flags(self.settings),
                reasons=[str(exc)],
            )
        return AutonomousSupervisorCycleRecord(
            cycle_number=cycle_number,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            status=AutonomousSupervisorCycleStatus.DRY_RUN,
            style=config.style,
            symbols=symbols,
            watchlist=config.watchlist,
            dry_run=True,
            safety_flags=_safety_flags(self.settings),
            reasons=reasons,
        )

    def _build_result(
        self,
        config: AutonomousSupervisorConfig,
        state: AutonomousSupervisorState,
        records: list[AutonomousSupervisorCycleRecord],
        final_status: AutonomousSupervisorFinalStatus,
        stop_reason: str | None,
        symbols: list[str],
        readiness_report: AutonomousReadinessReport | None = None,
        evidence_report: AutonomousEvidenceReport | None = None,
        recovery_plan: AutonomousRecoveryPlan | None = None,
    ) -> AutonomousSupervisorRunResult:
        completed_at = datetime.now(timezone.utc)
        return AutonomousSupervisorRunResult(
            run_id=state.run_id,
            started_at=state.started_at,
            completed_at=completed_at,
            cycle_count=len(records),
            style=config.style,
            symbols=symbols,
            watchlist=config.watchlist,
            dry_run=config.dry_run or not config.enabled,
            final_status=final_status,
            stop_reason=stop_reason,
            orders_created=sum(record.paper_orders_created for record in records),
            risk_summaries=[record.risk_summary for record in records if record.risk_summary],
            cycles=records,
            safety_flags=_safety_flags(self.settings),
            readiness_report=readiness_report,
            evidence_report=evidence_report,
            recovery_plan=recovery_plan,
        )

    def _export_result_if_requested(self, config: AutonomousSupervisorConfig, result: AutonomousSupervisorRunResult) -> list[Path]:
        paths: list[Path] = []
        if result.evidence_report is not None:
            paths.extend(Path(path) for path in result.evidence_report.output_paths)
        if config.export_json:
            paths.append(export_autonomous_supervisor_json(result, config.reports_dir))
        if config.export_txt:
            paths.append(export_autonomous_supervisor_txt(result, config.reports_dir))
        if result.readiness_report is not None and config.export_readiness_json:
            paths.append(export_autonomous_readiness_json(result.readiness_report, config.reports_dir))
        if result.readiness_report is not None and config.export_readiness_txt:
            paths.append(export_autonomous_readiness_txt(result.readiness_report, config.reports_dir))
        if result.recovery_plan is not None and config.export_recovery_json:
            paths.append(export_autonomous_recovery_json(result.recovery_plan, config.reports_dir))
        if result.recovery_plan is not None and config.export_recovery_txt:
            paths.append(export_autonomous_recovery_txt(result.recovery_plan, config.reports_dir))
        return paths

    def _maybe_build_recovery_plan(self, config: AutonomousSupervisorConfig) -> AutonomousRecoveryPlan | None:
        if not config.plan_recovery_on_block:
            return None
        return build_recovery_plan(AutonomousRecoveryConfig(reports_dir=config.reports_dir))

    def _sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.sleep_fn(seconds)

    def _cooldown(self, config: AutonomousSupervisorConfig) -> None:
        self._sleep(config.effective_cooldown_seconds)


def export_autonomous_supervisor_json(result: AutonomousSupervisorRunResult, reports_dir: Path) -> Path:
    """Write the JSON run summary using the required stable file name."""

    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_SUPERVISOR_JSON_REPORT
    path.write_text(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def export_autonomous_supervisor_txt(result: AutonomousSupervisorRunResult, reports_dir: Path) -> Path:
    """Write the text run summary using the required stable file name."""

    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / DEFAULT_AUTONOMOUS_SUPERVISOR_TXT_REPORT
    path.write_text(_format_txt_report(result), encoding="utf-8")
    return path


def _record_from_demo_result(
    cycle_number: int,
    config: AutonomousSupervisorConfig,
    result: DemoBotCycleResult,
) -> AutonomousSupervisorCycleRecord:
    accepted = sum(1 for decision in result.decisions if decision.accepted)
    rejected = len(result.decisions) - accepted
    reasons = [reason for decision in result.decisions for reason in decision.reasons]
    if result.orders_created > 0:
        status = AutonomousSupervisorCycleStatus.COMPLETED
    elif result.decisions and accepted == 0:
        status = AutonomousSupervisorCycleStatus.REJECTED
    else:
        status = AutonomousSupervisorCycleStatus.ZERO_ORDERS
    return AutonomousSupervisorCycleRecord(
        cycle_number=cycle_number,
        started_at=result.started_at,
        completed_at=result.completed_at,
        status=status,
        style=result.style,
        symbols=result.symbols,
        watchlist=config.watchlist,
        dry_run=False,
        opportunities=result.opportunities,
        paper_orders_created=result.orders_created,
        accepted_signals=accepted,
        rejected_signals=rejected,
        risk_summary=_risk_summary_dict(result.risk_summary),
        safety_flags=_safety_flags_for_completed_cycle(),
        reasons=reasons,
    )


def _risk_summary_dict(summary: DailyRiskSummary | BaseModel | object) -> dict[str, object]:
    if isinstance(summary, BaseModel):
        return summary.model_dump(mode="json")
    if hasattr(summary, "__dict__"):
        return dict(vars(summary))
    return {}


def _safety_flags(settings: AppSettings) -> dict[str, object]:
    flags = _safety_flags_for_completed_cycle()
    flags["safety_environment"] = demo_safety_status(settings)
    return flags


def _safety_flags_for_completed_cycle() -> dict[str, object]:
    return {
        "paper_demo_only": True,
        "live_execution_allowed": False,
        "broker_live_execution_allowed": False,
        "broker_order_submission_allowed": False,
        "broker_mode_required": "paper",
        "hidden_daemon_created": False,
        "infinite_loop_default": False,
    }


def _operator_stop_reason(maintenance_mode: bool, degraded_mode: bool) -> str:
    active = []
    if maintenance_mode:
        active.append("maintenance_mode")
    if degraded_mode:
        active.append("degraded_mode")
    return "operator control active: " + ", ".join(active)


def _format_txt_report(result: AutonomousSupervisorRunResult) -> str:
    lines = [
        "Autonomous Supervisor v0 paper/demo report",
        "This report does not authorize live trading or broker execution.",
        "",
        f"started_at: {result.started_at.isoformat()}",
        f"completed_at: {result.completed_at.isoformat()}",
        f"cycle_count: {result.cycle_count}",
        f"style: {result.style.value}",
        f"symbols: {', '.join(result.symbols)}",
        f"watchlist: {result.watchlist or '-'}",
        f"dry_run: {str(result.dry_run).lower()}",
        f"final_status: {result.final_status.value}",
        f"stop_reason: {result.stop_reason or '-'}",
        f"orders_created: {result.orders_created}",
        "",
        "Safety flags proving live execution was not allowed:",
    ]
    for key, value in result.safety_flags.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "Risk summaries:"])
    if not result.risk_summaries:
        lines.append("- none")
    for index, summary in enumerate(result.risk_summaries, start=1):
        lines.append(f"- cycle {index}: {summary}")
    if result.recovery_plan is not None:
        lines.extend(["", "Recovery plan summary:"])
        lines.append(f"- final_status: {result.recovery_plan.final_status.value}")
        lines.append(f"- causes: {len(result.recovery_plan.causes)}")
        lines.append(f"- actions: {len(result.recovery_plan.actions)}")
        lines.append(f"- next_recommended_command: {result.recovery_plan.next_recommended_command or '-'}")
    lines.extend(["", "Cycles:"])
    if not result.cycles:
        lines.append("- none")
    for cycle in result.cycles:
        lines.append(
            f"- cycle {cycle.cycle_number}: status={cycle.status.value} opportunities={cycle.opportunities} "
            f"paper_orders_created={cycle.paper_orders_created} reasons={'; '.join(cycle.reasons) or '-'}"
        )
    return "\n".join(lines) + "\n"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return float(default)
    return float(raw)
