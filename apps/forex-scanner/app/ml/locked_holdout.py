"""Preregistered, single-use calendar holdout evaluation for supervised research.

The holdout plan freezes dates, features, weighting policy, calibration size, embargo,
and evidence thresholds before evaluation. The evaluator trains only on labels known
before the holdout cutoff, evaluates promotion evidence on observed holdout outcomes,
and never persists a model.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from app.ml.baseline import (
    CATEGORICAL_FEATURES,
    FEATURES,
    FORBIDDEN_FEATURES,
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
    _number,
    _target_array,
    _validate_feature_contract,
)
from app.ml.source_aware import (
    SourceAwareConfig,
    _fit_weighted_platt,
    _prediction_coverage,
    _prediction_metrics,
    _sample_weights,
    label_source_class,
)
from app.ml.temporal import label_available_at, sorted_rows

PLAN_SCHEMA_VERSION = "locked_calendar_holdout_plan.v1"
REPORT_SCHEMA_VERSION = "locked_calendar_holdout_report.v1"
MODEL_NAME = "regularized_logistic_platt_locked_calendar_holdout"
SELECTION_RULE = (
    "all trainable candidates with decision_timestamp in [holdout_start, holdout_end); "
    "promotion evidence uses observed labels only"
)


@dataclass(frozen=True)
class LockedHoldoutConfig:
    """Immutable preregistration controls for one calendar holdout."""

    holdout_start: str
    holdout_end: str
    calibration_rows: int = 80
    embargo_minutes: float = 1_440.0
    minimum_train_rows: int = 200
    minimum_observed_train_rows: int = 40
    minimum_observed_calibration_rows: int = 10
    minimum_observed_holdout_rows: int = 30
    minimum_observed_class_count: int = 5
    observed_label_weight: float = 1.0
    shadow_label_weight: float = 0.35
    calibration_bins: int = 10
    coverage_levels: tuple[float, ...] = (0.10, 0.25, 0.50, 1.00)
    regularization_c: float = 1.0
    platt_regularization_c: float = 100.0
    max_iterations: int = 2_000
    class_weight_balanced: bool = True
    historical_dry_run: bool = False

    def __post_init__(self) -> None:
        start = _timestamp(self.holdout_start)
        end = _timestamp(self.holdout_end)
        if end <= start:
            raise ValueError("holdout_end must be later than holdout_start")
        for name in (
            "calibration_rows",
            "minimum_train_rows",
            "minimum_observed_train_rows",
            "minimum_observed_calibration_rows",
            "minimum_observed_holdout_rows",
            "minimum_observed_class_count",
            "calibration_bins",
            "max_iterations",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        if self.embargo_minutes < 0.0:
            raise ValueError("embargo_minutes must be non-negative")
        if self.observed_label_weight <= 0.0 or self.shadow_label_weight <= 0.0:
            raise ValueError("label weights must be positive")
        if self.shadow_label_weight > self.observed_label_weight:
            raise ValueError("shadow_label_weight must not exceed observed_label_weight")
        if self.regularization_c <= 0.0 or self.platt_regularization_c <= 0.0:
            raise ValueError("regularization values must be positive")
        if any(level <= 0.0 or level > 1.0 for level in self.coverage_levels):
            raise ValueError("coverage levels must be in (0, 1]")


@dataclass(frozen=True)
class LockedHoldoutResult:
    status: str
    predictions: list[dict[str, object]]
    report: dict[str, object]
    manifest: dict[str, object]
    training_audit: dict[str, object]


def freeze_holdout_plan(
    config: LockedHoldoutConfig,
    *,
    frozen_at: datetime | None = None,
    notes: str = "",
) -> dict[str, object]:
    """Create a metric-free preregistration plan.

    Strict plans must be frozen no later than the holdout start. Historical dry-run
    plans are explicitly marked and can never authorize promotion.
    """

    created = _as_utc(frozen_at or datetime.now(timezone.utc))
    start = _timestamp(config.holdout_start)
    if created > start and not config.historical_dry_run:
        raise ValueError(
            "strict holdout plans must be frozen before holdout_start; "
            "use historical_dry_run only for pipeline validation"
        )
    payload: dict[str, object] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "frozen_at": created.isoformat(),
        "holdout_start": _timestamp(config.holdout_start).isoformat(),
        "holdout_end": _timestamp(config.holdout_end).isoformat(),
        "selection_rule": SELECTION_RULE,
        "config": _config_dict(config),
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "forbidden_features": sorted(FORBIDDEN_FEATURES),
        "feature_contract_sha256": _feature_contract_sha256(),
        "research_only": True,
        "historical_dry_run": config.historical_dry_run,
        "deployment_authorized": False,
        "automatic_threshold_update": False,
        "single_use_receipt_required": True,
        "notes": notes,
    }
    payload["plan_sha256"] = _payload_sha256(payload)
    return payload


def write_holdout_plan(plan: dict[str, object], path: Path) -> Path:
    """Write a preregistration plan without any outcome metrics."""

    verify_holdout_plan(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"holdout plan already exists: {path}")
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_holdout_plan(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("holdout plan must be a JSON object")
    verify_holdout_plan(payload)
    return payload


def verify_holdout_plan(plan: dict[str, object]) -> None:
    """Fail if the preregistration content or feature contract changed."""

    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported holdout plan schema")
    expected = str(plan.get("plan_sha256") or "")
    candidate = dict(plan)
    candidate.pop("plan_sha256", None)
    actual = _payload_sha256(candidate)
    if not expected or expected != actual:
        raise ValueError("holdout plan hash mismatch")
    if plan.get("feature_contract_sha256") != _feature_contract_sha256():
        raise ValueError("feature contract changed after holdout preregistration")
    config = _config_from_plan(plan)
    if _timestamp(config.holdout_end) <= _timestamp(config.holdout_start):
        raise ValueError("invalid holdout window")


def evaluate_locked_holdout(
    rows: list[dict[str, object]],
    plan: dict[str, object],
    *,
    dataset_path: Path | None = None,
    dataset_manifest_path: Path | None = None,
) -> LockedHoldoutResult:
    """Fit once on pre-holdout history and score the locked calendar cohort."""

    verify_holdout_plan(plan)
    config = _config_from_plan(plan)
    _validate_feature_contract(rows)
    start = _timestamp(config.holdout_start)
    end = _timestamp(config.holdout_end)
    cutoff = start - timedelta(minutes=config.embargo_minutes)

    eligible = sorted_rows(_eligible_rows(rows))
    holdout_all = [
        row
        for row in eligible
        if start <= _timestamp(row.get("decision_timestamp")) < end
    ]
    if not holdout_all:
        return _empty_result(
            status="empty_holdout",
            reason="no trainable candidates exist inside the locked calendar window",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )

    preholdout = [
        row
        for row in eligible
        if _timestamp(row.get("decision_timestamp")) < cutoff
        and label_available_at(row, _temporal_config(config)) <= cutoff
    ]
    if len(preholdout) < config.minimum_train_rows + config.calibration_rows:
        return _empty_result(
            status="insufficient_preholdout_data",
            reason="not enough label-available rows before the locked cutoff",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )

    calibration_rows = preholdout[-config.calibration_rows :]
    train_rows = preholdout[: -config.calibration_rows]
    observed_train = [row for row in train_rows if label_source_class(row) == "observed"]
    observed_calibration = [
        row for row in calibration_rows if label_source_class(row) == "observed"
    ]
    if len(train_rows) < config.minimum_train_rows:
        return _empty_result(
            status="insufficient_preholdout_data",
            reason="training rows are below the preregistered minimum",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )
    if len(observed_train) < config.minimum_observed_train_rows:
        return _empty_result(
            status="insufficient_observed_training_data",
            reason="observed training rows are below the preregistered minimum",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )
    if len(observed_calibration) < config.minimum_observed_calibration_rows:
        return _empty_result(
            status="insufficient_observed_calibration_data",
            reason="observed calibration rows are below the preregistered minimum",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )
    if min(_class_counts(observed_train).values()) < config.minimum_observed_class_count:
        return _empty_result(
            status="insufficient_observed_training_classes",
            reason="observed training class support is below the preregistered minimum",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )
    if min(_class_counts(observed_calibration).values()) < 1:
        return _empty_result(
            status="insufficient_observed_calibration_classes",
            reason="observed calibration requires both target classes",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
        )

    source_config = _source_config(config)
    pipeline = _build_pipeline(source_config.baseline)
    train_target = _target_array(train_rows)
    train_weights = _sample_weights(train_rows, source_config)
    pipeline.fit(
        _feature_frame(train_rows),
        train_target,
        classifier__sample_weight=train_weights,
    )

    calibration_target = _target_array(calibration_rows)
    calibration_weights = _sample_weights(calibration_rows, source_config)
    calibration_raw = pipeline.predict_proba(_feature_frame(calibration_rows))[:, 1]
    calibrator, calibration_method = _fit_weighted_platt(
        calibration_raw,
        calibration_target,
        calibration_weights,
        source_config.baseline,
    )

    labeled_holdout = [
        row for row in holdout_all if row.get("target_positive_r") in {True, False}
    ]
    observed_holdout = [
        row for row in labeled_holdout if label_source_class(row) == "observed"
    ]
    if len(observed_holdout) < config.minimum_observed_holdout_rows:
        return _empty_result(
            status="insufficient_observed_holdout_labels",
            reason="observed holdout labels are below the preregistered minimum",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
            holdout_rows=len(holdout_all),
            labeled_holdout_rows=len(labeled_holdout),
            observed_holdout_rows=len(observed_holdout),
        )
    if min(_class_counts(observed_holdout).values()) < config.minimum_observed_class_count:
        return _empty_result(
            status="insufficient_observed_holdout_classes",
            reason="observed holdout class support is below the preregistered minimum",
            plan=plan,
            config=config,
            dataset_path=dataset_path,
            dataset_manifest_path=dataset_manifest_path,
            holdout_rows=len(holdout_all),
            labeled_holdout_rows=len(labeled_holdout),
            observed_holdout_rows=len(observed_holdout),
        )

    raw_probability = pipeline.predict_proba(_feature_frame(labeled_holdout))[:, 1]
    probability = _apply_platt(calibrator, raw_probability)
    predictions: list[dict[str, object]] = []
    for row, raw_value, value in zip(
        labeled_holdout, raw_probability, probability, strict=True
    ):
        final_score = _number(row.get("final_score"))
        source_class = label_source_class(row)
        predictions.append(
            {
                "decision_id": str(row.get("decision_id") or ""),
                "decision_timestamp": row.get("decision_timestamp"),
                "normalized_symbol": row.get("normalized_symbol"),
                "setup_family": row.get("setup_family"),
                "setup_subtype": row.get("setup_subtype"),
                "target_positive_r": bool(row.get("target_positive_r")),
                "realized_r": _number(row.get("realized_r")),
                "model_probability": round(float(value), 10),
                "uncalibrated_model_probability": round(float(raw_value), 10),
                "score_baseline_probability": (
                    round(min(1.0, max(0.0, final_score / 100.0)), 10)
                    if final_score is not None
                    else 0.5
                ),
                "label_source": row.get("label_source"),
                "label_source_class": source_class,
                "shadow_counterfactual": source_class == "shadow",
                "calibration_method": calibration_method,
                "evaluation_partition": "locked_calendar_holdout",
            }
        )
    predictions.sort(
        key=lambda row: (str(row.get("decision_timestamp", "")), str(row["decision_id"]))
    )

    observed_predictions = [
        row for row in predictions if row.get("label_source_class") == "observed"
    ]
    shadow_predictions = [
        row for row in predictions if row.get("label_source_class") == "shadow"
    ]
    observed_metrics = _prediction_metrics(observed_predictions, config.calibration_bins)
    shadow_metrics = _prediction_metrics(shadow_predictions, config.calibration_bins)
    observed_coverage = _prediction_coverage(
        observed_predictions,
        config.coverage_levels,
        "model_observed_locked_holdout",
    )
    observed_baseline_coverage = _prediction_coverage(
        observed_predictions,
        config.coverage_levels,
        "score_baseline_observed_locked_holdout",
        probability_key="score_baseline_probability",
    )
    gate = _evidence_gate(
        predictions=observed_predictions,
        metrics=observed_metrics["model"],
        baseline_metrics=observed_metrics["score_baseline"],
        coverage=observed_coverage,
        minimum_oof_rows=config.minimum_observed_holdout_rows,
    )
    gate.update(
        {
            "validation_basis": "observed_locked_calendar_holdout_only",
            "historical_dry_run": config.historical_dry_run,
            "promotion_authorized": False,
        }
    )
    if config.historical_dry_run:
        gate["result"] = "historical_dry_run_no_promotion"
        gate["reason"] = "historical dry runs validate the pipeline but cannot authorize promotion"

    training_audit = {
        "cutoff": cutoff.isoformat(),
        "train_rows": len(train_rows),
        "calibration_rows": len(calibration_rows),
        "train_rows_by_source": _source_counts(train_rows),
        "calibration_rows_by_source": _source_counts(calibration_rows),
        "train_effective_weight": round(float(np.sum(train_weights)), 6),
        "calibration_effective_weight": round(float(np.sum(calibration_weights)), 6),
        "latest_train_decision": max(
            _timestamp(row.get("decision_timestamp")) for row in train_rows
        ).isoformat(),
        "latest_calibration_decision": max(
            _timestamp(row.get("decision_timestamp")) for row in calibration_rows
        ).isoformat(),
        "latest_preholdout_label_available_at": max(
            label_available_at(row, _temporal_config(config)) for row in preholdout
        ).isoformat(),
        "holdout_ids_in_training": len(
            {str(row.get("decision_id")) for row in holdout_all}
            & {str(row.get("decision_id")) for row in preholdout}
        ),
    }
    if training_audit["holdout_ids_in_training"] != 0:
        raise AssertionError("locked holdout rows leaked into preholdout fitting data")
    if _timestamp(training_audit["latest_preholdout_label_available_at"]) > cutoff:
        raise AssertionError("a preholdout label was unavailable at the locked cutoff")

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model": MODEL_NAME,
        "status": "evaluated",
        "plan_sha256": plan["plan_sha256"],
        "holdout_start": start.isoformat(),
        "holdout_end": end.isoformat(),
        "holdout_rows": len(holdout_all),
        "labeled_holdout_rows": len(labeled_holdout),
        "observed_holdout_rows": len(observed_predictions),
        "shadow_holdout_rows": len(shadow_predictions),
        "observed_metrics": observed_metrics,
        "shadow_metrics": shadow_metrics,
        "observed_coverage": observed_coverage,
        "observed_score_baseline_coverage": observed_baseline_coverage,
        "evidence_gate": gate,
        "deployment_authorized": False,
        "automatic_threshold_update": False,
        "model_binary_written": False,
        "single_use_receipt_required": True,
        "limitations": [
            "the receipt is an operational guard, not cryptographic prevention of file deletion",
            "shadow labels may influence fitting but never the holdout evidence gate",
            "historical dry runs cannot authorize promotion",
            "broker-quality forward paper evidence remains mandatory",
        ],
    }
    manifest = _manifest(
        plan=plan,
        config=config,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        predictions=predictions,
        training_audit=training_audit,
    )
    return LockedHoldoutResult(
        status="evaluated",
        predictions=predictions,
        report=report,
        manifest=manifest,
        training_audit=training_audit,
    )


def write_locked_holdout_result(
    result: LockedHoldoutResult,
    output_dir: Path,
    *,
    receipt_path: Path | None = None,
) -> dict[str, Path]:
    """Write one evaluation and atomically claim its local single-use receipt."""

    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_path or output_dir / "locked_holdout_evaluation_receipt.json"
    if receipt.exists():
        raise FileExistsError(
            f"locked holdout receipt already exists: {receipt}; replay is disabled by default"
        )
    paths = {
        "report_json": output_dir / "locked_holdout_report.json",
        "report_txt": output_dir / "locked_holdout_report.txt",
        "predictions_jsonl": output_dir / "locked_holdout_predictions.jsonl",
        "predictions_csv": output_dir / "locked_holdout_predictions.csv",
        "training_audit": output_dir / "locked_holdout_training_audit.json",
        "manifest": output_dir / "locked_holdout_manifest.json",
        "receipt": receipt,
    }
    paths["report_json"].write_text(
        json.dumps(result.report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    paths["report_txt"].write_text(report_to_text(result), encoding="utf-8")
    _write_jsonl(paths["predictions_jsonl"], result.predictions)
    _write_csv(paths["predictions_csv"], result.predictions)
    paths["training_audit"].write_text(
        json.dumps(result.training_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["manifest"].write_text(
        json.dumps(result.manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt_payload = {
        "schema_version": "locked_calendar_holdout_receipt.v1",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": result.report.get("plan_sha256"),
        "status": result.status,
        "report_sha256": _file_sha256(paths["report_json"]),
        "predictions_sha256": _file_sha256(paths["predictions_jsonl"]),
        "deployment_authorized": False,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return paths


def report_to_text(result: LockedHoldoutResult) -> str:
    report = result.report
    gate = report.get("evidence_gate", {})
    observed = report.get("observed_metrics", {}).get("model", {})
    baseline = report.get("observed_metrics", {}).get("score_baseline", {})
    lines = [
        "Locked Calendar Holdout (research-only)",
        "=======================================",
        f"status                    : {result.status}",
        f"holdout start             : {report.get('holdout_start', '')}",
        f"holdout end               : {report.get('holdout_end', '')}",
        f"observed holdout rows     : {report.get('observed_holdout_rows', 0)}",
        f"shadow holdout rows       : {report.get('shadow_holdout_rows', 0)}",
        "deployment authorized     : false",
        "automatic threshold update: false",
        "",
        "Observed locked-holdout quality:",
        f"  ROC AUC model           : {_fmt(observed.get('roc_auc'))}",
        f"  ROC AUC score baseline  : {_fmt(baseline.get('roc_auc'))}",
        f"  Brier model             : {_fmt(observed.get('brier'))}",
        f"  Brier score baseline    : {_fmt(baseline.get('brier'))}",
        "",
        "Evidence gate:",
        f"  basis                    : {gate.get('validation_basis', '')}",
        f"  result                   : {gate.get('result', '')}",
        f"  reason                   : {gate.get('reason', '')}",
        "",
        "This evaluation is single-use by local receipt and writes no model binary.",
        "",
    ]
    return "\n".join(lines)


def _empty_result(
    *,
    status: str,
    reason: str,
    plan: dict[str, object],
    config: LockedHoldoutConfig,
    dataset_path: Path | None,
    dataset_manifest_path: Path | None,
    holdout_rows: int = 0,
    labeled_holdout_rows: int = 0,
    observed_holdout_rows: int = 0,
) -> LockedHoldoutResult:
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model": MODEL_NAME,
        "status": status,
        "reason": reason,
        "plan_sha256": plan.get("plan_sha256"),
        "holdout_start": _timestamp(config.holdout_start).isoformat(),
        "holdout_end": _timestamp(config.holdout_end).isoformat(),
        "holdout_rows": holdout_rows,
        "labeled_holdout_rows": labeled_holdout_rows,
        "observed_holdout_rows": observed_holdout_rows,
        "evidence_gate": {
            "result": status,
            "reason": reason,
            "validation_basis": "observed_locked_calendar_holdout_only",
            "promotion_authorized": False,
        },
        "deployment_authorized": False,
        "automatic_threshold_update": False,
        "model_binary_written": False,
    }
    manifest = _manifest(
        plan=plan,
        config=config,
        dataset_path=dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        predictions=[],
        training_audit={},
    )
    return LockedHoldoutResult(
        status=status,
        predictions=[],
        report=report,
        manifest=manifest,
        training_audit={},
    )


def _manifest(
    *,
    plan: dict[str, object],
    config: LockedHoldoutConfig,
    dataset_path: Path | None,
    dataset_manifest_path: Path | None,
    predictions: list[dict[str, object]],
    training_audit: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model": MODEL_NAME,
        "plan_sha256": plan.get("plan_sha256"),
        "feature_contract_sha256": _feature_contract_sha256(),
        "config": _config_dict(config),
        "dataset_path": str(dataset_path) if dataset_path else None,
        "dataset_sha256": _file_sha256(dataset_path),
        "dataset_manifest_path": str(dataset_manifest_path) if dataset_manifest_path else None,
        "dataset_manifest_sha256": _file_sha256(dataset_manifest_path),
        "prediction_rows": len(predictions),
        "prediction_rows_by_source": {
            "observed": sum(
                row.get("label_source_class") == "observed" for row in predictions
            ),
            "shadow": sum(row.get("label_source_class") == "shadow" for row in predictions),
        },
        "training_audit": training_audit,
        "validation_policy": {
            "calendar_holdout": True,
            "holdout_used_for_feature_selection": False,
            "holdout_used_for_hyperparameter_selection": False,
            "holdout_used_for_weight_selection": False,
            "label_availability_purge": True,
            "embargo_minutes": config.embargo_minutes,
            "observed_only_evidence_gate": True,
            "single_use_receipt_required": True,
        },
        "model_binary_written": False,
        "deployment_authorized": False,
    }


def _source_config(config: LockedHoldoutConfig) -> SourceAwareConfig:
    baseline = BaselineConfig(
        regularization_c=config.regularization_c,
        max_iterations=config.max_iterations,
        class_weight_balanced=config.class_weight_balanced,
        platt_regularization_c=config.platt_regularization_c,
        calibration_bins=config.calibration_bins,
        minimum_oof_rows=config.minimum_observed_holdout_rows,
        coverage_levels=config.coverage_levels,
    )
    return SourceAwareConfig(
        baseline=baseline,
        observed_label_weight=config.observed_label_weight,
        shadow_label_weight=config.shadow_label_weight,
        minimum_observed_train_rows=config.minimum_observed_train_rows,
        minimum_observed_calibration_rows=config.minimum_observed_calibration_rows,
        minimum_observed_test_rows=config.minimum_observed_holdout_rows,
        minimum_observed_class_count=config.minimum_observed_class_count,
        minimum_observed_oof_rows=config.minimum_observed_holdout_rows,
    )


def _temporal_config(config: LockedHoldoutConfig):
    baseline = _source_config(config).baseline
    return baseline.split.__class__(
        min_train_rows=max(2, config.minimum_train_rows),
        calibration_rows=max(2, config.calibration_rows),
        test_rows=1,
        step_rows=1,
        embargo_minutes=config.embargo_minutes,
        unknown_label_delay_minutes=baseline.split.unknown_label_delay_minutes,
        minimum_class_count=1,
    )


def _config_dict(config: LockedHoldoutConfig) -> dict[str, object]:
    payload = asdict(config)
    payload["coverage_levels"] = list(config.coverage_levels)
    payload["holdout_start"] = _timestamp(config.holdout_start).isoformat()
    payload["holdout_end"] = _timestamp(config.holdout_end).isoformat()
    return payload


def _config_from_plan(plan: dict[str, object]) -> LockedHoldoutConfig:
    raw = plan.get("config")
    if not isinstance(raw, dict):
        raise ValueError("holdout plan config is missing")
    payload = dict(raw)
    payload["coverage_levels"] = tuple(float(item) for item in payload["coverage_levels"])
    return LockedHoldoutConfig(**payload)


def _eligible_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        row
        for row in rows
        if bool(row.get("is_trainable_candidate", True))
        and row.get("decision_timestamp")
        and row.get("target_positive_r") in {True, False}
    ]


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


def _feature_contract_sha256() -> str:
    payload = {
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "features": FEATURES,
        "forbidden_features": sorted(FORBIDDEN_FEATURES),
        "selection_rule": SELECTION_RULE,
    }
    return _payload_sha256(payload)


def _payload_sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        raise ValueError("timestamp is required")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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
    return "n/a" if number is None or not math.isfinite(number) else f"{number:.4f}"
