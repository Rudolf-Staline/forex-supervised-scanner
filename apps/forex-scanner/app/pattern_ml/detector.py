"""Adapters that turn fitted estimators into one-pattern detectors."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.pattern_ml.contracts import PatternModelMetadata, PatternPrediction
from app.pattern_ml.features import build_shape_features


class PatternModelLoadError(RuntimeError):
    """Raised when a pattern model artifact cannot be loaded safely."""


class PatternModelInferenceError(RuntimeError):
    """Raised when an estimator does not expose a supported prediction API."""


class SklearnPatternDetector:
    """One fitted scikit-learn-compatible estimator dedicated to one pattern."""

    def __init__(self, estimator: Any, metadata: PatternModelMetadata) -> None:
        self.estimator = estimator
        self.metadata = metadata

    @classmethod
    def from_directory(cls, model_directory: str | Path) -> "SklearnPatternDetector":
        directory = Path(model_directory)
        metadata_path = directory / "metadata.json"
        if not metadata_path.is_file():
            raise PatternModelLoadError(f"missing metadata file: {metadata_path}")
        try:
            metadata = PatternModelMetadata.model_validate(json.loads(metadata_path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise PatternModelLoadError(f"invalid pattern metadata: {metadata_path}") from exc

        estimator_path = directory / metadata.estimator_filename
        if not estimator_path.is_file():
            raise PatternModelLoadError(f"missing estimator file: {estimator_path}")
        try:
            import joblib
        except ImportError as exc:
            raise PatternModelLoadError("install the project with the 'ml' extra to load model artifacts") from exc
        try:
            estimator = joblib.load(estimator_path)
        except Exception as exc:
            raise PatternModelLoadError(f"could not load estimator: {estimator_path}") from exc
        return cls(estimator=estimator, metadata=metadata)

    def predict(self, frame: pd.DataFrame) -> PatternPrediction:
        feature_window = build_shape_features(frame, window_size=self.metadata.window_size)
        probability = _positive_probability(
            self.estimator,
            feature_window.flattened.reshape(1, -1),
            positive_class=self.metadata.positive_class,
        )
        detected = probability >= self.metadata.decision_threshold
        confidence = round(probability * 100.0, 4)
        state = "detected" if detected else "not detected"
        return PatternPrediction(
            pattern_name=self.metadata.pattern_name,
            direction=self.metadata.direction,
            detected=detected,
            probability=probability,
            confidence=confidence,
            model_version=self.metadata.model_version,
            window_size=self.metadata.window_size,
            feature_schema=self.metadata.feature_schema,
            start_time=feature_window.start_time,
            end_time=feature_window.end_time,
            latest_close=feature_window.latest_close,
            explanation=(
                f"ML pattern evidence: {self.metadata.pattern_name} {state} "
                f"with probability {probability:.3f} using model {self.metadata.model_version}."
            ),
        )


def _positive_probability(estimator: Any, features: np.ndarray, *, positive_class: int | str) -> float:
    if hasattr(estimator, "predict_proba"):
        probabilities = np.asarray(estimator.predict_proba(features), dtype=float)
        if probabilities.ndim != 2 or probabilities.shape[0] != 1:
            raise PatternModelInferenceError("predict_proba must return shape (1, n_classes)")
        classes = list(getattr(estimator, "classes_", range(probabilities.shape[1])))
        try:
            class_index = classes.index(positive_class)
        except ValueError as exc:
            raise PatternModelInferenceError(f"positive class {positive_class!r} is absent from estimator classes") from exc
        probability = float(probabilities[0, class_index])
    elif hasattr(estimator, "decision_function"):
        score = float(np.asarray(estimator.decision_function(features), dtype=float).reshape(-1)[0])
        probability = 1.0 / (1.0 + math.exp(-max(-709.0, min(709.0, score))))
    elif hasattr(estimator, "predict"):
        prediction = np.asarray(estimator.predict(features)).reshape(-1)
        if prediction.size != 1:
            raise PatternModelInferenceError("predict must return one result")
        probability = 1.0 if prediction[0] == positive_class else 0.0
    else:
        raise PatternModelInferenceError("estimator must implement predict_proba, decision_function, or predict")

    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise PatternModelInferenceError("estimator produced an invalid probability")
    return probability
