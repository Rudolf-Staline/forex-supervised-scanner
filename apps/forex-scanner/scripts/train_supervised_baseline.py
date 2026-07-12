"""Run the first supervised meta-filter baseline. Research only; no deployment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.baseline import (
    BaselineConfig,
    load_training_rows,
    report_to_text,
    run_supervised_baseline,
    write_baseline_result,
)
from app.ml.temporal import TemporalSplitConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a regularized logistic meta-filter with purged chronological "
            "OOF predictions. No model is persisted or deployed."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT
        / "reports"
        / "decision_calibration"
        / "decision_training_dataset.jsonl",
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "reports"
        / "decision_calibration"
        / "decision_calibration_manifest.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "supervised_baseline",
    )
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
            "scripts/build_decision_calibration_dataset.py first."
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
    config = BaselineConfig(
        split=split,
        regularization_c=args.regularization_c,
        max_iterations=args.max_iterations,
        class_weight_balanced=not args.no_balanced_class_weight,
        platt_regularization_c=args.platt_regularization_c,
        calibration_bins=args.calibration_bins,
        minimum_oof_rows=args.minimum_oof_rows,
    )
    manifest_path = args.dataset_manifest if args.dataset_manifest.is_file() else None
    result = run_supervised_baseline(
        rows,
        config=config,
        dataset_path=args.dataset,
        dataset_manifest_path=manifest_path,
    )
    outputs = write_baseline_result(result, args.output_dir)

    print("supervised_baseline research_only=true deployment_authorized=false")
    print(report_to_text(result))
    for label, path in outputs.items():
        print(f"{label}_export={path}")


if __name__ == "__main__":
    main()
