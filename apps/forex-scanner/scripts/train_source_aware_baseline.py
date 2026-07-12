"""Run a source-aware supervised benchmark. Research only; no deployment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.baseline import BaselineConfig, load_training_rows
from app.ml.source_aware import (
    SourceAwareConfig,
    report_to_text,
    run_source_aware_baseline,
    write_source_aware_result,
)
from app.ml.temporal import TemporalSplitConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a source-aware logistic meta-filter with shadow-label down-weighting "
            "and an observed-only evidence gate. No model is persisted or deployed."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT
        / "reports"
        / "shadow_labels"
        / "decision_training_with_shadow.jsonl",
    )
    parser.add_argument("--dataset-manifest", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "source_aware_supervised",
    )
    parser.add_argument("--observed-label-weight", type=float, default=1.0)
    parser.add_argument("--shadow-label-weight", type=float, default=0.35)
    parser.add_argument("--minimum-observed-train-rows", type=int, default=20)
    parser.add_argument("--minimum-observed-calibration-rows", type=int, default=5)
    parser.add_argument("--minimum-observed-test-rows", type=int, default=5)
    parser.add_argument("--minimum-observed-class-count", type=int, default=2)
    parser.add_argument("--minimum-observed-oof-rows", type=int, default=40)
    parser.add_argument("--min-train-rows", type=int, default=120)
    parser.add_argument("--calibration-rows", type=int, default=40)
    parser.add_argument("--test-rows", type=int, default=40)
    parser.add_argument("--step-rows", type=int, default=40)
    parser.add_argument("--embargo-minutes", type=float, default=60.0)
    parser.add_argument("--unknown-label-delay-minutes", type=float, default=1_440.0)
    parser.add_argument("--minimum-class-count", type=int, default=8)
    parser.add_argument("--minimum-oof-rows", type=int, default=80)
    parser.add_argument("--regularization-c", type=float, default=1.0)
    parser.add_argument("--platt-regularization-c", type=float, default=100.0)
    parser.add_argument("--max-iterations", type=int, default=2_000)
    parser.add_argument("--calibration-bins", type=int, default=10)
    parser.add_argument(
        "--no-balanced-class-weight",
        action="store_true",
        help="Disable class_weight='balanced' in the base logistic model.",
    )
    args = parser.parse_args()

    if not args.dataset.is_file():
        raise SystemExit(
            f"training dataset not found: {args.dataset}. Run "
            "scripts/build_shadow_labels.py --write-augmented-dataset first."
        )

    rows = load_training_rows(args.dataset)
    split = TemporalSplitConfig(
        min_train_rows=args.min_train_rows,
        calibration_rows=args.calibration_rows,
        test_rows=args.test_rows,
        step_rows=args.step_rows,
        embargo_minutes=args.embargo_minutes,
        unknown_label_delay_minutes=args.unknown_label_delay_minutes,
        minimum_class_count=args.minimum_class_count,
    )
    baseline = BaselineConfig(
        split=split,
        regularization_c=args.regularization_c,
        max_iterations=args.max_iterations,
        class_weight_balanced=not args.no_balanced_class_weight,
        platt_regularization_c=args.platt_regularization_c,
        calibration_bins=args.calibration_bins,
        minimum_oof_rows=args.minimum_oof_rows,
    )
    config = SourceAwareConfig(
        baseline=baseline,
        observed_label_weight=args.observed_label_weight,
        shadow_label_weight=args.shadow_label_weight,
        minimum_observed_train_rows=args.minimum_observed_train_rows,
        minimum_observed_calibration_rows=args.minimum_observed_calibration_rows,
        minimum_observed_test_rows=args.minimum_observed_test_rows,
        minimum_observed_class_count=args.minimum_observed_class_count,
        minimum_observed_oof_rows=args.minimum_observed_oof_rows,
    )
    manifest_path = (
        args.dataset_manifest
        if args.dataset_manifest is not None and args.dataset_manifest.is_file()
        else None
    )
    result = run_source_aware_baseline(
        rows,
        config=config,
        dataset_path=args.dataset,
        dataset_manifest_path=manifest_path,
    )
    outputs = write_source_aware_result(result, args.output_dir)

    print(
        "source_aware_supervised research_only=true deployment_authorized=false "
        "evidence_basis=observed_oof_only"
    )
    print(report_to_text(result))
    for label, path in outputs.items():
        print(f"{label}_export={path}")


if __name__ == "__main__":
    main()
