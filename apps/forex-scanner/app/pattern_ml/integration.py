"""Optional integration of ML pattern evidence with rules-based raw setups."""

from __future__ import annotations

from enum import Enum
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from app.core.types import RawSetup
from app.pattern_ml.bridge import to_chart_pattern_signals
from app.pattern_ml.registry import PatternMLScanResult, PatternModelRegistry
from app.setups.chart_patterns import MAX_PATTERN_SCORE, pattern_score


class PatternMLMode(str, Enum):
    """Controls whether ML evidence is ignored, reported, or scored."""

    DISABLED = "disabled"
    REPORT_ONLY = "report_only"
    CONFLUENCE = "confluence"


class PatternMLEnrichmentResult(BaseModel):
    """Raw setups plus the complete inference diagnostics used to enrich them."""

    setups: list[RawSetup] = Field(default_factory=list)
    scan: PatternMLScanResult = Field(default_factory=PatternMLScanResult)
    mode: PatternMLMode = PatternMLMode.DISABLED


def enrich_setups_with_pattern_ml(
    setups: list[RawSetup],
    frame: pd.DataFrame,
    *,
    registry: PatternModelRegistry | None = None,
    mode: PatternMLMode | str = PatternMLMode.DISABLED,
    confluence_weight: float = 0.35,
) -> PatternMLEnrichmentResult:
    """Attach optional ML evidence without creating or executing any setup.

    `report_only` records model evidence but leaves the existing pattern score
    unchanged. `confluence` adds only a bounded fraction of the normal pattern
    score, preserving the scanner's existing 0-15 cap.
    """

    resolved_mode = PatternMLMode(mode)
    if not 0.0 <= confluence_weight <= 1.0:
        raise ValueError("confluence_weight must be between 0 and 1")
    if resolved_mode == PatternMLMode.DISABLED or registry is None or not setups:
        return PatternMLEnrichmentResult(setups=[setup.model_copy(deep=True) for setup in setups], mode=resolved_mode)

    scan = registry.scan(frame)
    detected_signals = to_chart_pattern_signals(scan)
    enriched: list[RawSetup] = []
    for setup in setups:
        compatible = [signal for signal in detected_signals if signal.direction == setup.direction]
        if not compatible:
            enriched.append(setup.model_copy(deep=True))
            continue

        names = list(dict.fromkeys([*setup.detected_patterns, *(signal.pattern_name for signal in compatible)]))
        explanations = list(dict.fromkeys([*setup.pattern_explanations, *(signal.explanation for signal in compatible)]))
        score = setup.pattern_score
        if resolved_mode == PatternMLMode.CONFLUENCE:
            ml_score = pattern_score(compatible, setup.direction)
            score = round(min(MAX_PATTERN_SCORE, score + ml_score * confluence_weight), 2)

        explanation = setup.explanation
        evidence_names = ", ".join(signal.pattern_name for signal in compatible)
        if evidence_names and evidence_names not in explanation:
            explanation = f"{explanation} ML pattern evidence: {evidence_names}."
        enriched.append(
            setup.model_copy(
                deep=True,
                update={
                    "detected_patterns": names,
                    "pattern_explanations": explanations,
                    "pattern_score": score,
                    "explanation": explanation,
                },
            )
        )
    return PatternMLEnrichmentResult(setups=enriched, scan=scan, mode=resolved_mode)


def detect_setups_with_optional_pattern_ml(
    *,
    pattern_ml_registry: PatternModelRegistry | None = None,
    pattern_ml_mode: PatternMLMode | str = PatternMLMode.DISABLED,
    pattern_ml_confluence_weight: float = 0.35,
    **detector_kwargs: Any,
) -> PatternMLEnrichmentResult:
    """Drop-in wrapper around the current rules-based setup detector.

    Existing callers remain unchanged because the ML path is disabled unless a
    registry is explicitly supplied and the mode is not `disabled`.
    """

    from app.setups.detector import detect_setups

    setups = detect_setups(**detector_kwargs)
    entry_frame = detector_kwargs.get("entry_df")
    if not isinstance(entry_frame, pd.DataFrame):
        raise TypeError("entry_df must be a pandas DataFrame")
    return enrich_setups_with_pattern_ml(
        setups,
        entry_frame,
        registry=pattern_ml_registry,
        mode=pattern_ml_mode,
        confluence_weight=pattern_ml_confluence_weight,
    )
