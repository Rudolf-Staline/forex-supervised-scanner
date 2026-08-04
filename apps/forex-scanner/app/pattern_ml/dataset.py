"""Causal dataset construction for one-pattern machine-learning models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from app.core.types import DirectionBias
from app.pattern_ml.features import build_shape_features
from app.setups.chart_patterns import detect_chart_patterns


@dataclass(frozen=True, slots=True)
class PatternSamples:
    features: np.ndarray
    labels: np.ndarray
    timestamps: pd.DatetimeIndex
    sources: np.ndarray
    positions: np.ndarray
    window_size: int

    def __post_init__(self) -> None:
        sample_count = len(self.labels)
        if self.features.ndim != 2 or self.features.shape[0] != sample_count:
            raise ValueError("features must have shape (n_samples, n_features)")
        if len(self.timestamps) != sample_count or len(self.sources) != sample_count or len(self.positions) != sample_count:
            raise ValueError("sample metadata lengths must match labels")
        if self.window_size < 8:
            raise ValueError("window_size must be at least 8")

    @property
    def positive_count(self) -> int:
        return int(np.sum(self.labels == 1))

    @property
    def negative_count(self) -> int:
        return int(np.sum(self.labels == 0))

    def take(self, indices: np.ndarray) -> "PatternSamples":
        return PatternSamples(
            features=self.features[indices],
            labels=self.labels[indices],
            timestamps=self.timestamps[indices],
            sources=self.sources[indices],
            positions=self.positions[indices],
            window_size=self.window_size,
        )


@dataclass(frozen=True, slots=True)
class TemporalPatternSplit:
    train: PatternSamples
    validation: PatternSamples
    test: PatternSamples
    embargo_samples: int


def build_weak_pattern_samples(
    frame: pd.DataFrame,
    *,
    source: str,
    pattern_name: str,
    direction: DirectionBias,
    window_size: int,
    stride: int = 1,
) -> PatternSamples:
    """Label trailing windows with the current causal rules detector."""

    if stride < 1:
        raise ValueError("stride must be at least 1")
    normalized_pattern = pattern_name.strip().lower().replace("-", "_").replace(" ", "_")
    features: list[np.ndarray] = []
    labels: list[int] = []
    timestamps: list[pd.Timestamp] = []
    positions: list[int] = []

    for end_position in range(window_size - 1, len(frame), stride):
        window = frame.iloc[end_position - window_size + 1 : end_position + 1]
        feature_window = build_shape_features(window, window_size=window_size)
        label = int(
            any(
                pattern.pattern_name == normalized_pattern and pattern.direction == direction
                for pattern in detect_chart_patterns(window)
            )
        )
        features.append(feature_window.flattened)
        labels.append(label)
        timestamps.append(pd.Timestamp(feature_window.end_time))
        positions.append(end_position)

    if not features:
        raise ValueError("no samples could be built from the supplied frame")
    return PatternSamples(
        features=np.vstack(features).astype(np.float64, copy=False),
        labels=np.asarray(labels, dtype=np.int8),
        timestamps=pd.DatetimeIndex(timestamps),
        sources=np.asarray([source] * len(features), dtype=object),
        positions=np.asarray(positions, dtype=np.int64),
        window_size=window_size,
    )


def concatenate_samples(sample_sets: Iterable[PatternSamples]) -> PatternSamples:
    sets = list(sample_sets)
    if not sets:
        raise ValueError("at least one sample set is required")
    window_size = sets[0].window_size
    if any(samples.window_size != window_size for samples in sets):
        raise ValueError("all sample sets must use the same window_size")
    return PatternSamples(
        features=np.vstack([samples.features for samples in sets]),
        labels=np.concatenate([samples.labels for samples in sets]),
        timestamps=pd.DatetimeIndex(np.concatenate([samples.timestamps.to_numpy() for samples in sets])),
        sources=np.concatenate([samples.sources for samples in sets]),
        positions=np.concatenate([samples.positions for samples in sets]),
        window_size=window_size,
    )


def temporal_split_by_source(
    samples: PatternSamples,
    *,
    validation_fraction: float = 0.20,
    test_fraction: float = 0.20,
    embargo_samples: int | None = None,
) -> TemporalPatternSplit:
    """Split each source chronologically and remove overlapping boundary windows."""

    if not 0.0 < validation_fraction < 0.5 or not 0.0 < test_fraction < 0.5:
        raise ValueError("validation_fraction and test_fraction must be between 0 and 0.5")
    if validation_fraction + test_fraction >= 0.8:
        raise ValueError("validation and test fractions leave too little training data")
    embargo = samples.window_size - 1 if embargo_samples is None else embargo_samples
    if embargo < 0:
        raise ValueError("embargo_samples cannot be negative")

    train_indices: list[int] = []
    validation_indices: list[int] = []
    test_indices: list[int] = []
    for source in sorted(set(str(item) for item in samples.sources)):
        source_indices = np.flatnonzero(samples.sources == source)
        source_indices = source_indices[np.argsort(samples.positions[source_indices], kind="stable")]
        count = len(source_indices)
        train_end = int(count * (1.0 - validation_fraction - test_fraction))
        validation_end = int(count * (1.0 - test_fraction))
        validation_start = min(count, train_end + embargo)
        test_start = min(count, validation_end + embargo)
        train_indices.extend(source_indices[:train_end].tolist())
        validation_indices.extend(source_indices[validation_start:validation_end].tolist())
        test_indices.extend(source_indices[test_start:].tolist())

    if not train_indices or not validation_indices or not test_indices:
        raise ValueError("temporal split produced an empty partition")
    return TemporalPatternSplit(
        train=samples.take(np.asarray(train_indices, dtype=np.int64)),
        validation=samples.take(np.asarray(validation_indices, dtype=np.int64)),
        test=samples.take(np.asarray(test_indices, dtype=np.int64)),
        embargo_samples=embargo,
    )
