"""Purged chronological split utilities for decision-level model research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class TemporalSplitConfig:
    """Row-count controls plus a time embargo for expanding-window evaluation."""

    min_train_rows: int = 120
    calibration_rows: int = 40
    test_rows: int = 40
    step_rows: int = 40
    embargo_minutes: float = 60.0
    unknown_label_delay_minutes: float = 1_440.0
    minimum_class_count: int = 8

    def __post_init__(self) -> None:
        if self.min_train_rows < 2:
            raise ValueError("min_train_rows must be at least 2")
        if self.calibration_rows < 2:
            raise ValueError("calibration_rows must be at least 2")
        if self.test_rows < 1:
            raise ValueError("test_rows must be positive")
        if self.step_rows < self.test_rows:
            raise ValueError("step_rows must be at least test_rows to keep OOF tests disjoint")
        if self.embargo_minutes < 0.0:
            raise ValueError("embargo_minutes must be non-negative")
        if self.unknown_label_delay_minutes < 0.0:
            raise ValueError("unknown_label_delay_minutes must be non-negative")
        if self.minimum_class_count < 1:
            raise ValueError("minimum_class_count must be positive")


@dataclass(frozen=True)
class TemporalFold:
    """One expanding train/calibration/test split with audit timestamps."""

    fold_index: int
    train_indices: tuple[int, ...]
    calibration_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    train_start: datetime
    train_end: datetime
    calibration_start: datetime
    calibration_end: datetime
    test_start: datetime
    test_end: datetime
    label_cutoff: datetime
    latest_pretest_label_available_at: datetime


def build_purged_expanding_folds(
    rows: list[dict[str, object]],
    config: TemporalSplitConfig | None = None,
) -> list[TemporalFold]:
    """Build disjoint OOF folds using only labels available before each test.

    Rows must already be filtered to known binary targets. They are sorted by
    ``decision_timestamp`` and ``decision_id`` before splitting. A pre-test row is
    eligible only when its inferred label-availability timestamp is no later than
    ``test_start - embargo``. The most recent eligible rows form the independent
    calibration segment; all older eligible rows form the training segment.
    """

    rules = config or TemporalSplitConfig()
    ordered = sorted(
        rows,
        key=lambda row: (_timestamp(row.get("decision_timestamp")), str(row.get("decision_id", ""))),
    )
    if len(ordered) < rules.min_train_rows + rules.calibration_rows + rules.test_rows:
        return []

    folds: list[TemporalFold] = []
    first_test_index = rules.min_train_rows + rules.calibration_rows
    fold_index = 0
    for test_start_index in range(first_test_index, len(ordered), rules.step_rows):
        test_stop_index = min(test_start_index + rules.test_rows, len(ordered))
        test_indices = tuple(range(test_start_index, test_stop_index))
        if len(test_indices) < rules.test_rows:
            break

        test_start = _timestamp(ordered[test_indices[0]].get("decision_timestamp"))
        test_end = _timestamp(ordered[test_indices[-1]].get("decision_timestamp"))
        cutoff = test_start - timedelta(minutes=rules.embargo_minutes)
        eligible = [
            index
            for index in range(test_start_index)
            if label_available_at(ordered[index], rules) <= cutoff
        ]
        if len(eligible) < rules.min_train_rows + rules.calibration_rows:
            continue

        calibration_indices = tuple(eligible[-rules.calibration_rows :])
        train_indices = tuple(eligible[: -rules.calibration_rows])
        if len(train_indices) < rules.min_train_rows:
            continue
        if not _has_minimum_class_support(ordered, train_indices, rules.minimum_class_count):
            continue
        if not _has_minimum_class_support(ordered, calibration_indices, 1):
            continue

        latest_available = max(label_available_at(ordered[index], rules) for index in eligible)
        if latest_available > cutoff:
            raise AssertionError("purged fold contains a label unavailable at test time")
        if set(train_indices) & set(calibration_indices):
            raise AssertionError("train and calibration indices overlap")
        if (set(train_indices) | set(calibration_indices)) & set(test_indices):
            raise AssertionError("pre-test and test indices overlap")

        folds.append(
            TemporalFold(
                fold_index=fold_index,
                train_indices=train_indices,
                calibration_indices=calibration_indices,
                test_indices=test_indices,
                train_start=_timestamp(ordered[train_indices[0]].get("decision_timestamp")),
                train_end=_timestamp(ordered[train_indices[-1]].get("decision_timestamp")),
                calibration_start=_timestamp(
                    ordered[calibration_indices[0]].get("decision_timestamp")
                ),
                calibration_end=_timestamp(
                    ordered[calibration_indices[-1]].get("decision_timestamp")
                ),
                test_start=test_start,
                test_end=test_end,
                label_cutoff=cutoff,
                latest_pretest_label_available_at=latest_available,
            )
        )
        fold_index += 1
    return folds


def label_available_at(
    row: dict[str, object],
    config: TemporalSplitConfig | None = None,
) -> datetime:
    """Infer when a row's return label became knowable.

    A future dataset schema may provide ``label_timestamp`` directly. Until then,
    paper rows use ``decision_timestamp + time_in_trade_minutes``. Rows lacking both
    receive the configured conservative delay rather than being assumed immediately
    known.
    """

    rules = config or TemporalSplitConfig()
    explicit = row.get("label_timestamp")
    if explicit:
        return _timestamp(explicit)
    decision = _timestamp(row.get("decision_timestamp"))
    delay = _number(row.get("time_in_trade_minutes"))
    if delay is None:
        delay = rules.unknown_label_delay_minutes
    return decision + timedelta(minutes=max(0.0, delay))


def sorted_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return the deterministic ordering used by fold indices."""

    return sorted(
        rows,
        key=lambda row: (_timestamp(row.get("decision_timestamp")), str(row.get("decision_id", ""))),
    )


def fold_to_dict(fold: TemporalFold) -> dict[str, object]:
    """Serialize split metadata without exposing redundant row payloads."""

    return {
        "fold_index": fold.fold_index,
        "train_rows": len(fold.train_indices),
        "calibration_rows": len(fold.calibration_indices),
        "test_rows": len(fold.test_indices),
        "train_start": fold.train_start.isoformat(),
        "train_end": fold.train_end.isoformat(),
        "calibration_start": fold.calibration_start.isoformat(),
        "calibration_end": fold.calibration_end.isoformat(),
        "test_start": fold.test_start.isoformat(),
        "test_end": fold.test_end.isoformat(),
        "label_cutoff": fold.label_cutoff.isoformat(),
        "latest_pretest_label_available_at": fold.latest_pretest_label_available_at.isoformat(),
    }


def _has_minimum_class_support(
    rows: list[dict[str, object]],
    indices: tuple[int, ...],
    minimum: int,
) -> bool:
    counts = {False: 0, True: 0}
    for index in indices:
        target = rows[index].get("target_positive_r")
        if target is True:
            counts[True] += 1
        elif target is False:
            counts[False] += 1
    return counts[True] >= minimum and counts[False] >= minimum


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        raise ValueError("decision_timestamp is required for temporal validation")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
