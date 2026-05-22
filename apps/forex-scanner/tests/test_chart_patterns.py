"""Chart-pattern confluence tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.core.types import DirectionBias
from app.setups.chart_patterns import detect_chart_patterns, pattern_score


def test_bullish_and_bearish_engulfing_are_detected() -> None:
    bullish = detect_chart_patterns(_frame([(1.1000, 1.1010, 1.0940, 1.0960), (1.0950, 1.1030, 1.0940, 1.1020)]))
    bearish = detect_chart_patterns(_frame([(1.1000, 1.1060, 1.0990, 1.1050), (1.1060, 1.1070, 1.0980, 1.0990)]))

    assert any(pattern.pattern_name == "bullish_engulfing" and pattern.direction == DirectionBias.LONG for pattern in bullish)
    assert any(pattern.pattern_name == "bearish_engulfing" and pattern.direction == DirectionBias.SHORT for pattern in bearish)


def test_pin_bar_is_detected_with_direction() -> None:
    patterns = detect_chart_patterns(_frame([(1.1000, 1.1010, 1.0900, 1.1005)]))

    assert any(pattern.pattern_name == "pin_bar" and pattern.direction == DirectionBias.LONG for pattern in patterns)


def test_double_top_and_double_bottom_are_detected() -> None:
    top_prices = [(1.1000, 1.1010, 1.0960, 1.0990)] * 12 + [(1.1000, 1.1100, 1.0980, 1.1060)] + [(1.1040, 1.1050, 1.0990, 1.1010)] * 8 + [(1.1050, 1.1101, 1.1020, 1.1099)]
    bottom_prices = [(1.1000, 1.1040, 1.0990, 1.1010)] * 12 + [(1.1000, 1.1020, 1.0900, 1.0940)] + [(1.0960, 1.1010, 1.0950, 1.0990)] * 8 + [(1.0950, 1.0980, 1.0899, 1.0901)]

    assert any(pattern.pattern_name == "double_top" for pattern in detect_chart_patterns(_frame(top_prices)))
    assert any(pattern.pattern_name == "double_bottom" for pattern in detect_chart_patterns(_frame(bottom_prices)))


def test_breakout_retest_is_detected() -> None:
    prices = [(1.1000, 1.1050, 1.0950, 1.1000)] * 30
    prices.extend([(1.1060, 1.1120, 1.1055, 1.1110), (1.1100, 1.1130, 1.1048, 1.1080)])

    patterns = detect_chart_patterns(_frame(prices))

    assert any(pattern.pattern_name == "breakout_retest" and pattern.direction == DirectionBias.LONG for pattern in patterns)


def test_pattern_score_is_capped_and_directional() -> None:
    patterns = detect_chart_patterns(_frame([(1.1000, 1.1010, 1.0940, 1.0960), (1.0950, 1.1030, 1.0940, 1.1020)]))

    assert 0.0 < pattern_score(patterns, DirectionBias.LONG) <= 15.0
    assert pattern_score(patterns, DirectionBias.SHORT) == 0.0


def _frame(prices: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    padding = [(1.1000, 1.1020, 1.0980, 1.1005)] * max(0, 40 - len(prices))
    rows = [*padding, *prices]
    index = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=len(rows), freq="15min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
