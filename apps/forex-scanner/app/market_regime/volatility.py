"""Volatility regime detection from ATR and Bollinger Band width."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.types import VolatilityRegime, VolatilityResult


def detect_volatility_regime(df: pd.DataFrame, min_history: int = 40) -> VolatilityResult:
    """Classify volatility using percentile ranks of ATR percentage and band width."""

    required = {"close", "atr_14", "bb_width"}
    missing = required - set(df.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        return VolatilityResult(
            regime=VolatilityRegime.UNKNOWN,
            suitability_score=0.0,
            percentile_rank=0.0,
            is_unstable=False,
            explanation=f"missing volatility inputs: {missing_text}",
        )

    volatility = pd.DataFrame(
        {
            "atr_pct": df["atr_14"] / df["close"],
            "bb_width": df["bb_width"],
        }
    ).replace([np.inf, -np.inf], np.nan)
    volatility = volatility.dropna()
    if len(volatility) < min_history:
        return VolatilityResult(
            regime=VolatilityRegime.UNKNOWN,
            suitability_score=45.0,
            percentile_rank=50.0,
            is_unstable=False,
            explanation="insufficient volatility history for percentile classification",
        )

    current_atr_pct = float(volatility["atr_pct"].iloc[-1])
    current_width = float(volatility["bb_width"].iloc[-1])
    atr_rank = float((volatility["atr_pct"] <= current_atr_pct).mean())
    width_rank = float((volatility["bb_width"] <= current_width).mean())
    combined_rank = (atr_rank + width_rank) / 2.0
    percentile = round(combined_rank * 100.0, 2)

    if combined_rank >= 0.92:
        return VolatilityResult(
            regime=VolatilityRegime.HIGH_VOLATILITY,
            suitability_score=18.0,
            percentile_rank=percentile,
            is_unstable=True,
            explanation="ATR percentage and Bollinger width are in an extreme recent percentile",
        )
    if combined_rank >= 0.75:
        suitability = max(35.0, min(82.0, 100.0 - abs(combined_rank - 0.55) * 130.0))
        return VolatilityResult(
            regime=VolatilityRegime.ELEVATED,
            suitability_score=round(suitability, 2),
            percentile_rank=percentile,
            is_unstable=False,
            explanation="volatility is elevated but not unstable",
        )
    if combined_rank <= 0.15:
        return VolatilityResult(
            regime=VolatilityRegime.COMPRESSED,
            suitability_score=45.0,
            percentile_rank=percentile,
            is_unstable=False,
            explanation="volatility is compressed relative to recent history",
        )

    suitability = max(25.0, min(100.0, 100.0 - abs(combined_rank - 0.55) * 130.0))
    return VolatilityResult(
        regime=VolatilityRegime.NORMAL,
        suitability_score=round(suitability, 2),
        percentile_rank=percentile,
        is_unstable=False,
        explanation="volatility is within a usable recent range",
    )

