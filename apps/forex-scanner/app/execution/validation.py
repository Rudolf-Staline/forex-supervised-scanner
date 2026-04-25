"""Pre-live validation checks before creating executable paper intents."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.config.settings import AppSettings
from app.core.types import DirectionBias, Opportunity, OpportunityStatus, SessionName
from app.execution.models import ExecutionOrder
from app.risk.guardrails import PortfolioGuardrails


class PreTradeValidationResult(BaseModel):
    """Structured result for pre-live and paper-intent validation."""

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    portfolio_snapshot: dict[str, str | float | int] = Field(default_factory=dict)


class PreTradeValidator:
    """Run explicit checks required before creating an executable intent."""

    def __init__(self, settings: AppSettings, guardrails: PortfolioGuardrails | None = None) -> None:
        self.settings = settings
        self.guardrails = guardrails or PortfolioGuardrails(settings)

    def validate(
        self,
        opportunity: Opportunity,
        open_orders: list[ExecutionOrder],
        closed_orders: list[ExecutionOrder],
        *,
        now: datetime | None = None,
    ) -> PreTradeValidationResult:
        """Return validation reasons that block or permit execution intent creation."""

        config = self.settings.pre_live_validation
        snapshot = self.guardrails.snapshot(open_orders, closed_orders)
        if not config.enabled:
            return PreTradeValidationResult(allowed=True, portfolio_snapshot=snapshot)

        current_time = now or datetime.now(timezone.utc)
        reasons: list[str] = []
        if opportunity.status not in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}:
            reasons.append(f"status {opportunity.status.value} is not executable")
        if opportunity.direction not in {DirectionBias.LONG, DirectionBias.SHORT}:
            reasons.append(f"direction {opportunity.direction.value} is not executable")

        if config.require_complete_levels:
            missing = [
                field_name
                for field_name, value in {
                    "entry": opportunity.entry,
                    "stop_loss": opportunity.stop_loss,
                    "take_profit": opportunity.take_profit,
                }.items()
                if value is None
            ]
            if missing:
                reasons.append(f"missing executable levels: {', '.join(missing)}")

        if config.require_data_quality:
            data_quality_score = opportunity.data_quality.score if opportunity.data_quality else 100.0
            if data_quality_score < self.settings.portfolio_risk.min_data_quality_for_entry:
                reasons.append(
                    f"data quality {data_quality_score:.1f} below executable threshold "
                    f"{self.settings.portfolio_risk.min_data_quality_for_entry:.1f}"
                )

        if opportunity.timestamp is not None:
            signal_time = opportunity.timestamp if opportunity.timestamp.tzinfo else opportunity.timestamp.replace(tzinfo=timezone.utc)
            age_minutes = max(0.0, (current_time - signal_time).total_seconds() / 60.0)
            if age_minutes > config.max_signal_age_minutes:
                reasons.append(f"signal age {age_minutes:.1f} minutes exceeds {config.max_signal_age_minutes:.1f} minute limit")

        if config.block_invalidated_setups and opportunity.invalidation and opportunity.rejection_reason:
            reasons.append(f"setup invalidated: {opportunity.invalidation}")

        if not config.allow_off_hours and opportunity.session == SessionName.OFF_HOURS:
            reasons.append("off-hours session is not executable")

        if opportunity.spread is not None and opportunity.atr is not None and opportunity.atr > 0.0:
            spread_to_atr = opportunity.spread / opportunity.atr
            if spread_to_atr > self.settings.portfolio_risk.max_spread_to_atr_ratio:
                reasons.append(
                    f"spread/ATR {spread_to_atr:.3f} above executable threshold "
                    f"{self.settings.portfolio_risk.max_spread_to_atr_ratio:.3f}"
                )

        guardrail = self.guardrails.evaluate(opportunity, open_orders, closed_orders, now=current_time)
        reasons.extend(guardrail.reasons)
        return PreTradeValidationResult(
            allowed=not reasons,
            reasons=_dedupe(reasons),
            portfolio_snapshot=snapshot,
        )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
