"""Compatibility bridge from ML predictions to existing pattern confluence."""

from __future__ import annotations

from app.pattern_ml.registry import PatternMLScanResult
from app.setups.chart_patterns import ChartPatternSignal


def to_chart_pattern_signals(result: PatternMLScanResult) -> list[ChartPatternSignal]:
    """Expose detected ML evidence through the scanner's existing pattern type."""

    return [
        ChartPatternSignal(
            pattern_name=f"ml_{prediction.pattern_name}",
            direction=prediction.direction,
            confidence=prediction.confidence,
            entry_hint=prediction.latest_close,
            explanation=prediction.explanation,
        )
        for prediction in result.detected_predictions
    ]
