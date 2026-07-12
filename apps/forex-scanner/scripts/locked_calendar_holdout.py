"""Freeze or evaluate a single-use locked calendar holdout. Research only."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.locked_holdout import (
    LockedHoldoutConfig,
    evaluate_locked_holdout,
    freeze_holdout_plan,
    load_holdout_plan,
    report_to_text,
    write_holdout_plan,
    write_locked_holdout_result,
)


def _add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--holdout-start", required=True, help="Inclusive ISO-8601 UTC timestamp")
    parser.add_argument("--holdout-end", required=True, help="Exclusive ISO-8601 UTC timestamp")
    parser.add_argument("--calibration-rows", type=int, default=80)
    parser.add_argument("--embargo-minutes", type=float, default=1_440.0)
    parser.add_argument("--minimum-train-rows", type=int, default=200)
    parser.add_argument("--minimum-observed-train-rows", type=int, default=40)
    parser.add_argument("--minimum-observed-calibration-rows", type=int, default=10)
    parser.add_argument("--minimum-observed-holdout-rows", type=int, default=30)
    parser.add_argument("--minimum-observed-class-count", type=int, default=5)
    parser.add_argument("--observed-label-weight", type=float, default=1.0)
    parser.add_argument("--shadow-label-weight", type=float, default=0.35)
    parser.add_argument("--calibration-bins", type=int, default=10)
    parser.add_argument("--regularization-c", type=float, default=1.0)
    parser.add_argument("--platt-regularization-c", type=float, default=100.0)
    parser.add_argument("--max-iterations", type=int, default=2_000)
    parser.add_argument("--no-balanced-class-weight", action="store_true")
    parser.add_argument(
        "--historical-dry-run",
        action="store_true",
        help="Validate the pipeline on an already-known period; never eligible for promotion.",
    )


def _config(args: argparse.Namespace) -> LockedHoldoutConfig:
    return LockedHoldoutConfig(
        holdout_start=args.holdout_start,
        holdout_end=args.holdout_end,
        calibration_rows=args.calibration_rows,
        embargo_minutes=args.embargo_minutes,
        minimum_train_rows=args.minimum_train_rows,
        minimum_observed_train_rows=args.minimum_observed_train_rows,
        minimum_observed_calibration_rows=args.minimum_observed_calibration_rows,
        minimum_observed_holdout_rows=args.minimum_observed_holdout_rows,
        minimum_observed_class_count=args.minimum_observed_class_count,
        observed_label_weight=args.observed_label_weight,
        shadow_label_weight=args.shadow_label_weight,
        calibration_bins=args.calibration_bins,
        regularization_c=args.regularization_c,
        platt_regularization_c=args.platt_regularization_c,
        max_iterations=args.max_iterations,
        class_weight_balanced=not args.no_balanced_class_weight,
        historical_dry_run=args.historical_dry_run,
    )


def _load_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(payload)
    return rows


def _as_utc_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preregister or evaluate a locked calendar holdout. No model is persisted."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze_parser = subparsers.add_parser("freeze", help="Write a metric-free holdout plan")
    _add_policy_arguments(freeze_parser)
    freeze_parser.add_argument(
        "--plan",
        type=Path,
        default=PROJECT_ROOT / "reports" / "locked_holdout" / "holdout_plan.json",
    )
    freeze_parser.add_argument("--notes", default="")

    evaluate_parser = subparsers.add_parser("evaluate", help="Evaluate one frozen plan once")
    evaluate_parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT
        / "reports"
        / "decision_calibration"
        / "decision_calibration_with_shadow.jsonl",
    )
    evaluate_parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=PROJECT_ROOT
        / "reports"
        / "decision_calibration"
        / "decision_calibration_manifest.json",
    )
    evaluate_parser.add_argument(
        "--plan",
        type=Path,
        default=PROJECT_ROOT / "reports" / "locked_holdout" / "holdout_plan.json",
    )
    evaluate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "locked_holdout",
    )
    evaluate_parser.add_argument("--receipt", type=Path, default=None)

    args = parser.parse_args()
    if args.command == "freeze":
        plan = freeze_holdout_plan(_config(args), notes=args.notes)
        path = write_holdout_plan(plan, args.plan)
        print("locked_holdout_plan frozen=true metrics_computed=false")
        print(f"plan_sha256={plan['plan_sha256']}")
        print(f"plan={path}")
        return

    if not args.dataset.is_file():
        raise SystemExit(f"dataset not found: {args.dataset}")
    if not args.plan.is_file():
        raise SystemExit(f"holdout plan not found: {args.plan}")
    receipt = args.receipt or args.output_dir / "locked_holdout_evaluation_receipt.json"
    if receipt.exists():
        raise SystemExit(
            f"locked holdout already evaluated: receipt exists at {receipt}; replay refused"
        )

    plan = load_holdout_plan(args.plan)
    holdout_end = _as_utc_timestamp(plan["holdout_end"])
    if not bool(plan.get("historical_dry_run")) and datetime.now(timezone.utc) < holdout_end:
        raise SystemExit(
            f"locked holdout is still open until {holdout_end.isoformat()}; early evaluation refused"
        )

    rows = _load_rows(args.dataset)
    manifest = args.dataset_manifest if args.dataset_manifest.is_file() else None
    result = evaluate_locked_holdout(
        rows,
        plan,
        dataset_path=args.dataset,
        dataset_manifest_path=manifest,
    )
    outputs = write_locked_holdout_result(result, args.output_dir, receipt_path=receipt)
    print("locked_holdout research_only=true deployment_authorized=false")
    print(report_to_text(result))
    for label, path in outputs.items():
        print(f"{label}_export={path}")


if __name__ == "__main__":
    main()
