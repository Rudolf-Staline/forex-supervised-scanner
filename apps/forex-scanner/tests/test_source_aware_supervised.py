"""Tests for source-aware observed/shadow supervised evaluation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.ml.baseline import BaselineConfig
from app.ml.source_aware import (
    SourceAwareConfig,
    label_source_class,
    run_source_aware_baseline,
    write_source_aware_result,
)
from app.ml.temporal import TemporalSplitConfig

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _rows(count: int = 320, *, all_shadow: bool = False) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        positive = index % 4 in {2, 3}
        shadow = all_shadow or index % 3 == 0
        score = 78.0 if positive else 54.0
        rows.append(
            {
                "decision_id": f"decision-{index:04d}",
                "decision_timestamp": (BASE + timedelta(hours=index)).isoformat(),
                "normalized_symbol": ["EURUSD", "GBPUSD", "USDJPY"][index % 3],
                "style": "day_trading",
                "setup_family": (
                    "trend_continuation" if index % 2 == 0 else "breakout_confirmation"
                ),
                "setup_subtype": (
                    "ema50_pullback" if index % 2 == 0 else "breakout_close"
                ),
                "direction": "long" if index % 3 else "short",
                "scanner_status": "rejected" if shadow else "approved",
                "regime": "trending" if index % 2 == 0 else "ranging",
                "htf_regime": "trending",
                "entry_regime": "trending" if index % 2 == 0 else "ranging",
                "trigger_regime": "trending",
                "session": "london" if index % 2 == 0 else "new_york",
                "provider": "csv",
                "score_band": "75-80" if positive else "50-60",
                "technical_score": score + 4.0,
                "execution_score": score,
                "context_score": 70.0 if positive else 50.0,
                "empirical_score": 60.0 if positive else 45.0,
                "final_score": score,
                "pattern_score": 5.0 if positive else 0.0,
                "activation_quality": 75.0 if positive else 45.0,
                "invalidation_quality": 70.0 if positive else 48.0,
                "risk_reward": 2.2 if positive else 1.4,
                "required_min_rr": 1.5,
                "spread_atr_ratio": 0.08 if positive else 0.20,
                "data_quality_score": 90.0,
                "base_min_score": 60.0,
                "adaptive_min_score": 62.0,
                "effective_min_score": 62.0,
                "target_positive_r": positive,
                "target_non_negative_r": positive,
                "realized_r": 1.2 if positive else -1.0,
                "return_label_available": True,
                "is_trainable_candidate": True,
                "time_in_trade_minutes": 30.0,
                "label_timestamp": (
                    BASE + timedelta(hours=index, minutes=30)
                ).isoformat(),
                "label_source": "shadow_historical" if shadow else "paper_order",
                "shadow_counterfactual": shadow,
            }
        )
    return rows


def _config(**overrides: object) -> SourceAwareConfig:
    baseline = BaselineConfig(
        split=TemporalSplitConfig(
            min_train_rows=80,
            calibration_rows=24,
            test_rows=24,
            step_rows=24,
            embargo_minutes=0.0,
            unknown_label_delay_minutes=1_440.0,
            minimum_class_count=4,
        ),
        minimum_oof_rows=48,
        calibration_bins=5,
    )
    values: dict[str, object] = {
        "baseline": baseline,
        "observed_label_weight": 1.0,
        "shadow_label_weight": 0.25,
        "minimum_observed_train_rows": 30,
        "minimum_observed_calibration_rows": 8,
        "minimum_observed_test_rows": 8,
        "minimum_observed_class_count": 2,
        "minimum_observed_oof_rows": 48,
    }
    values.update(overrides)
    return SourceAwareConfig(**values)


def test_source_aware_baseline_reports_both_sources_and_weights() -> None:
    result = run_source_aware_baseline(_rows(), config=_config())

    assert result.status == "evaluated"
    assert result.predictions
    sources = {row["label_source_class"] for row in result.predictions}
    assert sources == {"observed", "shadow"}
    assert {
        row["training_weight_policy"]
        for row in result.predictions
        if row["label_source_class"] == "shadow"
    } == {0.25}
    assert {
        row["training_weight_policy"]
        for row in result.predictions
        if row["label_source_class"] == "observed"
    } == {1.0}
    assert {item["source"] for item in result.source_metrics} == {"observed", "shadow"}
    assert result.observed_metrics["rows"] > 0
    assert result.shadow_metrics["rows"] > 0


def test_every_kept_fold_has_observed_train_calibration_and_test_anchors() -> None:
    result = run_source_aware_baseline(_rows(), config=_config())

    assert result.folds
    for fold in result.folds:
        assert fold["train_rows_by_source"]["observed"] >= 30
        assert fold["calibration_rows_by_source"]["observed"] >= 8
        assert fold["test_rows_by_source"]["observed"] >= 8
        expected_train_weight = (
            fold["train_rows_by_source"]["observed"] * 1.0
            + fold["train_rows_by_source"]["shadow"] * 0.25
        )
        assert fold["train_effective_weight"] == pytest.approx(expected_train_weight)


def test_evidence_gate_uses_only_observed_oof_predictions() -> None:
    result = run_source_aware_baseline(_rows(), config=_config())
    observed_count = sum(
        row["label_source_class"] == "observed" for row in result.predictions
    )
    shadow_count = sum(row["label_source_class"] == "shadow" for row in result.predictions)
    gate = result.report["evidence_gate"]

    assert gate["validation_basis"] == "observed_oof_only"
    assert gate["observed_oof_rows"] == observed_count
    assert gate["shadow_oof_rows_excluded_from_gate"] == shadow_count
    assert result.report["deployment_authorized"] is False
    assert gate["promotion_authorized"] is False


def test_all_shadow_dataset_cannot_construct_a_valid_fold() -> None:
    result = run_source_aware_baseline(_rows(all_shadow=True), config=_config())

    assert result.status == "insufficient_observed_data"
    assert result.predictions == []
    assert result.report["evidence_gate"]["validation_basis"] == "observed_oof_only"
    assert result.report["deployment_authorized"] is False


def test_too_few_observed_oof_rows_cannot_pass_evidence_gate() -> None:
    result = run_source_aware_baseline(
        _rows(),
        config=_config(minimum_observed_oof_rows=10_000),
    )

    assert result.status == "insufficient_observed_oof"
    assert result.report["evidence_gate"]["result"] == "insufficient_data"
    assert result.report["evidence_gate"]["promotion_authorized"] is False


def test_source_classification_is_explicit_and_conservative() -> None:
    assert label_source_class({"label_source": "shadow_historical"}) == "shadow"
    assert label_source_class({"shadow_counterfactual": True}) == "shadow"
    assert label_source_class({"label_source": "paper_order"}) == "observed"
    assert label_source_class({}) == "observed"


def test_results_are_deterministic() -> None:
    rows = _rows()
    first = run_source_aware_baseline(rows, config=_config())
    second = run_source_aware_baseline(rows, config=_config())

    assert first.predictions == second.predictions
    assert first.report == second.report


def test_writer_emits_diagnostics_but_no_model_binary(tmp_path: Path) -> None:
    result = run_source_aware_baseline(_rows(), config=_config())
    outputs = write_source_aware_result(result, tmp_path)

    assert outputs["report_json"].is_file()
    assert outputs["predictions_jsonl"].is_file()
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["model_binary_written"] is False
    assert manifest["evidence_policy"]["promotion_gate_uses_observed_oof_only"] is True
    assert not list(tmp_path.glob("*.pkl"))
    assert not list(tmp_path.glob("*.joblib"))
    assert not list(tmp_path.glob("*.onnx"))


def test_invalid_source_weights_fail_loudly() -> None:
    with pytest.raises(ValueError, match="shadow_label_weight"):
        _config(shadow_label_weight=1.1)
    with pytest.raises(ValueError, match="observed_label_weight"):
        _config(observed_label_weight=0.0)
