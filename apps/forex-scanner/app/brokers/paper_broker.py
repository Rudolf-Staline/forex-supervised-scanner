"""Realistic paper-broker fill simulation.

This module is paper-only. It never talks to MT5 or any external broker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core.types import DirectionBias
from app.execution.models import ExecutionOrder, OrderRequest

PAPER_SLIPPAGE_MODE_ENV = "PAPER_SLIPPAGE_MODE"
PAPER_COMMISSION_MODE_ENV = "PAPER_COMMISSION_MODE"
PAPER_FILL_MODE_ENV = "PAPER_FILL_MODE"


@dataclass(frozen=True)
class PaperBrokerConfig:
    """Paper fill simulation knobs loaded from environment defaults."""

    slippage_mode: str = "conservative"
    commission_mode: str = "estimated"
    fill_mode: str = "realistic"
    max_spread_atr: float = 0.50
    min_volume: float = 0.01
    max_volume: float = 100.0
    min_stop_spread_multiple: float = 2.0

    @classmethod
    def from_env(cls) -> "PaperBrokerConfig":
        """Load paper-broker simulation modes from environment variables."""

        return cls(
            slippage_mode=os.getenv(PAPER_SLIPPAGE_MODE_ENV, "conservative").strip().lower() or "conservative",
            commission_mode=os.getenv(PAPER_COMMISSION_MODE_ENV, "estimated").strip().lower() or "estimated",
            fill_mode=os.getenv(PAPER_FILL_MODE_ENV, "realistic").strip().lower() or "realistic",
            max_spread_atr=_env_float("PAPER_MAX_SPREAD_ATR", 0.50),
            min_volume=_env_float("PAPER_MIN_VOLUME", 0.01),
            max_volume=_env_float("PAPER_MAX_VOLUME", 100.0),
            min_stop_spread_multiple=_env_float("PAPER_MIN_STOP_SPREAD_MULTIPLE", 2.0),
        )


@dataclass(frozen=True)
class PaperFillSimulation:
    """Result of a realistic paper fill pre-check."""

    accepted: bool
    fill_status: str
    reasons: list[str] = field(default_factory=list)
    requested_entry: float | None = None
    filled_entry: float | None = None
    slippage_points: float = 0.0
    spread_cost: float = 0.0
    commission_estimate: float = 0.0
    final_risk_reward: float | None = None
    partial_fill_ratio: float = 1.0

    def assumptions(self) -> dict[str, str | float | bool]:
        """Return serializable execution assumptions for persisted paper orders."""

        return {
            "paper_fill_status": self.fill_status,
            "paper_requested_entry": self.requested_entry or 0.0,
            "paper_filled_entry": self.filled_entry or 0.0,
            "paper_slippage_points": self.slippage_points,
            "paper_spread_cost": self.spread_cost,
            "paper_commission_estimate": self.commission_estimate,
            "paper_final_risk_reward": self.final_risk_reward or 0.0,
            "paper_partial_fill_ratio": self.partial_fill_ratio,
            "paper_realistic_fill": True,
        }


class RealisticPaperBroker:
    """Paper-only broker simulator for local forward tests and demos."""

    def __init__(self, config: PaperBrokerConfig | None = None) -> None:
        self.config = config or PaperBrokerConfig.from_env()

    def simulate_request(self, request: OrderRequest) -> PaperFillSimulation:
        """Validate and estimate a paper fill for one order intent."""

        reasons: list[str] = []
        spread = float(request.spread_at_signal or 0.0)
        atr = float(request.atr_at_signal or 0.0)
        spread_atr = None if atr <= 0.0 else spread / atr
        stop_distance = abs(request.entry_price - request.stop_loss)

        if request.quantity_units < self.config.min_volume or request.quantity_units > self.config.max_volume:
            reasons.append(
                f"paper fill rejected: volume {request.quantity_units:.4f} outside "
                f"{self.config.min_volume:.4f}-{self.config.max_volume:.4f}"
            )
        if (request.session or "").lower() == "off_hours":
            reasons.append("paper fill rejected: session is not tradable")
        if spread_atr is not None and spread_atr > self.config.max_spread_atr:
            reasons.append(f"paper fill rejected: spread/ATR {spread_atr:.3f} above {self.config.max_spread_atr:.3f}")
        if spread > 0.0 and stop_distance <= spread * self.config.min_stop_spread_multiple:
            reasons.append("paper fill rejected: stop_loss too close to current spread")

        slippage = _slippage_points(spread, request, self.config)
        spread_cost = spread / 2.0 if spread > 0.0 else 0.0
        filled_entry = _filled_entry(request, slippage + spread_cost)
        commission = _commission_estimate(request, self.config)
        final_rr = _final_risk_reward(request, filled_entry, commission)
        partial_ratio = _partial_fill_ratio(request, self.config)
        if reasons:
            return PaperFillSimulation(
                accepted=False,
                fill_status="rejected",
                reasons=reasons,
                requested_entry=request.entry_price,
                filled_entry=None,
                slippage_points=slippage,
                spread_cost=spread_cost,
                commission_estimate=commission,
                final_risk_reward=final_rr,
                partial_fill_ratio=0.0,
            )
        fill_status = "partial_fill" if partial_ratio < 1.0 else "filled"
        return PaperFillSimulation(
            accepted=True,
            fill_status=fill_status,
            requested_entry=request.entry_price,
            filled_entry=filled_entry,
            slippage_points=slippage,
            spread_cost=spread_cost,
            commission_estimate=commission,
            final_risk_reward=final_rr,
            partial_fill_ratio=partial_ratio,
        )

    def decorate_order(self, order: ExecutionOrder, simulation: PaperFillSimulation) -> ExecutionOrder:
        """Attach paper fill details to a persisted execution order."""

        assumptions = {**order.execution_assumptions, **simulation.assumptions()}
        updates: dict[str, object] = {
            "execution_assumptions": assumptions,
            "estimated_slippage": simulation.slippage_points,
            "spread_adjustment": simulation.spread_cost,
        }
        if simulation.filled_entry is not None:
            updates["simulated_entry"] = simulation.filled_entry
        if simulation.partial_fill_ratio < 1.0:
            updates["filled_quantity"] = order.request.quantity_units * simulation.partial_fill_ratio
        return order.model_copy(update=updates)


def _slippage_points(spread: float, request: OrderRequest, config: PaperBrokerConfig) -> float:
    if config.slippage_mode == "none":
        return 0.0
    if spread > 0.0:
        multiplier = 0.50 if config.slippage_mode == "conservative" else 0.25
        return spread * multiplier
    return request.entry_price * (0.00003 if config.slippage_mode == "conservative" else 0.00001)


def _filled_entry(request: OrderRequest, cost: float) -> float:
    if request.direction == DirectionBias.LONG:
        return request.entry_price + cost
    return request.entry_price - cost


def _commission_estimate(request: OrderRequest, config: PaperBrokerConfig) -> float:
    if config.commission_mode == "none":
        return 0.0
    return max(0.0, request.quantity_units) * 0.00002


def _final_risk_reward(request: OrderRequest, filled_entry: float, commission: float) -> float | None:
    risk = abs(filled_entry - request.stop_loss) + commission
    if risk <= 0.0:
        return None
    if request.direction == DirectionBias.LONG:
        reward = request.take_profit - filled_entry - commission
    else:
        reward = filled_entry - request.take_profit - commission
    if reward <= 0.0:
        return None
    return reward / risk


def _partial_fill_ratio(request: OrderRequest, config: PaperBrokerConfig) -> float:
    if config.fill_mode != "realistic":
        return 1.0
    if request.quantity_units > config.max_volume * 0.5:
        return 0.5
    return 1.0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)
