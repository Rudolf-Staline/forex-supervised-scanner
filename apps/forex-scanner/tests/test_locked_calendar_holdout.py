"""Tests for preregistered single-use calendar holdout evaluation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.ml.locked_holdout import (
    LockedHoldoutConfig,
    evaluate_locked_holdout,
    freeze_holdout_plan,
    verify_holdout_plan,
    write_holdout_plan,
    write_locked_holdout_result,
)

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _rows(count: int = 360) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        timestamp = BASE + timedelta(hours=index)
        positive = index % 4 in {1, 2}
        shadow = index % 5 == 0
        score = 76.0 if positive else 52.0
        rows.append(
            {
                "decision_id": f"decision-{index:04d}",
                "decision_timestamp": timestamp.isoformat(),
                "normalized_symbol": ["EURUSD", "GBPUSD", "USDJPY"][index % 3],
                "style": "day_trading",
                "setup_family": (
                    "trend_continuation" if index % 2 == 0 else "breakout_confirmation"
                ),
                "setup_subtype": "ema50_pullback" if index % 2 == 0 else "breakout_retest",
                "direction": "long" if index % 3 else "short",
                "scanner_status": "rejected" if shadow else "approved",
                "regime": "trending" if index % 2 == 0 else "ranging",
                "htf_regime": "trending",
                "entry_regime": "trending" if index % 2 == 0 else "ranging",
                "trigger_regime": "trending",
                "session": "london" if index % 2 == 0 else "new_york",
                "provider": "csv",
                "score_band": "75-80" if positive else "50-60",
                "technical_score": score + 3.0,
                "execution_score": score,
                "context_score": 68.0 if positive else 49.0,
                "empirical_score": 61.0 if positive else 44.0,
                "final_score": score,
                "pattern_score": 4.0 if positive else 0.0,
                "activation_quality": 72.0 if positive else 43.0,
                "invalidation_quality": 69.0 if positive else 47.0,
                "risk_reward": 2.1 if positive else 1.3,
                "required_min_rr": 1.5,
                "spread_atr_ratio": 0.08 if positive else 0.21,
                "data_quality_score": 91.0,
                "base_min_score": 60.0,
                "adaptive_min_score": 62.0,
                "effective_min_score": 62.0,
                "target_positive_r": positive,
                "target_non_negative_r": positive,
                "realized_r": 1.1 if positive else -1.0,
                "return_label_available": True,
                "is_trainable_candidate": True,
                "time_in_trade_minutes": 30.0,
                "label_timestamp": (timestamp + timedelta(minutes=30)).isoformat(),
                "order_status": "shadow_closed" if shadow else "fully_closed_trade",
                "label_source": "shadow_historical" if shadow else "paper_order",
                "shadow_counterfactual": shadow,
            }
        )
    return rows


def _config(**overrides: object) -> LockedHoldoutConfig:
    values: dict[str, object] = {
        "holdout_start": (BASE + timedelta(hours=280)).isoformat(),
        "holdout_end": (BASE + timedelta(hours=340)).isoformat(),
        "calibration_rows": 40,
        "embargo_minutes": 0.0,
        "minimum_train_rows": 180,
        "minimum_observed_train_rows": 80,
        "minimum_observed_calibration_rows": 20,
        "minimum_observed_holdout_rows": 30,
        "minimum_observed_class_count": 5,
        "calibration_bins": 5,
        "historical_dry_run": True,
    }
    values.update(overrides)
    return LockedHoldoutConfig(**values)


def _plan(config: LockedHoldoutConfig | None = None) -> dict[str, object]:
    return freeze_holdout_plan(
        config or _config(),
        frozen_at=BASE - timedelta(days=1),
        notes="test preregistration",
    )


def test_plan_hash_detects_any_post_freeze_edit() -> None:
    plan = _plan()
    verify_holdout_plan(plan)
    tampered = json.loads(json.dumps(plan))
    tampered["config"]["shadow_label_weight"] = 0.9

    with pytest.raises(ValueError, match="hash mismatch"):
        verify_holdout_plan(tampered)


def test_strict_plan_must_be_frozen_before_holdout_start() -> None:
    strict = _config(historical_dry_run=False)
    with pytest.raises(ValueError, match="before holdout_start"):
        freeze_holdout_plan(
            strict,
            frozen_at=BASE + timedelta(hours=300),
        )


def test_locked_holdout_is_disjoint_and_uses_observed_evidence_only() -> None:
    rows = _rows()
    result = evaluate_locked_holdout(rows, _plan())

    assert result.status == "evaluated"
    assert result.training_audit["holdout_ids_in_training"] == 0
    assert result.training_audit["latest_preholdout_label_available_at"] <= result.training_audit["cutoff"]
    assert result.report["observed_holdout_rows"] > 0
    assert result.report["shadow_holdout_rows"] > 0
    assert result.report["evidence_gate"]["validation_basis"] == (
        "observed_locked_calendar_holdout_only"
    )
    assert result.report["evidence_gate"]["result"] == "historical_dry_run_no_promotion"
    assert result.report["deployment_authorized"] is False
    assert all(
        row["evaluation_partition"] == "locked_calendar_holdout"
        for row in result.predictions
    )


def test_label_unavailable_at_cutoff_is_purged_from_fitting() -> None:
    rows = _rows()
    cutoff = BASE + timedelta(hours=280)
    rows[260]["label_timestamp"] = (cutoff + timedelta(days=10)).isoformat()
    result = evaluate_locked_holdout(rows, _plan())

    assert result.status == "evaluated"
    assert result.training_audit["latest_preholdout_label_available_at"] <= result.training_audit["cutoff"]
    assert result.training_audit["train_rows"] + result.training_audit["calibration_rows"] < 280


def test_all_shadow_holdout_can_never_supply_promotion_evidence() -> None:
    rows = _rows()
    start = BASE + timedelta(hours=280)
    end = BASE + timedelta(hours=340)
    for row in rows:
        timestamp = datetime.fromisoformat(str(row["decision_timestamp"]))
        if start <= timestamp < end:
            row["label_source"] = "shadow_historical"
            row["shadow_counterfactual"] = True

    result = evaluate_locked_holdout(rows, _plan())

    assert result.status == "insufficient_observed_holdout_labels"
    assert result.predictions == []
    assert result.report["evidence_gate"]["promotion_authorized"] is False


def test_plan_and_receipt_are_single_use_files(tmp_path: Path) -> None:
    plan = _plan()
    plan_path = tmp_path / "holdout_plan.json"
    write_holdout_plan(plan, plan_path)
    with pytest.raises(FileExistsError, match="already exists"):
        write_holdout_plan(plan, plan_path)

    result = evaluate_locked_holdout(_rows(), plan)
    outputs = write_locked_holdout_result(result, tmp_path / "result")
    assert outputs["receipt"].is_file()
    with pytest.raises(FileExistsError, match="receipt already exists"):
        write_locked_holdout_result(result, tmp_path / "result")


def test_artifacts_never_include_a_model_binary(tmp_path: Path) -> None:
    result = evaluate_locked_holdout(_rows(), _plan())
    outputs = write_locked_holdout_result(result, tmp_path)

    assert outputs["report_json"].is_file()
    assert outputs["manifest"].is_file()
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
    assert manifest["model_binary_written"] is False
    assert manifest["validation_policy"]["holdout_used_for_hyperparameter_selection"] is False
    assert not any(
        path.suffix in {".pkl", ".pickle", ".joblib", ".onnx"}
        for path in tmp_path.iterdir()
    )


def test_invalid_window_and_weight_policy_fail_loudly() -> None:
    with pytest.raises(ValueError, match="later"):
        _config(
            holdout_start=(BASE + timedelta(hours=10)).isoformat(),
            holdout_end=(BASE + timedelta(hours=5)).isoformat(),
        )
    with pytest.raises(ValueError, match="must not exceed"):
        _config(observed_label_weight=0.5, shadow_label_weight=0.8)
