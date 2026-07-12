"""Tests for the research-only supervised meta-filter baseline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.ml.baseline import (
    FEATURES,
    FORBIDDEN_FEATURES,
    BaselineConfig,
    load_training_rows,
    run_supervised_baseline,
    write_baseline_result,
)
from app.ml.temporal import (
    TemporalSplitConfig,
    build_purged_expanding_folds,
    label_available_at,
    sorted_rows,
)

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _rows(count: int = 300, *, include_duration: bool = True) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        positive = index % 4 in {2, 3}
        family = "trend_continuation" if index % 2 == 0 else "breakout_confirmation"
        score = 78.0 if positive else 54.0
        rows.append(
            {
                "decision_id": f"decision-{index:04d}",
                "decision_timestamp": (BASE + timedelta(hours=index)).isoformat(),
                "normalized_symbol": ["EURUSD", "GBPUSD", "USDJPY"][index % 3],
                "style": "day_trading",
                "setup_family": family,
                "setup_subtype": "ema50_pullback" if index % 2 == 0 else "range_breakout",
                "direction": "long" if index % 3 else "short",
                "scanner_status": "approved",
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
                "time_in_trade_minutes": 30.0 if include_duration else None,
                "order_status": "fully_closed_trade",
                "label_source": "paper_order",
            }
        )
    return rows


def _config() -> BaselineConfig:
    return BaselineConfig(
        split=TemporalSplitConfig(
            min_train_rows=80,
            calibration_rows=20,
            test_rows=20,
            step_rows=20,
            embargo_minutes=0.0,
            unknown_label_delay_minutes=1_440.0,
            minimum_class_count=5,
        ),
        minimum_oof_rows=40,
        calibration_bins=5,
    )


def test_temporal_folds_use_only_labels_available_before_test() -> None:
    rows = sorted_rows(_rows(220))
    config = _config().split
    folds = build_purged_expanding_folds(rows, config)

    assert folds
    all_test_indices: list[int] = []
    for fold in folds:
        all_test_indices.extend(fold.test_indices)
        assert fold.latest_pretest_label_available_at <= fold.label_cutoff
        assert max(label_available_at(rows[index], config) for index in fold.train_indices) <= fold.label_cutoff
        assert max(label_available_at(rows[index], config) for index in fold.calibration_indices) <= fold.label_cutoff
        assert max(fold.train_indices) < min(fold.calibration_indices)
        assert max(fold.calibration_indices) < min(fold.test_indices)
    assert len(all_test_indices) == len(set(all_test_indices))


def test_supervised_baseline_produces_unique_deterministic_oof_predictions() -> None:
    rows = _rows()
    first = run_supervised_baseline(rows, config=_config())
    second = run_supervised_baseline(rows, config=_config())

    assert first.status == "evaluated"
    assert len(first.predictions) >= 40
    assert len({row["decision_id"] for row in first.predictions}) == len(first.predictions)
    assert first.predictions == second.predictions
    assert first.metrics["roc_auc"] is not None
    assert first.baseline_metrics["roc_auc"] is not None
    assert first.report["deployment_authorized"] is False
    assert first.manifest["model_binary_written"] is False
    assert first.manifest["validation_policy"]["random_split"] is False
    assert all(
        fold["latest_pretest_label_available_at"] <= fold["label_cutoff"]
        for fold in first.folds
    )


def test_feature_allowlist_excludes_every_label_and_lifecycle_field() -> None:
    assert not (set(FEATURES) & FORBIDDEN_FEATURES)
    assert "realized_r" not in FEATURES
    assert "target_positive_r" not in FEATURES
    assert "time_in_trade_minutes" not in FEATURES
    assert "mae" not in FEATURES
    assert "mfe" not in FEATURES


def test_family_metrics_and_coverage_are_reported() -> None:
    result = run_supervised_baseline(_rows(), config=_config())

    assert {item["setup_family"] for item in result.family_metrics} == {
        "breakout_confirmation",
        "trend_continuation",
    }
    assert [item["coverage"] for item in result.coverage] == [0.10, 0.25, 0.50, 1.00]
    assert all(item["rows"] > 0 for item in result.coverage)
    assert result.report["evidence_gate"]["promotion_authorized"] is False


def test_insufficient_dataset_returns_auditable_no_result() -> None:
    result = run_supervised_baseline(_rows(60), config=_config())

    assert result.status == "insufficient_data"
    assert result.predictions == []
    assert result.report["evidence_gate"]["result"] == "insufficient_data"
    assert result.report["deployment_authorized"] is False


def test_unknown_label_delay_can_purge_recent_rows_and_prevent_invalid_fold() -> None:
    rows = _rows(140, include_duration=False)
    config = BaselineConfig(
        split=TemporalSplitConfig(
            min_train_rows=80,
            calibration_rows=20,
            test_rows=20,
            step_rows=20,
            embargo_minutes=0.0,
            unknown_label_delay_minutes=10_000.0,
            minimum_class_count=5,
        ),
        minimum_oof_rows=20,
    )

    result = run_supervised_baseline(rows, config=config)

    assert result.status == "insufficient_data"
    assert result.predictions == []


def test_writers_emit_reports_but_never_a_model_binary(tmp_path: Path) -> None:
    result = run_supervised_baseline(_rows(), config=_config())
    outputs = write_baseline_result(result, tmp_path)

    assert outputs["report_json"].is_file()
    assert outputs["predictions_jsonl"].is_file()
    assert outputs["manifest"].is_file()
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["model_binary_written"] is False
    forbidden_suffixes = {".pkl", ".pickle", ".joblib", ".onnx"}
    assert not any(path.suffix in forbidden_suffixes for path in tmp_path.iterdir())


def test_load_training_rows_skips_unlabeled_and_non_trainable_rows(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    rows = _rows(2)
    unlabeled = dict(rows[0])
    unlabeled["decision_id"] = "unlabeled"
    unlabeled["target_positive_r"] = None
    rejected = dict(rows[1])
    rejected["decision_id"] = "not-trainable"
    rejected["is_trainable_candidate"] = False
    with path.open("w", encoding="utf-8") as handle:
        for row in [rows[0], unlabeled, rejected]:
            handle.write(json.dumps(row) + "\n")

    loaded = load_training_rows(path)

    assert [row["decision_id"] for row in loaded] == [rows[0]["decision_id"]]


def test_invalid_temporal_configuration_fails_loudly() -> None:
    with pytest.raises(ValueError, match="step_rows"):
        TemporalSplitConfig(test_rows=40, step_rows=20)
    with pytest.raises(ValueError, match="coverage"):
        BaselineConfig(coverage_levels=(0.0,))
