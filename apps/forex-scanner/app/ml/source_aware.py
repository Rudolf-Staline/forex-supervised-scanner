"""Source-aware supervised benchmark for observed and shadow return labels.

Shadow labels may supplement model fitting at a configurable lower weight, but
only observed out-of-fold outcomes can pass the evidence gate. No estimator is
persisted and no runtime decision policy is modified.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.linear_model import LogisticRegression

from app.ml.baseline import (
    CATEGORICAL_FEATURES,
    FEATURES,
    FORBIDDEN_FEATURES,
    MODEL_NAME,
    NUMERIC_FEATURES,
    BaselineConfig,
    _aggregate_coefficients,
    _apply_platt,
    _build_pipeline,
    _classification_metrics,
    _coverage_table,
    _evidence_gate,
    _feature_frame,
    _fold_coefficients,
    _logit,
    _number,
    _target_array,
    _validate_feature_contract,
)
from app.ml.temporal import (
    TemporalFold,
    build_purged_expanding_folds,
    fold_to_dict,
    sorted_rows,
)

SOURCE_AWARE_MODEL_NAME = f"{MODEL_NAME}_source_aware"
SOURCE_AWARE_SCHEMA_VERSION = "source_aware_supervised_report.v1"


@dataclass(frozen=True)
class SourceAwareConfig:
    """Controls for label-source weighting and observed-evidence requirements."""

    baseline: BaselineConfig = BaselineConfig()
    observed_label_weight: float = 1.0
    shadow_label_weight: float = 0.35
    minimum_observed_train_rows: int = 20
    minimum_observed_calibration_rows: int = 5
    minimum_observed_test_rows: int = 5
    minimum_observed_class_count: int = 2
    minimum_observed_oof_rows: int = 40

    def __post_init__(self) -> None:
        if self.observed_label_weight <= 0.0:
            raise ValueError("observed_label_weight must be positive")
        if self.shadow_label_weight <= 0.0:
            raise ValueError("shadow_label_weight must be positive")
        if self.shadow_label_weight > self.observed_label_weight:
            raise ValueError("shadow_label_weight must not exceed observed_label_weight")
        for name in (
            "minimum_observed_train_rows",
            "minimum_observed_calibration_rows",
            "minimum_observed_test_rows",
            "minimum_observed_class_count",
            "minimum_observed_oof_rows",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class SourceAwareResult:
    status: str
    predictions: list[dict[str, object]]
    overall_metrics: dict[str, object]
    observed_metrics: dict[str, object]
    shadow_metrics: dict[str, object]
    source_metrics: list[dict[str, object]]
    coverage: list[dict[str, object]]
    observed_coverage: list[dict[str, object]]
    folds: list[dict[str, object]]
    coefficients: list[dict[str, object]]
    report: dict[str, object]
    manifest: dict[str, object]


def run_source_aware_baseline(
    rows: list[dict[str, object]],
    *,
    config: SourceAwareConfig | None = None,
    dataset_path: Path | None = None,
    dataset_manifest_path: Path | None = None,
) -> SourceAwareResult:
    """Evaluate a weighted mixed-source model with observed-only evidence gating."""

    rules = config or SourceAwareConfig()
    _validate_feature_contract(rows)
    ordered = sorted_rows(_eligible_rows(rows))
    candidate_folds = build_purged_expanding_folds(ordered, rules.baseline.split)
    folds = [fold for fold in candidate_folds if _fold_has_observed_anchor(ordered, fold, rules)]
    if not folds:
        return _empty_result(
            status="insufficient_observed_data",
            reason="no purged temporal fold met the observed-label anchor requirements",
            config=rules,
            ordered=ordered,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )

    predictions: list[dict[str, object]] = []
    coefficient_rows: list[dict[str, object]] = []
    fold_diagnostics: list[dict[str, object]] = []
    seen_decisions: set[str] = set()

    for fold in folds:
        train_rows = [ordered[index] for index in fold.train_indices]
        calibration_rows = [ordered[index] for index in fold.calibration_indices]
        test_rows = [ordered[index] for index in fold.test_indices]
        train_weights = _sample_weights(train_rows, rules)
        calibration_weights = _sample_weights(calibration_rows, rules)

        pipeline = _build_pipeline(rules.baseline)
        train_frame = _feature_frame(train_rows)
        train_target = _target_array(train_rows)
        pipeline.fit(
            train_frame,
            train_target,
            classifier__sample_weight=train_weights,
        )

        calibration_frame = _feature_frame(calibration_rows)
        calibration_target = _target_array(calibration_rows)
        calibration_raw = pipeline.predict_proba(calibration_frame)[:, 1]
        calibrator, calibration_method = _fit_weighted_platt(
            calibration_raw,
            calibration_target,
            calibration_weights,
            rules.baseline,
        )

        test_frame = _feature_frame(test_rows)
        test_target = _target_array(test_rows)
        raw_probability = pipeline.predict_proba(test_frame)[:, 1]
        probability = _apply_platt(calibrator, raw_probability)

        for row, target, raw_value, value in zip(
            test_rows,
            test_target,
            raw_probability,
            probability,
            strict=True,
        ):
            decision_id = str(row.get("decision_id") or "")
            if decision_id in seen_decisions:
                raise AssertionError("OOF decision predicted more than once")
            seen_decisions.add(decision_id)
            source_class = label_source_class(row)
            final_score = _number(row.get("final_score"))
            predictions.append(
                {
                    "decision_id": decision_id,
                    "decision_timestamp": row.get("decision_timestamp"),
                    "fold_index": fold.fold_index,
                    "normalized_symbol": row.get("normalized_symbol"),
                    "setup_family": row.get("setup_family"),
                    "setup_subtype": row.get("setup_subtype"),
                    "target_positive_r": bool(target),
                    "realized_r": _number(row.get("realized_r")),
                    "model_probability": round(float(value), 10),
                    "uncalibrated_model_probability": round(float(raw_value), 10),
                    "score_baseline_probability": (
                        round(min(1.0, max(0.0, final_score / 100.0)), 10)
                        if final_score is not None
                        else 0.5
                    ),
                    "calibration_method": calibration_method,
                    "label_source": row.get("label_source"),
                    "label_source_class": source_class,
                    "shadow_counterfactual": source_class == "shadow",
                    "training_weight_policy": (
                        rules.shadow_label_weight
                        if source_class == "shadow"
                        else rules.observed_label_weight
                    ),
                }
            )

        diagnostic = fold_to_dict(fold)
        diagnostic.update(
            {
                "train_rows_by_source": _source_counts(train_rows),
                "calibration_rows_by_source": _source_counts(calibration_rows),
                "test_rows_by_source": _source_counts(test_rows),
                "train_effective_weight": round(float(np.sum(train_weights)), 6),
                "calibration_effective_weight": round(float(np.sum(calibration_weights)), 6),
                "calibration_method": calibration_method,
            }
        )
        fold_diagnostics.append(diagnostic)
        coefficient_rows.extend(_fold_coefficients(pipeline, fold.fold_index))

    predictions.sort(key=lambda row: (str(row["decision_timestamp"]), str(row["decision_id"])))
    overall = _prediction_metrics(predictions, rules.baseline.calibration_bins)
    observed_predictions = [
        row for row in predictions if row.get("label_source_class") == "observed"
    ]
    shadow_predictions = [
        row for row in predictions if row.get("label_source_class") == "shadow"
    ]
    observed = _prediction_metrics(observed_predictions, rules.baseline.calibration_bins)
    shadow = _prediction_metrics(shadow_predictions, rules.baseline.calibration_bins)

    coverage = _prediction_coverage(predictions, rules.baseline.coverage_levels, "model_all_sources")
    observed_coverage = _prediction_coverage(
        observed_predictions,
        rules.baseline.coverage_levels,
        "model_observed_only",
    )
    observed_baseline_coverage = _prediction_coverage(
        observed_predictions,
        rules.baseline.coverage_levels,
        "score_baseline_observed_only",
        probability_key="score_baseline_probability",
    )
    evidence_gate = _evidence_gate(
        predictions=observed_predictions,
        metrics=observed["model"],
        baseline_metrics=observed["score_baseline"],
        coverage=observed_coverage,
        minimum_oof_rows=rules.minimum_observed_oof_rows,
    )
    evidence_gate.update(
        {
            "validation_basis": "observed_oof_only",
            "observed_oof_rows": len(observed_predictions),
            "shadow_oof_rows_excluded_from_gate": len(shadow_predictions),
            "promotion_authorized": False,
        }
    )
    status = (
        "evaluated"
        if len(observed_predictions) >= rules.minimum_observed_oof_rows
        else "insufficient_observed_oof"
    )
    source_metrics = [
        {"source": "observed", **observed},
        {"source": "shadow", **shadow},
    ]
    report = {
        "schema_version": SOURCE_AWARE_SCHEMA_VERSION,
        "model": SOURCE_AWARE_MODEL_NAME,
        "status": status,
        "deployment_authorized": False,
        "automatic_threshold_update": False,
        "oof_rows": len(predictions),
        "observed_oof_rows": len(observed_predictions),
        "shadow_oof_rows": len(shadow_predictions),
        "folds": len(folds),
        "label_weights": {
            "observed": rules.observed_label_weight,
            "shadow": rules.shadow_label_weight,
        },
        "overall_metrics": overall,
        "observed_metrics": observed,
        "shadow_metrics": shadow,
        "coverage": coverage,
        "observed_coverage": observed_coverage,
        "observed_score_baseline_coverage": observed_baseline_coverage,
        "evidence_gate": evidence_gate,
        "limitations": [
            "shadow labels are historical counterfactual simulations, not observed fills",
            "shadow rows are down-weighted but can still influence fitted coefficients",
            "only observed OOF rows can pass the evidence gate",
            "a final locked calendar holdout and forward paper evidence remain mandatory",
            "this report cannot deploy a model or change scanner thresholds",
        ],
    }
    manifest = _manifest(
        config=rules,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        input_rows=len(ordered),
        predictions=predictions,
        folds=folds,
    )
    return SourceAwareResult(
        status=status,
        predictions=predictions,
        overall_metrics=overall,
        observed_metrics=observed,
        shadow_metrics=shadow,
        source_metrics=source_metrics,
        coverage=coverage,
        observed_coverage=observed_coverage,
        folds=fold_diagnostics,
        coefficients=_aggregate_coefficients(coefficient_rows),
        report=report,
        manifest=manifest,
    )


def label_source_class(row: dict[str, object]) -> str:
    """Map a dataset row to observed or counterfactual evidence."""

    if bool(row.get("shadow_counterfactual")):
        return "shadow"
    if str(row.get("label_source") or "").strip().lower() == "shadow_historical":
        return "shadow"
    return "observed"


def write_source_aware_result(
    result: SourceAwareResult,
    output_dir: Path,
) -> dict[str, Path]:
    """Write diagnostics and OOF predictions; never persist an estimator."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "report_json": output_dir / "source_aware_supervised_report.json",
        "report_txt": output_dir / "source_aware_supervised_report.txt",
        "predictions_jsonl": output_dir / "source_aware_oof_predictions.jsonl",
        "predictions_csv": output_dir / "source_aware_oof_predictions.csv",
        "folds": output_dir / "source_aware_temporal_folds.json",
        "coefficients": output_dir / "source_aware_coefficient_stability.csv",
        "manifest": output_dir / "source_aware_experiment_manifest.json",
    }
    paths["report_json"].write_text(
        json.dumps(result.report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["report_txt"].write_text(report_to_text(result), encoding="utf-8")
    _write_jsonl(paths["predictions_jsonl"], result.predictions)
    _write_csv(paths["predictions_csv"], result.predictions)
    paths["folds"].write_text(
        json.dumps(result.folds, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(paths["coefficients"], result.coefficients)
    paths["manifest"].write_text(
        json.dumps(result.manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return paths


def report_to_text(result: SourceAwareResult) -> str:
    gate = result.report.get("evidence_gate", {})
    observed = result.observed_metrics.get("model", {})
    observed_baseline = result.observed_metrics.get("score_baseline", {})
    lines = [
        "Source-Aware Supervised Forex Baseline (research-only)",
        "=======================================================",
        f"status                    : {result.status}",
        f"OOF rows                  : {len(result.predictions)}",
        f"observed OOF rows         : {result.report.get('observed_oof_rows', 0)}",
        f"shadow OOF rows           : {result.report.get('shadow_oof_rows', 0)}",
        f"observed label weight     : {result.report['label_weights']['observed']}",
        f"shadow label weight       : {result.report['label_weights']['shadow']}",
        "deployment authorized     : false",
        "automatic threshold update: false",
        "",
        "Observed-only OOF quality:",
        f"  ROC AUC model           : {_fmt(observed.get('roc_auc'))}",
        f"  ROC AUC score baseline  : {_fmt(observed_baseline.get('roc_auc'))}",
        f"  Brier model             : {_fmt(observed.get('brier'))}",
        f"  Brier score baseline    : {_fmt(observed_baseline.get('brier'))}",
        "",
        "Evidence gate:",
        f"  basis                    : {gate.get('validation_basis', '')}",
        f"  result                   : {gate.get('result', 'not_evaluated')}",
        f"  reason                   : {gate.get('reason', '')}",
        "",
        "Observed-only coverage / expectancy:",
    ]
    for item in result.observed_coverage:
        lines.append(
            f"  top {float(item['coverage']):.0%}: n={item['rows']} "
            f"expectancy={_fmt(item.get('expectancy_r'))}R "
            f"positive_rate={_fmt(item.get('positive_rate'))}"
        )
    lines.extend(
        [
            "",
            "Shadow labels may assist fitting but cannot authorize promotion.",
            "A locked holdout and observed forward paper outcomes remain mandatory.",
            "",
        ]
    )
    return "\n".join(lines)


def _fold_has_observed_anchor(
    rows: list[dict[str, object]],
    fold: TemporalFold,
    config: SourceAwareConfig,
) -> bool:
    train = [rows[index] for index in fold.train_indices]
    calibration = [rows[index] for index in fold.calibration_indices]
    test = [rows[index] for index in fold.test_indices]
    observed_train = [row for row in train if label_source_class(row) == "observed"]
    observed_calibration = [row for row in calibration if label_source_class(row) == "observed"]
    observed_test = [row for row in test if label_source_class(row) == "observed"]
    if len(observed_train) < config.minimum_observed_train_rows:
        return False
    if len(observed_calibration) < config.minimum_observed_calibration_rows:
        return False
    if len(observed_test) < config.minimum_observed_test_rows:
        return False
    train_counts = _class_counts(observed_train)
    calibration_counts = _class_counts(observed_calibration)
    if min(train_counts.values()) < config.minimum_observed_class_count:
        return False
    if min(calibration_counts.values()) < 1:
        return False
    return True


def _fit_weighted_platt(
    raw_probability: np.ndarray,
    target: np.ndarray,
    sample_weight: np.ndarray,
    baseline: BaselineConfig,
) -> tuple[LogisticRegression | None, str]:
    if len(np.unique(target)) < 2:
        return None, "identity_single_class_calibration"
    calibrator = LogisticRegression(
        C=baseline.platt_regularization_c,
        solver="lbfgs",
        max_iter=baseline.max_iterations,
        random_state=0,
    )
    calibrator.fit(
        _logit(raw_probability).reshape(-1, 1),
        target,
        sample_weight=sample_weight,
    )
    return calibrator, "weighted_platt_independent_temporal_block"


def _sample_weights(
    rows: list[dict[str, object]],
    config: SourceAwareConfig,
) -> np.ndarray:
    return np.asarray(
        [
            config.shadow_label_weight
            if label_source_class(row) == "shadow"
            else config.observed_label_weight
            for row in rows
        ],
        dtype=float,
    )


def _prediction_metrics(
    predictions: list[dict[str, object]],
    bins: int,
) -> dict[str, object]:
    if not predictions:
        return {"rows": 0, "model": {}, "score_baseline": {}, "expectancy_r": None}
    target = np.asarray([bool(row["target_positive_r"]) for row in predictions], dtype=int)
    model_probability = np.asarray([float(row["model_probability"]) for row in predictions])
    baseline_probability = np.asarray(
        [float(row["score_baseline_probability"]) for row in predictions]
    )
    realized = [
        float(row["realized_r"])
        for row in predictions
        if row.get("realized_r") is not None
    ]
    return {
        "rows": len(predictions),
        "model": _classification_metrics(target, model_probability, bins=bins),
        "score_baseline": _classification_metrics(target, baseline_probability, bins=bins),
        "expectancy_r": round(float(np.mean(realized)), 6) if realized else None,
    }


def _prediction_coverage(
    predictions: list[dict[str, object]],
    levels: tuple[float, ...],
    name: str,
    *,
    probability_key: str = "model_probability",
) -> list[dict[str, object]]:
    if not predictions:
        return []
    probabilities = np.asarray([float(row[probability_key]) for row in predictions])
    target = np.asarray([bool(row["target_positive_r"]) for row in predictions], dtype=int)
    realized = np.asarray(
        [
            float(row["realized_r"])
            if row.get("realized_r") is not None
            else np.nan
            for row in predictions
        ]
    )
    return _coverage_table(
        probabilities=probabilities,
        targets=target,
        realized_r=realized,
        levels=levels,
        name=name,
    )


def _source_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    return {
        "observed": sum(label_source_class(row) == "observed" for row in rows),
        "shadow": sum(label_source_class(row) == "shadow" for row in rows),
    }


def _class_counts(rows: list[dict[str, object]]) -> dict[bool, int]:
    return {
        False: sum(row.get("target_positive_r") is False for row in rows),
        True: sum(row.get("target_positive_r") is True for row in rows),
    }


def _eligible_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        row
        for row in rows
        if row.get("target_positive_r") in {True, False}
        and bool(row.get("is_trainable_candidate", True))
        and row.get("decision_timestamp")
    ]


def _manifest(
    *,
    config: SourceAwareConfig,
    dataset_path: Path | None,
    dataset_manifest_path: Path | None,
    input_rows: int,
    predictions: list[dict[str, object]],
    folds: list[TemporalFold],
) -> dict[str, object]:
    return {
        "schema_version": SOURCE_AWARE_SCHEMA_VERSION,
        "model": SOURCE_AWARE_MODEL_NAME,
        "sklearn_version": sklearn_version,
        "config": {
            **asdict(config),
            "baseline": {
                **asdict(config.baseline),
                "split": asdict(config.baseline.split),
            },
        },
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "forbidden_features": sorted(FORBIDDEN_FEATURES),
        "dataset_path": str(dataset_path) if dataset_path else None,
        "dataset_sha256": _file_sha256(dataset_path),
        "dataset_manifest_path": str(dataset_manifest_path) if dataset_manifest_path else None,
        "dataset_manifest_sha256": _file_sha256(dataset_manifest_path),
        "input_rows": input_rows,
        "oof_rows": len(predictions),
        "oof_rows_by_source": {
            "observed": sum(row.get("label_source_class") == "observed" for row in predictions),
            "shadow": sum(row.get("label_source_class") == "shadow" for row in predictions),
        },
        "folds": [fold_to_dict(fold) for fold in folds],
        "model_binary_written": False,
        "deployment_authorized": False,
        "evidence_policy": {
            "training_uses_shadow": True,
            "shadow_training_weight": config.shadow_label_weight,
            "observed_training_weight": config.observed_label_weight,
            "promotion_gate_uses_observed_oof_only": True,
            "minimum_observed_oof_rows": config.minimum_observed_oof_rows,
        },
        "validation_policy": {
            "random_split": False,
            "expanding_window": True,
            "independent_calibration_block": True,
            "label_availability_purge": True,
            "observed_anchor_required_in_train_calibration_test": True,
            "embargo_minutes": config.baseline.split.embargo_minutes,
        },
    }


def _empty_result(
    *,
    status: str,
    reason: str,
    config: SourceAwareConfig,
    ordered: list[dict[str, object]],
    dataset_path: Path | None,
    dataset_manifest_path: Path | None,
) -> SourceAwareResult:
    report = {
        "schema_version": SOURCE_AWARE_SCHEMA_VERSION,
        "model": SOURCE_AWARE_MODEL_NAME,
        "status": status,
        "deployment_authorized": False,
        "automatic_threshold_update": False,
        "oof_rows": 0,
        "observed_oof_rows": 0,
        "shadow_oof_rows": 0,
        "folds": 0,
        "label_weights": {
            "observed": config.observed_label_weight,
            "shadow": config.shadow_label_weight,
        },
        "overall_metrics": {},
        "observed_metrics": {},
        "shadow_metrics": {},
        "coverage": [],
        "observed_coverage": [],
        "evidence_gate": {
            "result": status,
            "reason": reason,
            "validation_basis": "observed_oof_only",
            "promotion_authorized": False,
        },
        "limitations": [reason],
    }
    manifest = _manifest(
        config=config,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        input_rows=len(ordered),
        predictions=[],
        folds=[],
    )
    return SourceAwareResult(
        status=status,
        predictions=[],
        overall_metrics={},
        observed_metrics={},
        shadow_metrics={},
        source_metrics=[],
        coverage=[],
        observed_coverage=[],
        folds=[],
        coefficients=[],
        report=report,
        manifest=manifest,
    )


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        if not fieldnames:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: object) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:.4f}"
