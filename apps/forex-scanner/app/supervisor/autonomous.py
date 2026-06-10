"""Autonomous Supervisor v0 for strictly paper/demo operation.

The supervisor is intentionally foreground-only: callers must invoke it
explicitly, it runs a bounded number of cycles, and it never starts a daemon or
subprocess. It delegates signal handling to the existing paper demo bot and
keeps broker/live execution out of scope.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.settings import AppSettings
from app.config.watchlists import get_watchlist
from app.core.types import TradingStyle
from app.data.providers import MarketDataProvider
from app.execution.demo_bot import DemoBotCycleResult, DemoBotService
from app.ops.daily_checklist import DailyChecklistOptions, build_daily_checklist
from app.safety.env_doctor import SafetyStatus, evaluate_environment
from app.storage.database import Database

DEFAULT_SUPERVISOR_SYMBOLS = ["EUR/USD", "GBP/USD", "USD/CHF"]


class AutonomousSupervisorConfig(BaseModel):
    """Operator-supplied bounds for a foreground supervisor run."""

    style: TradingStyle = TradingStyle.DAY_TRADING
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_SUPERVISOR_SYMBOLS))
    watchlist: str | None = None
    cycles: int = Field(default=1, ge=1, le=25)
    interval_seconds: int = Field(default=0, ge=0, le=86_400)
    sleep_between_cycles: bool = True
    export_reports: bool = True
    reports_dir: Path = Path("reports")
    run_label: str = "autonomous-supervisor-v0"

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        normalized = [symbol.strip().upper() for symbol in value if symbol.strip()]
        if not normalized:
            raise ValueError("at least one symbol is required")
        return normalized

    def resolved_symbols(self) -> list[str]:
        """Return watchlist symbols when selected, otherwise explicit symbols."""

        if self.watchlist:
            return [symbol.strip().upper() for symbol in get_watchlist(self.watchlist)]
        return list(self.symbols)


class AutonomousSupervisorCycle(BaseModel):
    """Compact persisted summary for one paper/demo bot cycle."""

    cycle_number: int
    cycle_id: str | None = None
    started_at: datetime
    completed_at: datetime
    status: str
    symbols: list[str]
    opportunities: int = 0
    orders_created: int = 0
    accepted_signals: int = 0
    rejected_signals: int = 0
    reasons: list[str] = Field(default_factory=list)


class AutonomousSupervisorRunResult(BaseModel):
    """Audit-friendly summary of a bounded supervisor invocation."""

    run_id: str
    run_label: str
    started_at: datetime
    completed_at: datetime
    status: str
    style: TradingStyle
    symbols: list[str]
    requested_cycles: int
    completed_cycles: int
    total_opportunities: int
    total_paper_orders_created: int
    cycles: list[AutonomousSupervisorCycle]
    safety_status: str
    blocking_reasons: list[str] = Field(default_factory=list)
    checklist: dict[str, object] = Field(default_factory=dict)
    report_paths: list[str] = Field(default_factory=list)
    paper_demo_only: bool = True
    live_trading_enabled: bool = False
    broker_mode: str = "paper"
    mt5_called: bool = False
    broker_orders_sent: bool = False
    hidden_daemon_created: bool = False
    subprocess_used: bool = False


class AutonomousSupervisorService:
    """Run bounded paper/demo supervision cycles in the foreground."""

    def __init__(self, settings: AppSettings, provider: MarketDataProvider, database: Database) -> None:
        self.settings = settings
        self.provider = provider
        self.database = database

    def run(self, config: AutonomousSupervisorConfig) -> AutonomousSupervisorRunResult:
        """Validate safety, run bounded paper cycles, and optionally export reports."""

        started = datetime.now(timezone.utc)
        run_id = str(uuid.uuid4())
        symbols = config.resolved_symbols()
        checklist = build_daily_checklist(DailyChecklistOptions(mode="paper"))
        blocking_reasons = self._safety_blocking_reasons()
        if blocking_reasons:
            result = AutonomousSupervisorRunResult(
                run_id=run_id,
                run_label=config.run_label,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                status="BLOCKED",
                style=config.style,
                symbols=symbols,
                requested_cycles=config.cycles,
                completed_cycles=0,
                total_opportunities=0,
                total_paper_orders_created=0,
                cycles=[],
                safety_status="BLOCKED",
                blocking_reasons=blocking_reasons,
                checklist=checklist,
            )
            if config.export_reports:
                result.report_paths = [
                    str(path)
                    for path in export_autonomous_supervisor_reports(result, config.reports_dir)
                ]
            return result

        bot = DemoBotService(self.settings, self.provider, self.database)
        cycle_summaries: list[AutonomousSupervisorCycle] = []
        for index in range(config.cycles):
            cycle_result = bot.run_cycle(config.style, symbols, watchlist=config.watchlist)
            cycle_summaries.append(_cycle_summary(index + 1, cycle_result))
            if index < config.cycles - 1 and config.sleep_between_cycles and config.interval_seconds > 0:
                time.sleep(config.interval_seconds)

        total_opportunities = sum(cycle.opportunities for cycle in cycle_summaries)
        total_orders = sum(cycle.orders_created for cycle in cycle_summaries)
        result = AutonomousSupervisorRunResult(
            run_id=run_id,
            run_label=config.run_label,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            status="PAPER_DEMO_COMPLETED",
            style=config.style,
            symbols=symbols,
            requested_cycles=config.cycles,
            completed_cycles=len(cycle_summaries),
            total_opportunities=total_opportunities,
            total_paper_orders_created=total_orders,
            cycles=cycle_summaries,
            safety_status=SafetyStatus.SAFE_PAPER.value,
            checklist=checklist,
        )
        if config.export_reports:
            result.report_paths = [
                str(path) for path in export_autonomous_supervisor_reports(result, config.reports_dir)
            ]
        return result

    def _safety_blocking_reasons(self) -> list[str]:
        reasons: list[str] = []
        try:
            ensure_demo_safe_mode(self.settings, context="autonomous supervisor v0")
        except DemoSafetyError as exc:
            reasons.append(str(exc))
        safety_report = evaluate_environment("paper")
        if safety_report.status != SafetyStatus.SAFE_PAPER:
            reasons.append(f"safety environment status is {safety_report.status.value}")
        if self.settings.execution.mode != "paper":
            reasons.append(f"execution.mode must be paper, got {self.settings.execution.mode}")
        if (
            self.settings.broker.live_enabled
            or self.settings.execution_capabilities.broker_live_enabled
        ):
            reasons.append("live broker capability must remain disabled")
        return reasons


def export_autonomous_supervisor_reports(result: AutonomousSupervisorRunResult, reports_dir: Path) -> list[Path]:
    """Write JSON and Markdown summaries for the most recent supervisor run."""

    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "autonomous_supervisor_last_run.json"
    md_path = reports_dir / "autonomous_supervisor_last_run.md"
    payload = result.model_dump(mode="json")
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_result_markdown(result), encoding="utf-8")
    return [json_path, md_path]


def _cycle_summary(cycle_number: int, result: DemoBotCycleResult) -> AutonomousSupervisorCycle:
    accepted = sum(1 for decision in result.decisions if decision.accepted)
    rejected = len(result.decisions) - accepted
    reasons: list[str] = []
    for decision in result.decisions:
        for reason in decision.reasons:
            if reason not in reasons:
                reasons.append(reason)
    return AutonomousSupervisorCycle(
        cycle_number=cycle_number,
        cycle_id=result.cycle_id,
        started_at=result.started_at,
        completed_at=result.completed_at,
        status="completed",
        symbols=result.symbols,
        opportunities=result.opportunities,
        orders_created=result.orders_created,
        accepted_signals=accepted,
        rejected_signals=rejected,
        reasons=reasons,
    )


def _result_markdown(result: AutonomousSupervisorRunResult) -> str:
    lines = [
        "# Autonomous Supervisor v0 Last Run",
        "",
        "Paper/demo foreground supervisor report. This report does not authorize live trading or broker order execution.",
        "",
        f"- run_id: `{result.run_id}`",
        f"- status: `{result.status}`",
        f"- safety_status: `{result.safety_status}`",
        f"- style: `{result.style.value}`",
        f"- symbols: `{', '.join(result.symbols)}`",
        f"- completed_cycles: `{result.completed_cycles}/{result.requested_cycles}`",
        f"- total_opportunities: `{result.total_opportunities}`",
        f"- total_paper_orders_created: `{result.total_paper_orders_created}`",
        "",
        "## Safety Assertions",
        f"- paper_demo_only: `{str(result.paper_demo_only).lower()}`",
        f"- live_trading_enabled: `{str(result.live_trading_enabled).lower()}`",
        f"- broker_mode: `{result.broker_mode}`",
        f"- mt5_called: `{str(result.mt5_called).lower()}`",
        f"- broker_orders_sent: `{str(result.broker_orders_sent).lower()}`",
        f"- hidden_daemon_created: `{str(result.hidden_daemon_created).lower()}`",
        f"- subprocess_used: `{str(result.subprocess_used).lower()}`",
        "",
        "## Cycles",
    ]
    if not result.cycles:
        lines.append("- No cycles completed.")
    for cycle in result.cycles:
        lines.append(
            f"- cycle {cycle.cycle_number}: status=`{cycle.status}`, "
            f"opportunities=`{cycle.opportunities}`, "
            f"paper_orders_created=`{cycle.orders_created}`, "
            f"accepted=`{cycle.accepted_signals}`, rejected=`{cycle.rejected_signals}`"
        )
    if result.blocking_reasons:
        lines.extend(["", "## Blocking Reasons"])
        lines.extend(f"- {reason}" for reason in result.blocking_reasons)
    return "\n".join(lines) + "\n"
