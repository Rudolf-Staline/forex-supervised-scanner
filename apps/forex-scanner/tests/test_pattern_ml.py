"""Machine-learning pattern evidence tests."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.core.types import DirectionBias
from app.pattern_ml.bridge import to_chart_pattern_signals
from app.pattern_ml.contracts import PatternModelMetadata
from app.pattern_ml.detector import SklearnPatternDetector
from app.pattern_ml.features import PatternFeatureError, build_shape_features
from app.pattern_ml.registry import PatternModelRegistry


class _ProbabilityEstimator:
    classes_ = np.array([0, 1])

    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        assert features.shape == (1, 320)
        return np.array([[1.0 - self.probability, self.probability]])


class _BrokenDetector:
    metadata = PatternModelMetadata(
        pattern_name="broken_pattern",
        model_version="v1",
        direction=DirectionBias.LONG,
        window_size=32,
    )

    def predict(self, frame: pd.DataFrame):
        raise RuntimeError("model unavailable")


def test_features_are_scale_invariant() -> None:
    frame = _frame()
    base = build_shape_features(frame, window_size=32)
    scaled = frame.copy()
    scaled.loc[:, ["open", "high", "low", "close"]] *= 100.0
    transformed = build_shape_features(scaled, window_size=32)

    np.testing.assert_allclose(base.matrix, transformed.matrix, rtol=1e-9, atol=1e-9)


def test_invalid_ohlc_is_rejected() -> None:
    frame = _frame()
    frame.loc[frame.index[-1], "high"] = frame.loc[frame.index[-1], "low"] - 1.0

    with pytest.raises(PatternFeatureError, match="high"):
        build_shape_features(frame, window_size=32)


def test_sklearn_detector_emits_non_executable_prediction() -> None:
    detector = SklearnPatternDetector(
        _ProbabilityEstimator(0.87),
        PatternModelMetadata(
            pattern_name="double_top",
            model_version="double-top-v1",
            direction=DirectionBias.SHORT,
            window_size=32,
            decision_threshold=0.80,
        ),
    )

    prediction = detector.predict(_frame())

    assert prediction.detected is True
    assert prediction.pattern_name == "double_top"
    assert prediction.direction == DirectionBias.SHORT
    assert prediction.confidence == 87.0


def test_registry_isolates_model_failures_and_filters_direction() -> None:
    healthy = SklearnPatternDetector(
        _ProbabilityEstimator(0.91),
        PatternModelMetadata(
            pattern_name="double_bottom",
            model_version="double-bottom-v1",
            direction=DirectionBias.LONG,
            window_size=32,
            decision_threshold=0.80,
        ),
    )
    registry = PatternModelRegistry([healthy, _BrokenDetector()])

    result = registry.scan(_frame(), direction=DirectionBias.LONG)

    assert [prediction.pattern_name for prediction in result.predictions] == ["double_bottom"]
    assert [failure.pattern_name for failure in result.failures] == ["broken_pattern"]


def test_bridge_reuses_existing_chart_pattern_contract() -> None:
    detector = SklearnPatternDetector(
        _ProbabilityEstimator(0.93),
        PatternModelMetadata(
            pattern_name="head_and_shoulders",
            model_version="hns-v1",
            direction=DirectionBias.SHORT,
            window_size=32,
            decision_threshold=0.85,
        ),
    )
    result = PatternModelRegistry([detector]).scan(_frame())

    signals = to_chart_pattern_signals(result)

    assert len(signals) == 1
    assert signals[0].pattern_name == "ml_head_and_shoulders"
    assert signals[0].direction == DirectionBias.SHORT
    assert signals[0].stop_hint is None
    assert signals[0].target_hint is None


def test_registry_requires_explicit_replacement() -> None:
    first = SklearnPatternDetector(
        _ProbabilityEstimator(0.80),
        PatternModelMetadata(pattern_name="double_top", model_version="v1", direction=DirectionBias.SHORT, window_size=32),
    )
    second = SklearnPatternDetector(
        _ProbabilityEstimator(0.90),
        PatternModelMetadata(pattern_name="double_top", model_version="v2", direction=DirectionBias.SHORT, window_size=32),
    )
    registry = PatternModelRegistry([first])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(second)

    registry.register(second, replace=True)
    assert registry.get("double-top").metadata.model_version == "v2"


def _frame() -> pd.DataFrame:
    index = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=40, freq="15min")
    base = np.linspace(1.10, 1.13, len(index))
    open_price = base + np.sin(np.arange(len(index))) * 0.001
    close = base + np.cos(np.arange(len(index))) * 0.001
    high = np.maximum(open_price, close) + 0.002
    low = np.minimum(open_price, close) - 0.002
    volume = np.linspace(100.0, 300.0, len(index))
    return pd.DataFrame(
        {"open": open_price, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
