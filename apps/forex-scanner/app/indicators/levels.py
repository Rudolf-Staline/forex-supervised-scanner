"""Support and resistance approximation from recent swing points."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.core.types import PriceLevel


@dataclass(frozen=True)
class LevelSet:
    """Nearest support and resistance levels around the current price."""

    supports: list[PriceLevel]
    resistances: list[PriceLevel]

    @property
    def all_levels(self) -> list[PriceLevel]:
        return [*self.supports, *self.resistances]


def find_key_levels(df: pd.DataFrame, lookback: int = 160, tolerance_atr: float = 0.55) -> LevelSet:
    """Cluster recent swing highs and lows into approximate support and resistance levels."""

    if df.empty:
        return LevelSet(supports=[], resistances=[])
    recent = df.tail(lookback)
    current_close = float(recent["close"].iloc[-1])
    atr = float(recent["atr_14"].dropna().iloc[-1]) if "atr_14" in recent and not recent["atr_14"].dropna().empty else 0.0
    tolerance = max(atr * tolerance_atr, current_close * 0.00025)

    swing_lows = _cluster_levels(recent["swing_low"].dropna().to_numpy(), tolerance, current_close, "support")
    swing_highs = _cluster_levels(recent["swing_high"].dropna().to_numpy(), tolerance, current_close, "resistance")

    if not swing_lows:
        low = float(recent["low"].rolling(20, min_periods=1).min().iloc[-1])
        swing_lows = [PriceLevel(price=low, kind="support", strength=35.0, touches=1, label="20-bar low")]
    if not swing_highs:
        high = float(recent["high"].rolling(20, min_periods=1).max().iloc[-1])
        swing_highs = [PriceLevel(price=high, kind="resistance", strength=35.0, touches=1, label="20-bar high")]

    supports = sorted(
        [level for level in swing_lows if level.price < current_close],
        key=lambda level: (abs(current_close - level.price), -level.strength),
    )
    resistances = sorted(
        [level for level in swing_highs if level.price > current_close],
        key=lambda level: (abs(level.price - current_close), -level.strength),
    )
    return LevelSet(supports=supports[:5], resistances=resistances[:5])


def nearest_support(levels: LevelSet, price: float) -> PriceLevel | None:
    supports = [level for level in levels.supports if level.price < price]
    return min(supports, key=lambda level: price - level.price) if supports else None


def nearest_resistance(levels: LevelSet, price: float) -> PriceLevel | None:
    resistances = [level for level in levels.resistances if level.price > price]
    return min(resistances, key=lambda level: level.price - price) if resistances else None


def _cluster_levels(values: np.ndarray, tolerance: float, current_close: float, kind: str) -> list[PriceLevel]:
    if values.size == 0:
        return []
    sorted_values = np.sort(values)
    clusters: list[list[float]] = []
    for value in sorted_values:
        if not clusters or abs(float(np.mean(clusters[-1])) - float(value)) > tolerance:
            clusters.append([float(value)])
        else:
            clusters[-1].append(float(value))

    max_touches = max(len(cluster) for cluster in clusters)
    levels: list[PriceLevel] = []
    for cluster in clusters:
        price = float(np.mean(cluster))
        touches = len(cluster)
        distance_penalty = min(abs(price - current_close) / max(current_close * 0.02, tolerance), 1.0)
        touch_score = 70.0 * touches / max_touches
        strength = min(100.0, max(20.0, touch_score + 30.0 * (1.0 - distance_penalty)))
        label = f"{touches}-touch {kind}"
        levels.append(PriceLevel(price=price, kind=kind, strength=strength, touches=touches, label=label))
    return levels

