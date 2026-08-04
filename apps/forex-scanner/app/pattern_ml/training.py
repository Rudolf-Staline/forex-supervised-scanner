"""Model fitting, threshold selection, evaluation, and artifact persistence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.core.types import DirectionBias
from app.pattern_ml.contracts import PatternModelMetadata
from app.pattern_ml.dataset import PatternSamples, TemporalPatternSplit
from app.pattern_ml.features import FEATURE_COLUMNS, FEATURE_SCHEMA


@dataclass(frozen=True, slots=True)
class TrainedPatternModel:
    estimator: Any
    threshold: float
    report: dict[str, Any]


def choose_probability_threshold(
    labels: np.ndarray,
    probabilities: np.ndarray,
    *,
    minimum_precision: float = 0.70,
) -> float:
    """Choose the highest-recall validation threshold meeting minimum precision."""

    y_true = np.asarray(labels, dtype=np.int8)
    y_prob = np.asarray(probabilities, dtype=float)
    if y_true.shape != y_prob.shape or y_true.ndim != 1:
        raise ValueError("labels and probabilities must have equal one-dimensional shapes")
    if not 0.0 <= minimum_precision <= 1.0:
        raise ValueError("minimum_precision must be between 0 and 1")
    if not np.isfinite(y_prob).all() or np.any((y_prob < 0.0) | (y_prob > 1.0)):
        raise ValueError("probabilities must be finite values between 0 and 1")

    candidates = np.unique(np.concatenate([np.array([0.0, 0.5, 1.0]), y_prob]))
    feasible: list[tuple[float, float, float]] = []
    fallback: list[tuple[float, float]] = []
    for threshold in candidates:
        predicted = y_prob >= threshold
        true_positive = int(np.sum(predicted & (y_true == 1)))
        false_positive = int(np.sum(predicted & (y_true == 0)))
        false_negative = int(np.sum(~predicted & (y_true == 1)))
        precision = true_positive / max(1, true_positive + false_positive)
        recall = true_positive / max(1, true_positive + false_negative)
        f1 = 2.0 * precision * recall / max(1e-12, precision + recall)
        fallback.append((f1, float(threshold)))
        if precision >= minimum_precision:
            feasible.append((recall, precision, float(threshold)))
    if feasible:
        return max(feasible, key=lambda item: (item[0], item[1], item[2]))[2]
    return max(fallback, key=lambda item: (item[0], item[1]))[1]


def train_random_forest_pattern_model(
    split: TemporalPatternSplit,
    *,
    minimum_precision: float = 0.70,
    random_state: int = 42690,
    n_estimators: int = 400,
) -> TrainedPatternModel:
    """Fit a balanced random forest and lock its threshold on validation data."""

    try:
        from sklearn.ensemble import RandomForestClassifier
    except ImportError as exc:
        raise RuntimeError("install the project with the 'ml' extra to train pattern models") from exc

    if len(np.unique(split.train.labels)) < 2:
        raise ValueError("training partition must contain positive and negative samples")
    estimator = RandomForestClassifier(
        n_estimators=n_estimators,
        min_samples_leaf=3,
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=random_state,
    )
    estimator.fit(split.train.features, split.train.labels)
    validation_probabilities = _positive_probabilities(estimator, split.validation.features)
    threshold = choose_probability_threshold(
        split.validation.labels,
        validation_probabilities,
        minimum_precision=minimum_precision,
    )
    report = {
        "schema_version": "pattern_training_report.v1",
        "feature_schema": FEATURE_SCHEMA,
        "feature_columns": list(FEATURE_COLUMNS),
        "window_size": split.train.window_size,
        "embargo_samples": split.embargo_samples,
        "decision_threshold": threshold,
        "threshold_selected_on": "validation",
        "minimum_validation_precision": minimum_precision,
        "weak_labels": True,
        "deployment_authorized": False,
        "partitions": {
            "train": _partition_summary(split.train),
            "validation": _evaluate_partition(estimator, split.validation, threshold),
            "test": _evaluate_partition(estimator, split.test, threshold),
        },
    }
    return TrainedPatternModel(estimator=estimator, threshold=threshold, report=report)


def save_pattern_model_artifact(
    trained: TrainedPatternModel,
    *,
    output_dir: str | Path,
    pattern_name: str,
    model_version: str,
    direction: DirectionBias,
    window_size: int,
) -> dict[str, Path]:
    """Persist estimator, metadata, and training diagnostics."""

    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError("install the project with the 'ml' extra to save model artifacts") from exc

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    estimator_path = directory / "model.joblib"
    metadata_path = directory / "metadata.json"
    report_path = directory / "training_report.json"
    joblib.dump(trained.estimator, estimator_path)
    metadata = PatternModelMetadata(
        pattern_name=pattern_name,
        model_version=model_version,
        direction=direction,
        window_size=window_size,
        decision_threshold=trained.threshold,
        feature_schema=FEATURE_SCHEMA,
        estimator_filename=estimator_path.name,
    )
    metadata_path.write_text(json.dumps(metadata.model_dump(mode="json"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = {
        **trained.report,
        "pattern_name": metadata.pattern_name,
        "model_version": model_version,
        "model_sha256": _sha256(estimator_path),
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"estimator": estimator_path, "metadata": metadata_path, "report": report_path}


def _positive_probabilities(estimator: Any, features: np.ndarray) -> np.ndarray:
    probabilities = np.asarray(estimator.predict_proba(features), dtype=float)
    classes = list(estimator.classes_)
    if 1 not in classes:
        raise ValueError("trained estimator does not expose positive class 1")
    return probabilities[:, classes.index(1)]


def _partition_summary(samples: PatternSamples) -> dict[str, Any]:
    return {
        "samples": len(samples.labels),
        "positives": samples.positive_count,
        "negatives": samples.negative_count,
        "positive_rate": samples.positive_count / max(1, len(samples.labels)),
        "first_timestamp": samples.timestamps.min().isoformat(),
        "last_timestamp": samples.timestamps.max().isoformat(),
        "sources": sorted(set(str(source) for source in samples.sources)),
    }


def _evaluate_partition(estimator: Any, samples: PatternSamples, threshold: float) -> dict[str, Any]:
    try:
        from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_fscore_support, roc_auc_score
    except ImportError as exc:
        raise RuntimeError("install the project with the 'ml' extra to evaluate pattern models") from exc

    probabilities = _positive_probabilities(estimator, samples.features)
    predictions = probabilities >= threshold
    precision, recall, f1, _ = precision_recall_fscore_support(samples.labels, predictions, average="binary", zero_division=0)
    summary = _partition_summary(samples)
    summary.update(
        {
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "average_precision": float(average_precision_score(samples.labels, probabilities)) if len(np.unique(samples.labels)) > 1 else None,
            "roc_auc": float(roc_auc_score(samples.labels, probabilities)) if len(np.unique(samples.labels)) > 1 else None,
            "brier_score": float(brier_score_loss(samples.labels, probabilities)),
        }
    )
    return summary


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
