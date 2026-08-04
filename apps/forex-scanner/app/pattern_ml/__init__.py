"""Optional machine-learning evidence for chart-pattern confluence."""

from app.pattern_ml.bridge import to_chart_pattern_signals
from app.pattern_ml.contracts import PatternDetector, PatternModelMetadata, PatternPrediction
from app.pattern_ml.detector import SklearnPatternDetector
from app.pattern_ml.features import FEATURE_SCHEMA, PatternFeatureError, PatternFeatureWindow, build_shape_features
from app.pattern_ml.registry import PatternMLScanResult, PatternModelFailure, PatternModelRegistry

__all__ = [
    "FEATURE_SCHEMA",
    "PatternDetector",
    "PatternFeatureError",
    "PatternFeatureWindow",
    "PatternMLScanResult",
    "PatternModelFailure",
    "PatternModelMetadata",
    "PatternModelRegistry",
    "PatternPrediction",
    "SklearnPatternDetector",
    "build_shape_features",
    "to_chart_pattern_signals",
]
