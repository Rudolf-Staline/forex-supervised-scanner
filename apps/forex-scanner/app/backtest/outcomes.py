"""Path-based post-trade outcome enrichment for calibration reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.core.types import DirectionBias, RiskPlan, TradeOutcomeLabel


ExitReason = Literal["take_profit", "stop_loss", "time_exit", "end_of_data"]


@dataclass(frozen=True)
class PathOutcome:
    """Realized path metrics measured in R units and bars after entry."""

    outcome: TradeOutcomeLabel
    tp1_hit: bool
    tp2_hit: bool
    tp3_hit: bool
    mae: float
    mfe: float
    bars_to_activation: int | None
    bars_to_invalidation: int | None
    bars_to_tp1: int | None
    bars_to_tp2: int | None
    bars_to_tp3: int | None


def evaluate_path(direction: DirectionBias, risk_plan: RiskPlan, future: pd.DataFrame, exit_reason: ExitReason, net_r: float) -> PathOutcome:
    """Evaluate TP hits, MAE/MFE, event timing, and a richer outcome label."""

    risk = abs(float(risk_plan.entry) - float(risk_plan.stop_loss))
    if risk <= 0.0:
        return PathOutcome(
            outcome=TradeOutcomeLabel.TIMEOUT,
            tp1_hit=False,
            tp2_hit=False,
            tp3_hit=False,
            mae=0.0,
            mfe=0.0,
            bars_to_activation=0,
            bars_to_invalidation=None,
            bars_to_tp1=None,
            bars_to_tp2=None,
            bars_to_tp3=None,
        )

    tp1_hit = tp2_hit = tp3_hit = False
    bars_to_tp1: int | None = None
    bars_to_tp2: int | None = None
    bars_to_tp3: int | None = None
    bars_to_invalidation: int | None = None
    mfe = 0.0
    mae = 0.0

    for bar_number, (_timestamp, row) in enumerate(future.iterrows(), start=1):
        high = float(row["high"])
        low = float(row["low"])
        if direction == DirectionBias.LONG:
            favorable = (high - float(risk_plan.entry)) / risk
            adverse = (float(risk_plan.entry) - low) / risk
            stop_hit = low <= float(risk_plan.stop_loss)
            tp1_now = high >= float(risk_plan.tp1)
            tp2_now = high >= float(risk_plan.tp2)
            tp3_now = high >= float(risk_plan.tp3)
        else:
            favorable = (float(risk_plan.entry) - low) / risk
            adverse = (high - float(risk_plan.entry)) / risk
            stop_hit = high >= float(risk_plan.stop_loss)
            tp1_now = low <= float(risk_plan.tp1)
            tp2_now = low <= float(risk_plan.tp2)
            tp3_now = low <= float(risk_plan.tp3)

        mfe = max(mfe, favorable)
        mae = max(mae, adverse)
        if stop_hit and bars_to_invalidation is None:
            bars_to_invalidation = bar_number
            break
        if tp1_now and not tp1_hit:
            tp1_hit = True
            bars_to_tp1 = bar_number
        if tp2_now and not tp2_hit:
            tp2_hit = True
            bars_to_tp2 = bar_number
        if tp3_now and not tp3_hit:
            tp3_hit = True
            bars_to_tp3 = bar_number

    return PathOutcome(
        outcome=label_outcome(
            exit_reason=exit_reason,
            net_r=net_r,
            tp1_hit=tp1_hit,
            tp2_hit=tp2_hit,
            tp3_hit=tp3_hit,
            mae=mae,
            mfe=mfe,
            bars_to_invalidation=bars_to_invalidation,
        ),
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        tp3_hit=tp3_hit,
        mae=round(mae, 4),
        mfe=round(mfe, 4),
        bars_to_activation=0,
        bars_to_invalidation=bars_to_invalidation,
        bars_to_tp1=bars_to_tp1,
        bars_to_tp2=bars_to_tp2,
        bars_to_tp3=bars_to_tp3,
    )


def label_outcome(
    exit_reason: ExitReason,
    net_r: float,
    tp1_hit: bool,
    tp2_hit: bool,
    tp3_hit: bool,
    mae: float,
    mfe: float,
    bars_to_invalidation: int | None,
) -> TradeOutcomeLabel:
    """Classify the realized trade path into deterministic calibration labels."""

    if exit_reason in {"time_exit", "end_of_data"} and not tp1_hit and abs(net_r) <= 0.1:
        return TradeOutcomeLabel.BREAKEVEN
    if exit_reason in {"time_exit", "end_of_data"} and not tp1_hit:
        return TradeOutcomeLabel.TIMEOUT
    if exit_reason == "stop_loss" or net_r <= -0.35:
        if bars_to_invalidation is not None and bars_to_invalidation <= 3 and mfe < 0.35:
            return TradeOutcomeLabel.LOSS_FAST
        return TradeOutcomeLabel.LOSS_CLEAN
    if tp3_hit or (tp2_hit and mae <= 0.45):
        return TradeOutcomeLabel.WIN_CLEAN
    if tp2_hit or net_r >= 1.0:
        return TradeOutcomeLabel.WIN_MESSY
    if tp1_hit or net_r > 0.1:
        return TradeOutcomeLabel.PARTIAL_WIN
    if abs(net_r) <= 0.1:
        return TradeOutcomeLabel.BREAKEVEN
    return TradeOutcomeLabel.TIMEOUT
