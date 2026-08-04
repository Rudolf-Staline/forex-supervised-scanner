"""Explicit registry and failure-isolated execution for pattern detectors."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
from pydantic import BaseModel, Field

from app.core.types import DirectionBias
from app.pattern_ml.contracts import PatternDetector, PatternPrediction


class PatternModelFailure(BaseModel):
    pattern_name: str
    model_version: str
    error_type: str
    message: str


class PatternMLScanResult(BaseModel):
    predictions: list[PatternPrediction] = Field(default_factory=list)
    failures: list[PatternModelFailure] = Field(default_factory=list)

    @property
    def detected_predictions(self) -> list[PatternPrediction]:
        return [prediction for prediction in self.predictions if prediction.detected]


class PatternModelRegistry:
    """Keeps one active model per pattern and evaluates models independently."""

    def __init__(self, detectors: Iterable[PatternDetector] = ()) -> None:
        self._detectors: dict[str, PatternDetector] = {}
        for detector in detectors:
            self.register(detector)

    def register(self, detector: PatternDetector, *, replace: bool = False) -> None:
        pattern_name = detector.metadata.pattern_name
        if pattern_name in self._detectors and not replace:
            raise ValueError(f"a detector is already registered for pattern {pattern_name!r}")
        self._detectors[pattern_name] = detector

    def get(self, pattern_name: str) -> PatternDetector:
        normalized = pattern_name.strip().lower().replace("-", "_").replace(" ", "_")
        try:
            return self._detectors[normalized]
        except KeyError as exc:
            raise KeyError(f"no detector registered for pattern {normalized!r}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._detectors))

    def scan(self, frame: pd.DataFrame, *, direction: DirectionBias | None = None) -> PatternMLScanResult:
        predictions: list[PatternPrediction] = []
        failures: list[PatternModelFailure] = []
        for detector in self._detectors.values():
            if direction is not None and detector.metadata.direction != direction:
                continue
            try:
                predictions.append(detector.predict(frame))
            except Exception as exc:
                failures.append(
                    PatternModelFailure(
                        pattern_name=detector.metadata.pattern_name,
                        model_version=detector.metadata.model_version,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
        return PatternMLScanResult(predictions=predictions, failures=failures)
