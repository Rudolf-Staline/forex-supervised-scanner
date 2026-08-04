"""Train one optional chart-pattern model from local historical OHLCV CSV files.

The first intended use is `double_top`. Labels are weak labels produced by the
existing causal rules detector and must be reviewed before deployment. This
script never trades and never changes scanner or execution settings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.types import DirectionBias
from app.pattern_ml.dataset import build_weak_pattern_samples, concatenate_samples, temporal_split_by_source
from app.pattern_ml.training import save_pattern_model_artifact, train_random_forest_pattern_model

_PATTERN_DIRECTIONS = {
    "double_top": DirectionBias.SHORT,
    "double_bottom": DirectionBias.LONG,
    "bullish_engulfing": DirectionBias.LONG,
    "bearish_engulfing": DirectionBias.SHORT,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True, type=Path, help="Historical OHLCV CSV files")
    parser.add_argument("--pattern", default="double_top")
    parser.add_argument("--direction", choices=["long", "short"], default=None)
    parser.add_argument("--window-size", type=int, default=64)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    parser.add_argument("--test-fraction", type=float, default=0.20)
    parser.add_argument("--embargo-samples", type=int, default=None)
    parser.add_argument("--minimum-precision", type=float, default=0.70)
    parser.add_argument("--n-estimators", type=int, default=400)
    parser.add_argument("--random-state", type=int, default=42690)
    parser.add_argument("--model-version", default="double-top-v1")
    parser.add_argument("--output-dir", type=Path, default=Path("models/patterns/double_top"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    pattern_name = args.pattern.strip().lower().replace("-", "_").replace(" ", "_")
    direction = DirectionBias(args.direction) if args.direction else _PATTERN_DIRECTIONS.get(pattern_name)
    if direction is None:
        raise SystemExit("--direction is required for this pattern")

    sample_sets = []
    for path in args.input:
        frame = _load_frame(path)
        sample_sets.append(
            build_weak_pattern_samples(
                frame,
                source=path.stem,
                pattern_name=pattern_name,
                direction=direction,
                window_size=args.window_size,
                stride=args.stride,
            )
        )
    samples = concatenate_samples(sample_sets)
    split = temporal_split_by_source(
        samples,
        validation_fraction=args.validation_fraction,
        test_fraction=args.test_fraction,
        embargo_samples=args.embargo_samples,
    )
    trained = train_random_forest_pattern_model(
        split,
        minimum_precision=args.minimum_precision,
        random_state=args.random_state,
        n_estimators=args.n_estimators,
    )
    paths = save_pattern_model_artifact(
        trained,
        output_dir=args.output_dir,
        pattern_name=pattern_name,
        model_version=args.model_version,
        direction=direction,
        window_size=args.window_size,
    )
    labels_path = args.output_dir / "weak_labels.csv"
    _write_label_review(split, labels_path)
    summary = {
        "pattern": pattern_name,
        "direction": direction.value,
        "decision_threshold": trained.threshold,
        "samples": len(samples.labels),
        "positives": samples.positive_count,
        "artifacts": {key: str(value) for key, value in paths.items()},
        "weak_labels": str(labels_path),
        "deployment_authorized": False,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _load_frame(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    if "timestamp" not in frame:
        raise ValueError(f"{path}: missing timestamp column")
    required = {"open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    index = pd.to_datetime(frame.pop("timestamp"), utc=True, errors="coerce")
    if index.isna().any():
        raise ValueError(f"{path}: contains invalid timestamps")
    frame.index = pd.DatetimeIndex(index)
    numeric_columns = [column for column in ["open", "high", "low", "close", "volume"] if column in frame]
    frame.loc[:, numeric_columns] = frame.loc[:, numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        raise ValueError(f"{path}: duplicate timestamps are not allowed")
    return frame


def _write_label_review(split, path: Path) -> None:
    rows = []
    for partition_name, samples in (("train", split.train), ("validation", split.validation), ("test", split.test)):
        rows.extend(
            {
                "partition": partition_name,
                "source": str(source),
                "timestamp": timestamp.isoformat(),
                "end_position": int(position),
                "weak_label": int(label),
                "human_label": "",
                "review_status": "pending",
            }
            for source, timestamp, position, label in zip(samples.sources, samples.timestamps, samples.positions, samples.labels, strict=True)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


if __name__ == "__main__":
    raise SystemExit(main())
