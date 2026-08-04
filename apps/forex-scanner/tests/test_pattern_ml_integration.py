"""Optional pattern-ML integration and temporal training tests."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.core.types import DirectionBias, MarketRegime, RawSetup, SetupFamily, TradingStyle
from app.pattern_ml.contracts import PatternModelMetadata
from app.pattern_ml.dataset import PatternSamples, temporal_split_by_source
from app.pattern_ml.detector import SklearnPatternDetector
from app.pattern_ml.integration import PatternMLMode, enrich_setups_with_pattern_ml
from app.pattern_ml.registry import PatternModelRegistry
from app.pattern_ml.training import choose_probability_threshold


class _ProbabilityEstimator:
    classes_ = np.array([0, 1])

    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        return np.array([[1.0 - self.probability, self.probability]])


def test_report_only_adds_evidence_without_changing_score() -> None:
    setup = _raw_setup(pattern_score=4.0)
    registry = _registry(probability=0.92)

    result = enrich_setups_with_pattern_ml(
        [setup],
        _frame(),
        registry=registry,
        mode=PatternMLMode.REPORT_ONLY,
    )

    enriched = result.setups[0]
    assert enriched.pattern_score == 4.0
    assert "ml_double_top" in enriched.detected_patterns
    assert "ML pattern evidence" in enriched.explanation
    assert setup.detected_patterns == ["double_top"]


def test_confluence_bonus_is_bounded() -> None:
    result = enrich_setups_with_pattern_ml(
        [_raw_setup(pattern_score=14.0)],
        _frame(),
        registry=_registry(probability=0.99),
        mode=PatternMLMode.CONFLUENCE,
        confluence_weight=0.35,
    )

    assert 14.0 < result.setups[0].pattern_score <= 15.0


def test_disabled_mode_does_not_run_models() -> None:
    result = enrich_setups_with_pattern_ml(
        [_raw_setup(pattern_score=2.0)],
        _frame(),
        registry=_registry(probability=0.99),
        mode=PatternMLMode.DISABLED,
    )

    assert result.scan.predictions == []
    assert result.setups[0].detected_patterns == ["double_top"]


def test_temporal_split_embargoes_overlapping_samples_per_source() -> None:
    samples = _samples()

    split = temporal_split_by_source(
        samples,
        validation_fraction=0.20,
        test_fraction=0.20,
        embargo_samples=5,
    )

    assert split.train.positions.max() < split.validation.positions.min() - 4
    assert split.validation.positions.max() < split.test.positions.min() - 4


def test_threshold_prefers_recall_under_precision_constraint() -> None:
    labels = np.array([1, 1, 1, 0, 0, 0], dtype=np.int8)
    probabilities = np.array([0.95, 0.80, 0.60, 0.70, 0.40, 0.10])

    threshold = choose_probability_threshold(labels, probabilities, minimum_precision=0.75)

    assert threshold == 0.60


def _registry(*, probability: float) -> PatternModelRegistry:
    detector = SklearnPatternDetector(
        _ProbabilityEstimator(probability),
        PatternModelMetadata(
            pattern_name="double_top",
            model_version="double-top-v1",
            direction=DirectionBias.SHORT,
            window_size=32,
            decision_threshold=0.80,
        ),
    )
    return PatternModelRegistry([detector])


def _raw_setup(*, pattern_score: float) -> RawSetup:
    return RawSetup(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        family=SetupFamily.TREND_CONTINUATION,
        regime=MarketRegime.TRENDING_DOWN,
        direction=DirectionBias.SHORT,
        entry=1.10,
        stop_candidates={"atr": 1.11},
        target_candidates={"atr": 1.08},
        trend_clarity=75.0,
        structure_quality=72.0,
        mtf_alignment=80.0,
        volatility_suitability=68.0,
        momentum_confirmation=74.0,
        level_proximity=70.0,
        explanation="Rules-based bearish setup.",
        detected_patterns=["double_top"],
        pattern_score=pattern_score,
        pattern_explanations=["Rules-based double top."],
    )


def _frame() -> pd.DataFrame:
    index = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=40, freq="15min")
    base = np.linspace(1.13, 1.10, len(index))
    open_price = base + np.sin(np.arange(len(index))) * 0.001
    close = base + np.cos(np.arange(len(index))) * 0.001
    high = np.maximum(open_price, close) + 0.002
    low = np.minimum(open_price, close) - 0.002
    return pd.DataFrame({"open": open_price, "high": high, "low": low, "close": close}, index=index)


def _samples() -> PatternSamples:
    count = 100
    timestamps = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=count, freq="15min")
    return PatternSamples(
        features=np.zeros((count, 20), dtype=float),
        labels=np.asarray([index % 7 == 0 for index in range(count)], dtype=np.int8),
        timestamps=timestamps,
        sources=np.asarray(["EURUSD_M15"] * count, dtype=object),
        positions=np.arange(count, dtype=np.int64),
        window_size=8,
    )
