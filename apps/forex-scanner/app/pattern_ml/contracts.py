"""Typed contracts shared by pattern-model adapters and the scanner bridge."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from app.core.types import DirectionBias


class PatternModelMetadata(BaseModel):
    """Stable metadata stored beside one model dedicated to one chart pattern."""

    pattern_name: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=80)
    direction: DirectionBias
    window_size: int = Field(ge=8, le=2048)
    decision_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    feature_schema: Literal["ohlcv-shape-v1"] = "ohlcv-shape-v1"
    positive_class: int | str = 1
    estimator_filename: str = Field(default="model.joblib", min_length=1, max_length=200)

    @field_validator("pattern_name")
    @classmethod
    def normalize_pattern_name(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if not normalized or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in normalized):
            raise ValueError("pattern_name must be a lowercase slug")
        return normalized


class PatternPrediction(BaseModel):
    """Non-executable evidence emitted by one pattern model."""

    pattern_name: str
    direction: DirectionBias
    detected: bool
    probability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=100.0)
    model_version: str
    window_size: int = Field(ge=1)
    feature_schema: str
    start_time: datetime
    end_time: datetime
    latest_close: float = Field(gt=0.0)
    explanation: str


@runtime_checkable
class PatternDetector(Protocol):
    """Minimal interface implemented by every one-pattern model adapter."""

    metadata: PatternModelMetadata

    def predict(self, frame: pd.DataFrame) -> PatternPrediction:
        """Evaluate the most recent market window without creating a trade."""
        ...
