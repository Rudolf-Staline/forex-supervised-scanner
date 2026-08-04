"""Optional end-to-end training smoke test, executed when scikit-learn is installed."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

from app.core.types import DirectionBias
from app.pattern_ml.dataset import PatternSamples, TemporalPatternSplit
from app.pattern_ml.detector import SklearnPatternDetector
from app.pattern_ml.training import save_pattern_model_artifact, train_random_forest_pattern_model


def test_train_save_reload_and_infer(tmp_path) -> None:
    window_size = 8
    split = TemporalPatternSplit(
        train=_samples(120, window_size=window_size, seed=1, source="train"),
        validation=_samples(50, window_size=window_size, seed=2, source="validation"),
        test=_samples(50, window_size=window_size, seed=3, source="test"),
        embargo_samples=window_size - 1,
    )

    trained = train_random_forest_pattern_model(
        split,
        minimum_precision=0.50,
        n_estimators=30,
        random_state=7,
    )
    paths = save_pattern_model_artifact(
        trained,
        output_dir=tmp_path / "double_top",
        pattern_name="double_top",
        model_version="double-top-smoke-v1",
        direction=DirectionBias.SHORT,
        window_size=window_size,
    )

    detector = SklearnPatternDetector.from_directory(tmp_path / "double_top")
    prediction = detector.predict(_frame(window_size))
    report = json.loads(paths["report"].read_text(encoding="utf-8"))

    assert 0.0 <= trained.threshold <= 1.0
    assert prediction.model_version == "double-top-smoke-v1"
    assert prediction.feature_schema == "ohlcv-shape-v1"
    assert report["deployment_authorized"] is False
    assert len(report["model_sha256"]) == 64


def _samples(count: int, *, window_size: int, seed: int, source: str) -> PatternSamples:
    rng = np.random.default_rng(seed)
    feature_count = window_size * 10
    features = rng.normal(size=(count, feature_count))
    labels = (features[:, 0] + features[:, 1] * 0.4 > 0.0).astype(np.int8)
    timestamps = pd.date_range(datetime(2024, 1, 1, tzinfo=timezone.utc), periods=count, freq="15min")
    return PatternSamples(
        features=features,
        labels=labels,
        timestamps=timestamps,
        sources=np.asarray([source] * count, dtype=object),
        positions=np.arange(count, dtype=np.int64),
        window_size=window_size,
    )


def _frame(window_size: int) -> pd.DataFrame:
    index = pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=window_size, freq="15min")
    close = np.linspace(1.10, 1.11, window_size)
    open_price = close - 0.0004
    high = close + 0.0010
    low = open_price - 0.0010
    volume = np.linspace(100.0, 180.0, window_size)
    return pd.DataFrame(
        {"open": open_price, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
