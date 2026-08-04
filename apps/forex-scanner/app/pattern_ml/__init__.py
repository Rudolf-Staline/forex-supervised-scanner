"""Optional machine-learning evidence for chart-pattern confluence."""

from app.pattern_ml.bridge import to_chart_pattern_signals
from app.pattern_ml.contracts import PatternDetector, PatternModelMetadata, PatternPrediction
from app.pattern_ml.dataset import PatternSamples, TemporalPatternSplit, build_weak_pattern_samples, concatenate_samples, temporal_split_by_source
from app.pattern_ml.detector import SklearnPatternDetector
from app.pattern_ml.features import FEATURE_SCHEMA, PatternFeatureError, PatternFeatureWindow, build_shape_features
from app.pattern_ml.integration import PatternMLEnrichmentResult, PatternMLMode, detect_setups_with_optional_pattern_ml, enrich_setups_with_pattern_ml
from app.pattern_ml.registry import PatternMLScanResult, PatternModelFailure, PatternModelRegistry
from app.pattern_ml.training import TrainedPatternModel, choose_probability_threshold, save_pattern_model_artifact, train_random_forest_pattern_model

__all__ = [
    "FEATURE_SCHEMA",
    "PatternDetector",
    "PatternFeatureError",
    "PatternFeatureWindow",
    "PatternMLEnrichmentResult",
    "PatternMLMode",
    "PatternMLScanResult",
    "PatternModelFailure",
    "PatternModelMetadata",
    "PatternModelRegistry",
    "PatternPrediction",
    "PatternSamples",
    "SklearnPatternDetector",
    "TemporalPatternSplit",
    "TrainedPatternModel",
    "build_shape_features",
    "build_weak_pattern_samples",
    "choose_probability_threshold",
    "concatenate_samples",
    "detect_setups_with_optional_pattern_ml",
    "enrich_setups_with_pattern_ml",
    "save_pattern_model_artifact",
    "temporal_split_by_source",
    "to_chart_pattern_signals",
    "train_random_forest_pattern_model",
]
