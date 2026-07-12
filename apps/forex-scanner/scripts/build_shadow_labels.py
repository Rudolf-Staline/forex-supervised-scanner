"""Build counterfactual historical labels for rejected scanner decisions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.data.providers import build_provider
from app.ml.shadow_labeling import (
    ShadowLabelConfig,
    build_shadow_labels,
    load_jsonl,
    merge_shadow_labels,
    write_shadow_augmented_dataset,
    write_shadow_label_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build research-only shadow labels. No orders are sent."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "reports" / "decision_calibration" / "decision_calibration_dataset.jsonl",
        help="Decision-level JSONL dataset produced by build_decision_calibration_dataset.py.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "reports" / "shadow_labels",
    )
    parser.add_argument(
        "--provider",
        default="csv",
        choices=["csv", "mt5", "yahoo", "synthetic"],
        help="Historical candle provider. CSV is the research default.",
    )
    parser.add_argument("--csv-data-dir", default=None)
    parser.add_argument(
        "--scope",
        default="rejected_unlabeled",
        choices=["rejected_unlabeled", "all_unlabeled"],
    )
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--minimum-future-bars", type=int, default=1)
    parser.add_argument("--not-activated-return-r", type=float, default=0.0)
    parser.add_argument(
        "--write-augmented-dataset",
        action="store_true",
        help=(
            "Write new decision_calibration_with_shadow.jsonl and "
            "decision_training_with_shadow.jsonl files. The source dataset is never overwritten."
        ),
    )
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Explicitly permit deterministic synthetic candles for testing only.",
    )
    args = parser.parse_args()

    if not args.dataset.is_file():
        raise SystemExit(f"decision dataset not found: {args.dataset}")
    if args.provider == "synthetic" and not args.allow_synthetic:
        raise SystemExit(
            "synthetic shadow labels are disabled by default; pass --allow-synthetic for tests only"
        )

    settings = load_settings().model_copy(deep=True)
    settings.provider.name = args.provider
    if args.csv_data_dir:
        settings.provider.csv_data_dir = args.csv_data_dir
    provider = build_provider(settings)
    rows = load_jsonl(args.dataset)
    config = ShadowLabelConfig(
        scope=args.scope,
        not_activated_return_r=args.not_activated_return_r,
        minimum_future_bars=args.minimum_future_bars,
        max_candidates=args.max_candidates,
    )

    print(
        "shadow_labeling "
        f"provider={provider.name} scope={config.scope} decisions={len(rows)} "
        "paper_research_only=true broker_orders=false"
    )
    report = build_shadow_labels(rows, settings, provider, config=config)
    outputs = write_shadow_label_report(report, args.output_dir)
    print(f"labels={report.summary['labels_written']} failures={report.summary['failures']}")

    if args.write_augmented_dataset:
        merged = merge_shadow_labels(rows, report.labels)
        outputs.update(write_shadow_augmented_dataset(merged, args.output_dir))
        print(
            "augmented_dataset=true "
            f"shadow_labels_applied={merged.summary['shadow_labels_applied']} "
            f"observed_labels_preserved={merged.summary['observed_labels_preserved']}"
        )
    else:
        print("augmented_dataset=false source_dataset_mutated=false")

    for name, path in outputs.items():
        print(f"{name}_export={path}")


if __name__ == "__main__":
    main()
