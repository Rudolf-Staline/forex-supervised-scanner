"""Post-trade outcome enrichment tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.backtest.outcomes import evaluate_path, label_outcome
from app.core.types import DirectionBias, RiskPlan, TradeOutcomeLabel


def _risk_plan() -> RiskPlan:
    return RiskPlan(
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        tp1=1.1050,
        tp2=1.1100,
        tp3=1.1150,
        risk_reward=2.0,
        tp1_risk_reward=1.0,
        tp2_risk_reward=2.0,
        tp3_risk_reward=3.0,
        stop_method="atr",
        target_method="fixed_rr",
    )


def _future(highs: list[float], lows: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    index = pd.date_range(datetime(2025, 1, 1, tzinfo=timezone.utc), periods=len(highs), freq="5min")
    close = closes or [(high + low) / 2.0 for high, low in zip(highs, lows, strict=True)]
    return pd.DataFrame({"open": close, "high": highs, "low": lows, "close": close, "volume": 100.0}, index=index)


def test_outcome_label_covers_all_requested_labels() -> None:
    assert label_outcome("take_profit", 2.2, True, True, True, 0.2, 3.0, None) == TradeOutcomeLabel.WIN_CLEAN
    assert label_outcome("take_profit", 1.2, True, True, False, 0.8, 2.0, None) == TradeOutcomeLabel.WIN_MESSY
    assert label_outcome("time_exit", 0.4, True, False, False, 0.4, 1.1, None) == TradeOutcomeLabel.PARTIAL_WIN
    assert label_outcome("time_exit", 0.02, False, False, False, 0.2, 0.2, None) == TradeOutcomeLabel.BREAKEVEN
    assert label_outcome("time_exit", -0.2, False, False, False, 0.4, 0.3, None) == TradeOutcomeLabel.TIMEOUT
    assert label_outcome("stop_loss", -1.0, False, False, False, 0.9, 0.6, 8) == TradeOutcomeLabel.LOSS_CLEAN
    assert label_outcome("stop_loss", -1.0, False, False, False, 1.0, 0.1, 2) == TradeOutcomeLabel.LOSS_FAST


def test_evaluate_path_records_tp_hits_mae_mfe_and_bars() -> None:
    path = evaluate_path(
        DirectionBias.LONG,
        _risk_plan(),
        _future(highs=[1.1040, 1.1055, 1.1105, 1.1155], lows=[1.0980, 1.0990, 1.1010, 1.1060]),
        "take_profit",
        2.0,
    )

    assert path.tp1_hit
    assert path.tp2_hit
    assert path.tp3_hit
    assert path.bars_to_tp1 == 2
    assert path.bars_to_tp2 == 3
    assert path.bars_to_tp3 == 4
    assert path.bars_to_activation == 0
    assert path.mfe >= 3.0
    assert path.mae > 0.0
