"""Research-only supervised baseline with purged temporal OOF evaluation.

The baseline predicts whether a labeled candidate finishes above 0R. It uses an
explicit decision-time feature allowlist, expanding chronological folds, an
independent calibration segment, and no model persistence or deployment path.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn import __version__ as sklearn_version
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.ml.temporal import (
    TemporalFold,
    TemporalSplitConfig,
    build_purged_expanding_folds,
    fold_to_dict,
    sorted_rows,
)

MODEL_NAME = "regularized_logistic_platt_temporal_oof"
REPORT_SCHEMA_VERSION = "supervised_baseline_report.v1"

NUMERIC_FEATURES = [
    "technical_score",
    "execution_score",
    "context_score",
    "empirical_score",
    "final_score",
    "pattern_score",
    "activation_quality",
    "invalidation_quality",
    "risk_reward",
    "required_min_rr",
    "spread_atr_ratio",
    "data_quality_score",
    "base_min_score",
    "adaptive_min_score",
    "effective_min_score",
]

CATEGORICAL_FEATURES = [
    "normalized_symbol",
    "style",
    "setup_family",
    "setup_subtype",
    "direction",
    "scanner_status",
    "regime",
    "htf_regime",
    "entry_regime",
    "trigger_regime",
    "session",
    "provider",
    "score_band",
]

FEATURES = [*NUMERIC_FEATURES, *CATEGORICAL_FEATURES]
FORBIDDEN_FEATURES = {
    "target_positive_r",
    "target_non_negative_r",
    "realized_r",
    "realized_pnl",
    "return_label_available",
    "activation_label_available",
    "activated",
    "order_status",
    "outcome_status",
    "close_reason",
    "mae",
    "mfe",
    "time_in_trade_minutes",
    "bars_to_activation",
    "label_source",
    "label_timestamp",
}


@dataclass(frozen=True)
class BaselineConfig:
    """Model, calibration, evaluation, and evidence-gate controls."""

    split: TemporalSplitConfig = TemporalSplitConfig()
    regularization_c: float = 1.0
    max_iterations: int = 2_000
    class_weight_balanced: bool = True
    platt_regularization_c: float = 100.0
    calibration_bins: int = 10
    minimum_oof_rows: int = 80
    coverage_levels: tuple[float, ...] = (0.10, 0.25, 0.50, 1.00)

    def __post_init__(self) -> None:
        if self.regularization_c <= 0.0:
            raise ValueError("regularization_c must be positive")
        if self.max_iterations < 100:
            raise ValueError("max_iterations must be at least 100")
        if self.platt_regularization_c <= 0.0:
            raise ValueError("platt_regularization_c must be positive")
        if self.calibration_bins < 2:
            raise ValueError("calibration_bins must be at least 2")
        if self.minimum_oof_rows < 1:
            raise ValueError("minimum_oof_rows must be positive")
        if not self.coverage_levels:
            raise ValueError("coverage_levels must not be empty")
        if any(level <= 0.0 or level > 1.0 for level in self.coverage_levels):
            raise ValueError("coverage levels must be in (0, 1]")
        if FORBIDDEN_FEATURES & set(FEATURES):
            raise ValueError("label fields must never appear in the feature allowlist")


@dataclass(frozen=True)
class BaselineResult:
    """OOF predictions, metrics, diagnostics, and immutable experiment metadata."""

    status: str
    predictions: list[dict[str, object]]
    metrics: dict[str, object]
    baseline_metrics: dict[str, object]
    coverage: list[dict[str, object]]
    baseline_coverage: list[dict[str, object]]
    family_metrics: list[dict[str, object]]
    folds: list[dict[str, object]]
    coefficients: list[dict[str, object]]
    report: dict[str, object]
    manifest: dict[str, object]


def load_training_rows(path: Path) -> list[dict[str, object]]:
    """Load JSONL rows with known binary return targets."""

    rows: list[dict[str, object]] = []
    if not path.is_file():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"training row at {path}:{line_number} must be a JSON object")
        if payload.get("target_positive_r") not in {True, False}:
            continue
        if not bool(payload.get("is_trainable_candidate", True)):
            continue
        rows.append(payload)
    return rows


def run_supervised_baseline(
    rows: list[dict[str, object]],
    *,
    config: BaselineConfig | None = None,
    dataset_path: Path | None = None,
    dataset_manifest_path: Path | None = None,
) -> BaselineResult:
    """Evaluate the logistic baseline strictly out of sample.

    The fitted fold models and calibrators exist only in memory. The returned
    artifacts contain predictions and diagnostics, never a deployable estimator.
    """

    rules = config or BaselineConfig()
    _validate_feature_contract(rows)
    ordered = sorted_rows(_eligible_rows(rows))
    folds = build_purged_expanding_folds(ordered, rules.split)
    if not folds:
        return _empty_result(
            status="insufficient_data",
            config=rules,
            ordered=ordered,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
            reason="no valid purged temporal fold could be constructed",
        )

    predictions: list[dict[str, object]] = []
    coefficient_rows: list[dict[str, object]] = []
    seen_decisions: set[str] = set()
    fold_diagnostics: list[dict[str, object]] = []

    for fold in folds:
        train_rows = [ordered[index] for index in fold.train_indices]
        calibration_rows = [ordered[index] for index in fold.calibration_indices]
        test_rows = [ordered[index] for index in fold.test_indices]

        pipeline = _build_pipeline(rules)
        train_frame = _feature_frame(train_rows)
        train_target = _target_array(train_rows)
        pipeline.fit(train_frame, train_target)

        calibration_frame = _feature_frame(calibration_rows)
        calibration_target = _target_array(calibration_rows)
        calibration_raw = pipeline.predict_proba(calibration_frame)[:, 1]
        platt, calibration_method = _fit_platt_scaler(
            calibration_raw,
            calibration_target,
            rules,
        )

        test_frame = _feature_frame(test_rows)
        test_target = _target_array(test_rows)
        raw_model_probability = pipeline.predict_proba(test_frame)[:, 1]
        calibrated_probability = _apply_platt(platt, raw_model_probability)

        for row, target, raw_probability, probability in zip(
            test_rows,
            test_target,
            raw_model_probability,
            calibrated_probability,
            strict=True,
        ):
            decision_id = str(row.get("decision_id", ""))
            if decision_id in seen_decisions:
                raise AssertionError("OOF decision predicted more than once")
            seen_decisions.add(decision_id)
            final_score = _number(row.get("final_score"))
            prediction = {
                "decision_id": decision_id,
                "decision_timestamp": row.get("decision_timestamp"),
                "fold_index": fold.fold_index,
                "normalized_symbol": row.get("normalized_symbol"),
                "setup_family": row.get("setup_family"),
                "setup_subtype": row.get("setup_subtype"),
                "target_positive_r": bool(target),
                "realized_r": _number(row.get("realized_r")),
                "model_probability": round(float(probability), 10),
                "uncalibrated_model_probability": round(float(raw_probability), 10),
                "score_baseline_probability": (
                    round(min(1.0, max(0.0, final_score / 100.0)), 10)
                    if final_score is not None
                    else 0.5
                ),
                "calibration_method": calibration_method,
            }
            predictions.append(prediction)

        fold_diagnostic = fold_to_dict(fold)
        fold_diagnostic.update(
            {
                "train_positive_rate": round(float(np.mean(train_target)), 6),
                "calibration_positive_rate": round(float(np.mean(calibration_target)), 6),
                "test_positive_rate": round(float(np.mean(test_target)), 6),
                "calibration_method": calibration_method,
                "test_metrics": _classification_metrics(
                    test_target,
                    calibrated_probability,
                    bins=rules.calibration_bins,
                ),
            }
        )
        fold_diagnostics.append(fold_diagnostic)
        coefficient_rows.extend(_fold_coefficients(pipeline, fold.fold_index))

    predictions.sort(key=lambda row: (str(row["decision_timestamp"]), str(row["decision_id"])))
    targets = np.asarray([bool(row["target_positive_r"]) for row in predictions], dtype=int)
    model_probabilities = np.asarray([float(row["model_probability"]) for row in predictions])
    baseline_probabilities = np.asarray(
        [float(row["score_baseline_probability"]) for row in predictions]
    )
    realized_r = np.asarray(
        [float(row["realized_r"]) if row["realized_r"] is not None else np.nan for row in predictions]
    )

    metrics = _classification_metrics(targets, model_probabilities, bins=rules.calibration_bins)
    baseline_metrics = _classification_metrics(
        targets,
        baseline_probabilities,
        bins=rules.calibration_bins,
    )
    coverage = _coverage_table(
        probabilities=model_probabilities,
        targets=targets,
        realized_r=realized_r,
        levels=rules.coverage_levels,
        name="model",
    )
    baseline_coverage = _coverage_table(
        probabilities=baseline_probabilities,
        targets=targets,
        realized_r=realized_r,
        levels=rules.coverage_levels,
        name="score_baseline",
    )
    family_metrics = _family_metrics(predictions, rules.calibration_bins)
    coefficients = _aggregate_coefficients(coefficient_rows)
    evidence_gate = _evidence_gate(
        predictions=predictions,
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        coverage=coverage,
        minimum_oof_rows=rules.minimum_oof_rows,
    )
    status = "evaluated" if len(predictions) >= rules.minimum_oof_rows else "insufficient_oof"
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model": MODEL_NAME,
        "status": status,
        "deployment_authorized": False,
        "automatic_threshold_update": False,
        "oof_rows": len(predictions),
        "folds": len(folds),
        "metrics": metrics,
        "score_baseline_metrics": baseline_metrics,
        "coverage": coverage,
        "score_baseline_coverage": baseline_coverage,
        "family_metrics": family_metrics,
        "evidence_gate": evidence_gate,
        "limitations": [
            "labels exist primarily for paper-executed candidates, creating selection bias",
            "rejected decisions lack counterfactual return labels",
            "paper fills are not guaranteed broker fills",
            "row-count folds do not replace a final locked calendar holdout",
            "this report cannot deploy a model or change scanner thresholds",
        ],
    }
    manifest = _manifest(
        config=rules,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        input_rows=len(ordered),
        oof_rows=len(predictions),
        folds=folds,
    )
    return BaselineResult(
        status=status,
        predictions=predictions,
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        coverage=coverage,
        baseline_coverage=baseline_coverage,
        family_metrics=family_metrics,
        folds=fold_diagnostics,
        coefficients=coefficients,
        report=report,
        manifest=manifest,
    )


def write_baseline_result(result: BaselineResult, output_dir: Path) -> dict[str, Path]:
    """Write reports and OOF predictions; never write a model binary."""

    output_dir.mkdir(parents=True, exist_ok=True)
    report_json = output_dir / "supervised_baseline_report.json"
    report_txt = output_dir / "supervised_baseline_report.txt"
    predictions_jsonl = output_dir / "supervised_oof_predictions.jsonl"
    predictions_csv = output_dir / "supervised_oof_predictions.csv"
    folds_json = output_dir / "supervised_temporal_folds.json"
    coefficients_csv = output_dir / "supervised_coefficient_stability.csv"
    manifest_json = output_dir / "supervised_experiment_manifest.json"

    report_json.write_text(
        json.dumps(result.report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_txt.write_text(report_to_text(result), encoding="utf-8")
    _write_jsonl(predictions_jsonl, result.predictions)
    _write_csv(predictions_csv, result.predictions)
    folds_json.write_text(
        json.dumps(result.folds, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(coefficients_csv, result.coefficients)
    manifest_json.write_text(
        json.dumps(result.manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report_json": report_json,
        "report_txt": report_txt,
        "predictions_jsonl": predictions_jsonl,
        "predictions_csv": predictions_csv,
        "folds": folds_json,
        "coefficients": coefficients_csv,
        "manifest": manifest_json,
    }


def report_to_text(result: BaselineResult) -> str:
    """Render a concise, decision-oriented research summary."""

    report = result.report
    metrics = result.metrics
    baseline = result.baseline_metrics
    gate = report.get("evidence_gate", {})
    lines = [
        "Supervised Forex Meta-Filter Baseline (research-only)",
        "======================================================",
        f"status                    : {result.status}",
        f"model                     : {MODEL_NAME}",
        f"temporal folds            : {len(result.folds)}",
        f"OOF predictions           : {len(result.predictions)}",
        "deployment authorized     : false",
        "automatic threshold update: false",
        "",
        "OOF probability quality:",
        f"  ROC AUC model           : {_fmt(metrics.get('roc_auc'))}",
        f"  ROC AUC score baseline  : {_fmt(baseline.get('roc_auc'))}",
        f"  average precision model : {_fmt(metrics.get('average_precision'))}",
        f"  average precision score : {_fmt(baseline.get('average_precision'))}",
        f"  Brier model             : {_fmt(metrics.get('brier'))}",
        f"  Brier score baseline    : {_fmt(baseline.get('brier'))}",
        f"  log loss model          : {_fmt(metrics.get('log_loss'))}",
        f"  log loss score baseline : {_fmt(baseline.get('log_loss'))}",
        "",
        "Evidence gate:",
        f"  result                   : {gate.get('result', 'not_evaluated')}",
        f"  reason                   : {gate.get('reason', '')}",
        "",
        "Coverage / realized expectancy:",
    ]
    for item in result.coverage:
        lines.append(
            f"  top {float(item['coverage']):.0%}: n={item['rows']} "
            f"expectancy={_fmt(item.get('expectancy_r'))}R "
            f"positive_rate={_fmt(item.get('positive_rate'))}"
        )
    lines.extend(
        [
            "",
            "Important: this is an OOF benchmark, not a trading authorization. A locked",
            "calendar holdout and forward paper evidence remain mandatory before promotion.",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_pipeline(config: BaselineConfig) -> Pipeline:
    numeric = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("one_hot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    preprocessing = ColumnTransformer(
        transformers=[
            ("numeric", numeric, NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
        verbose_feature_names_out=True,
    )
    classifier = LogisticRegression(
        C=config.regularization_c,
        class_weight="balanced" if config.class_weight_balanced else None,
        max_iter=config.max_iterations,
        solver="lbfgs",
        random_state=0,
    )
    return Pipeline(steps=[("features", preprocessing), ("classifier", classifier)])


def _fit_platt_scaler(
    raw_probability: np.ndarray,
    target: np.ndarray,
    config: BaselineConfig,
) -> tuple[LogisticRegression | None, str]:
    if len(np.unique(target)) < 2:
        return None, "identity_single_class_calibration"
    logits = _logit(raw_probability).reshape(-1, 1)
    calibrator = LogisticRegression(
        C=config.platt_regularization_c,
        solver="lbfgs",
        max_iter=config.max_iterations,
        random_state=0,
    )
    calibrator.fit(logits, target)
    return calibrator, "platt_independent_temporal_block"


def _apply_platt(
    calibrator: LogisticRegression | None,
    raw_probability: np.ndarray,
) -> np.ndarray:
    clipped = np.clip(raw_probability, 1e-6, 1.0 - 1e-6)
    if calibrator is None:
        return clipped
    return calibrator.predict_proba(_logit(clipped).reshape(-1, 1))[:, 1]


def _classification_metrics(
    target: np.ndarray,
    probability: np.ndarray,
    *,
    bins: int,
) -> dict[str, object]:
    target = np.asarray(target, dtype=int)
    probability = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    both_classes = len(np.unique(target)) == 2
    return {
        "rows": int(len(target)),
        "positive_rate": round(float(np.mean(target)), 6) if len(target) else None,
        "roc_auc": round(float(roc_auc_score(target, probability)), 6) if both_classes else None,
        "average_precision": (
            round(float(average_precision_score(target, probability)), 6)
            if both_classes
            else None
        ),
        "brier": round(float(brier_score_loss(target, probability)), 6) if len(target) else None,
        "log_loss": (
            round(float(log_loss(target, probability, labels=[0, 1])), 6)
            if len(target)
            else None
        ),
        "accuracy_at_0_5": (
            round(float(accuracy_score(target, probability >= 0.5)), 6)
            if len(target)
            else None
        ),
        "expected_calibration_error": _expected_calibration_error(target, probability, bins),
        "probability_target_spearman": _spearman(probability, target),
    }


def _expected_calibration_error(
    target: np.ndarray,
    probability: np.ndarray,
    bins: int,
) -> float | None:
    if not len(target):
        return None
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(len(target))
    value = 0.0
    for index in range(bins):
        left, right = edges[index], edges[index + 1]
        mask = (
            (probability >= left) & (probability < right)
            if index < bins - 1
            else (probability >= left) & (probability <= right)
        )
        count = int(mask.sum())
        if count == 0:
            continue
        value += (count / total) * abs(float(np.mean(probability[mask])) - float(np.mean(target[mask])))
    return round(value, 6)


def _coverage_table(
    *,
    probabilities: np.ndarray,
    targets: np.ndarray,
    realized_r: np.ndarray,
    levels: tuple[float, ...],
    name: str,
) -> list[dict[str, object]]:
    if not len(probabilities):
        return []
    order = np.argsort(-probabilities, kind="stable")
    rows: list[dict[str, object]] = []
    for level in sorted(set(levels)):
        count = max(1, int(math.ceil(len(order) * level)))
        selected = order[:count]
        selected_r = realized_r[selected]
        finite_r = selected_r[np.isfinite(selected_r)]
        rows.append(
            {
                "ranking": name,
                "coverage": level,
                "rows": count,
                "probability_floor": round(float(probabilities[selected[-1]]), 6),
                "positive_rate": round(float(np.mean(targets[selected])), 6),
                "expectancy_r": round(float(np.mean(finite_r)), 6) if len(finite_r) else None,
                "cumulative_r": round(float(np.sum(finite_r)), 6) if len(finite_r) else None,
            }
        )
    return rows


def _family_metrics(
    predictions: list[dict[str, object]],
    bins: int,
) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for row in predictions:
        groups.setdefault(str(row.get("setup_family") or "missing"), []).append(row)
    result: list[dict[str, object]] = []
    for family, rows in sorted(groups.items()):
        target = np.asarray([bool(row["target_positive_r"]) for row in rows], dtype=int)
        probability = np.asarray([float(row["model_probability"]) for row in rows])
        realized = [float(row["realized_r"]) for row in rows if row["realized_r"] is not None]
        metrics = _classification_metrics(target, probability, bins=bins)
        metrics.update(
            {
                "setup_family": family,
                "expectancy_r": round(float(np.mean(realized)), 6) if realized else None,
            }
        )
        result.append(metrics)
    return result


def _fold_coefficients(pipeline: Pipeline, fold_index: int) -> list[dict[str, object]]:
    transformer = pipeline.named_steps["features"]
    classifier = pipeline.named_steps["classifier"]
    names = transformer.get_feature_names_out()
    coefficients = classifier.coef_[0]
    return [
        {
            "fold_index": fold_index,
            "feature": str(name),
            "coefficient": float(coefficient),
        }
        for name, coefficient in zip(names, coefficients, strict=True)
    ]


def _aggregate_coefficients(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(str(row["feature"]), []).append(float(row["coefficient"]))
    result = []
    for feature, values in grouped.items():
        array = np.asarray(values)
        result.append(
            {
                "feature": feature,
                "folds_present": len(values),
                "mean_coefficient": round(float(np.mean(array)), 8),
                "mean_abs_coefficient": round(float(np.mean(np.abs(array))), 8),
                "coefficient_std": round(float(np.std(array)), 8),
                "sign_consistency": round(float(max(np.mean(array >= 0), np.mean(array <= 0))), 6),
            }
        )
    result.sort(key=lambda item: (-float(item["mean_abs_coefficient"]), str(item["feature"])))
    return result


def _evidence_gate(
    *,
    predictions: list[dict[str, object]],
    metrics: dict[str, object],
    baseline_metrics: dict[str, object],
    coverage: list[dict[str, object]],
    minimum_oof_rows: int,
) -> dict[str, object]:
    if len(predictions) < minimum_oof_rows:
        return {
            "result": "insufficient_data",
            "reason": f"{len(predictions)} OOF rows is below minimum {minimum_oof_rows}",
        }
    model_brier = _number(metrics.get("brier"))
    baseline_brier = _number(baseline_metrics.get("brier"))
    model_auc = _number(metrics.get("roc_auc"))
    baseline_auc = _number(baseline_metrics.get("roc_auc"))
    top_quarter = min(coverage, key=lambda item: abs(float(item["coverage"]) - 0.25))
    full = min(coverage, key=lambda item: abs(float(item["coverage"]) - 1.0))
    improves_brier = (
        model_brier is not None and baseline_brier is not None and model_brier < baseline_brier
    )
    improves_auc = (
        model_auc is not None and baseline_auc is not None and model_auc > baseline_auc
    )
    top_expectancy = _number(top_quarter.get("expectancy_r"))
    full_expectancy = _number(full.get("expectancy_r"))
    improves_ranking = (
        top_expectancy is not None
        and full_expectancy is not None
        and top_expectancy > full_expectancy
    )
    passes = improves_brier and improves_auc and improves_ranking
    return {
        "result": "passes_baseline_screen" if passes else "fails_baseline_screen",
        "reason": (
            "requires simultaneous improvement in Brier, ROC AUC, and top-quartile expectancy"
        ),
        "checks": {
            "brier_improves_over_score": improves_brier,
            "roc_auc_improves_over_score": improves_auc,
            "top_quartile_expectancy_above_full_sample": improves_ranking,
        },
        "promotion_authorized": False,
    }


def _manifest(
    *,
    config: BaselineConfig,
    dataset_path: Path | None,
    dataset_manifest_path: Path | None,
    input_rows: int,
    oof_rows: int,
    folds: list[TemporalFold],
) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model": MODEL_NAME,
        "sklearn_version": sklearn_version,
        "config": {
            **asdict(config),
            "split": asdict(config.split),
        },
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "forbidden_features": sorted(FORBIDDEN_FEATURES),
        "dataset_path": str(dataset_path) if dataset_path else None,
        "dataset_sha256": _file_sha256(dataset_path),
        "dataset_manifest_path": str(dataset_manifest_path) if dataset_manifest_path else None,
        "dataset_manifest_sha256": _file_sha256(dataset_manifest_path),
        "input_rows": input_rows,
        "oof_rows": oof_rows,
        "folds": [fold_to_dict(fold) for fold in folds],
        "model_binary_written": False,
        "deployment_authorized": False,
        "validation_policy": {
            "random_split": False,
            "expanding_window": True,
            "independent_calibration_block": True,
            "label_availability_purge": True,
            "embargo_minutes": config.split.embargo_minutes,
        },
    }


def _empty_result(
    *,
    status: str,
    config: BaselineConfig,
    ordered: list[dict[str, object]],
    dataset_path: Path | None,
    dataset_manifest_path: Path | None,
    reason: str,
) -> BaselineResult:
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model": MODEL_NAME,
        "status": status,
        "deployment_authorized": False,
        "automatic_threshold_update": False,
        "oof_rows": 0,
        "folds": 0,
        "metrics": {},
        "score_baseline_metrics": {},
        "coverage": [],
        "score_baseline_coverage": [],
        "family_metrics": [],
        "evidence_gate": {"result": status, "reason": reason, "promotion_authorized": False},
        "limitations": [reason],
    }
    manifest = _manifest(
        config=config,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        input_rows=len(ordered),
        oof_rows=0,
        folds=[],
    )
    return BaselineResult(
        status=status,
        predictions=[],
        metrics={},
        baseline_metrics={},
        coverage=[],
        baseline_coverage=[],
        family_metrics=[],
        folds=[],
        coefficients=[],
        report=report,
        manifest=manifest,
    )


def _eligible_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        row
        for row in rows
        if row.get("target_positive_r") in {True, False}
        and bool(row.get("is_trainable_candidate", True))
        and row.get("decision_timestamp")
    ]


def _feature_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame([{feature: row.get(feature) for feature in FEATURES} for row in rows])
    for feature in NUMERIC_FEATURES:
        frame[feature] = pd.to_numeric(frame[feature], errors="coerce")
    for feature in CATEGORICAL_FEATURES:
        frame[feature] = frame[feature].fillna("missing").astype(str)
    return frame


def _target_array(rows: list[dict[str, object]]) -> np.ndarray:
    return np.asarray([1 if row.get("target_positive_r") is True else 0 for row in rows])


def _validate_feature_contract(rows: list[dict[str, object]]) -> None:
    overlap = FORBIDDEN_FEATURES & set(FEATURES)
    if overlap:
        raise ValueError(f"forbidden label features configured: {sorted(overlap)}")
    for row in rows:
        if row.get("target_positive_r") not in {True, False, None}:
            raise ValueError("target_positive_r must be boolean or null")


def _logit(probability: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


def _spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 2:
        return None
    value = pd.Series(left).rank(method="average").corr(
        pd.Series(right).rank(method="average"),
        method="pearson",
    )
    return round(float(value), 6) if pd.notna(value) else None


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


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: object) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:.4f}"
