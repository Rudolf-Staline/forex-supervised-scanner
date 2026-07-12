"""Tests for counterfactual labels on rejected scanner decisions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.config.settings import load_settings
from app.core.types import Timeframe
from app.data.providers import MarketDataProvider
from app.ml.shadow_labeling import (
    ShadowLabelConfig,
    build_shadow_labels,
    merge_shadow_labels,
    write_shadow_augmented_dataset,
    write_shadow_label_report,
)
from app.ml.temporal import TemporalSplitConfig, label_available_at

BASE = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


class FrameProvider(MarketDataProvider):
    name = "fixture"

    def __init__(self, frame: pd.DataFrame, *, fail: bool = False) -> None:
        self.frame = frame
        self.fail = fail
        self.calls: list[tuple[str, Timeframe, datetime | None, datetime | None]] = []

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        self.calls.append((symbol, timeframe, start, end))
        if self.fail:
            raise RuntimeError("fixture data unavailable")
        result = self.frame.copy()
        result.attrs["provider"] = self.name
        return result


def _settings():
    settings = load_settings().model_copy(deep=True)
    settings.provider.max_bars = 220
    return settings


def _decision(
    decision_id: str = "decision-1",
    *,
    timestamp: datetime = BASE,
    rejected: bool = True,
    labeled: bool = False,
    direction: str = "long",
    entry: float = 1.1000,
    stop: float = 1.0950,
    target: float = 1.1100,
) -> dict[str, object]:
    return {
        "schema_version": "decision_calibration_dataset.v1",
        "decision_id": decision_id,
        "decision_timestamp": timestamp.isoformat(),
        "feature_snapshot_hash": f"hash-{decision_id}",
        "source_records": ["scan_results"],
        "symbol": "EUR/USD",
        "normalized_symbol": "EURUSD",
        "style": "day_trading",
        "setup_family": "trend_continuation",
        "setup_subtype": "ema50_pullback",
        "direction": direction,
        "scanner_status": "rejected" if rejected else "approved",
        "scanner_approved": not rejected,
        "is_rejected": rejected,
        "is_no_trade": False,
        "is_trainable_candidate": True,
        "return_label_available": labeled,
        "activation_label_available": labeled,
        "realized_r": 0.75 if labeled else None,
        "target_positive_r": True if labeled else None,
        "target_non_negative_r": True if labeled else None,
        "label_source": "paper" if labeled else None,
        "entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "tp1": entry + 0.005 if direction == "long" else entry - 0.005,
        "tp2": entry + 0.0075 if direction == "long" else entry - 0.0075,
        "tp3": target,
        "risk_reward": 2.0,
        "spread": None,
        "final_score": 62.0,
        "rejection_category": "score below threshold" if rejected else None,
        "rejection_reason": "score below threshold" if rejected else None,
    }


def _frame(*, target_hit: bool = False, stop_hit: bool = False, activate: bool = True) -> pd.DataFrame:
    index = pd.date_range(BASE - timedelta(hours=24), periods=500, freq="5min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": 1.0800,
            "high": 1.0810,
            "low": 1.0790,
            "close": 1.0800,
            "volume": 100.0,
        },
        index=index,
    )
    first_future = index[index > pd.Timestamp(BASE)][0]
    if activate:
        frame.loc[first_future, ["open", "high", "low", "close"]] = [
            1.1000,
            1.1005,
            1.0995,
            1.1001,
        ]
        terminal = index[index > first_future][0]
        high = 1.1105 if target_hit else 1.1040
        low = 1.0945 if stop_hit else 1.0980
        frame.loc[terminal, ["open", "high", "low", "close"]] = [
            1.1001,
            high,
            low,
            1.1010,
        ]
    return frame


def test_rejected_candidate_receives_positive_counterfactual_label() -> None:
    provider = FrameProvider(_frame(target_hit=True))
    report = build_shadow_labels([_decision()], _settings(), provider)

    assert len(report.labels) == 1
    label = report.labels[0]
    assert label["shadow_status"] == "labeled"
    assert label["activated"] is True
    assert label["target_positive_r"] is True
    assert label["realized_r"] > 1.9
    assert label["close_reason"] == "take_profit"
    assert label["label_source"] == "shadow_historical"
    assert label["counterfactual"] is True
    assert label["max_hold_bars"] == 108
    assert provider.calls[0][1] == Timeframe.M5


def test_stop_wins_when_stop_and_target_share_a_bar() -> None:
    provider = FrameProvider(_frame(target_hit=True, stop_hit=True))
    report = build_shadow_labels([_decision()], _settings(), provider)
    label = report.labels[0]

    assert label["close_reason"] == "stop_loss"
    assert label["realized_r"] < 0.0
    assert label["intrabar_ambiguous"] is True


def test_not_activated_candidate_gets_zero_return_after_full_horizon() -> None:
    provider = FrameProvider(_frame(activate=False))
    report = build_shadow_labels([_decision()], _settings(), provider)
    label = report.labels[0]

    assert label["shadow_status"] == "not_activated"
    assert label["activated"] is False
    assert label["realized_r"] == 0.0
    assert label["target_positive_r"] is False
    assert label["target_non_negative_r"] is True
    assert label["bars_in_trade"] == 0
    assert label["time_in_trade_minutes"] == 108 * 5


def test_candidates_in_same_symbol_timeframe_share_one_provider_fetch() -> None:
    provider = FrameProvider(_frame(target_hit=True))
    rows = [
        _decision("first"),
        _decision("second", timestamp=BASE + timedelta(minutes=5)),
    ]
    report = build_shadow_labels(rows, _settings(), provider)

    assert len(report.labels) == 2
    assert len(provider.calls) == 1
    assert report.labels[0]["shadow_label_id"] != report.labels[1]["shadow_label_id"]


def test_default_scope_excludes_unlabeled_accepted_decisions() -> None:
    provider = FrameProvider(_frame(target_hit=True))
    accepted = _decision(rejected=False)
    default_report = build_shadow_labels([accepted], _settings(), provider)
    broad_report = build_shadow_labels(
        [accepted],
        _settings(),
        provider,
        config=ShadowLabelConfig(scope="all_unlabeled"),
    )

    assert default_report.labels == []
    assert len(broad_report.labels) == 1


def test_invalid_levels_are_audited_without_market_data_fetch() -> None:
    provider = FrameProvider(_frame(target_hit=True))
    invalid = _decision(entry=1.1000, stop=1.1050, target=1.1100)
    report = build_shadow_labels([invalid], _settings(), provider)

    assert report.labels == []
    assert report.failures[0]["reason"] == "invalid_long_levels"
    assert provider.calls == []


def test_provider_failure_does_not_abort_the_run() -> None:
    provider = FrameProvider(_frame(), fail=True)
    report = build_shadow_labels([_decision()], _settings(), provider)

    assert report.labels == []
    assert report.failures[0]["reason"] == "market_data_unavailable"
    assert report.summary["failures"] == 1


def test_merge_applies_shadow_label_without_mutating_source() -> None:
    source = _decision()
    original = json.loads(json.dumps(source))
    label = build_shadow_labels(
        [source], _settings(), FrameProvider(_frame(target_hit=True))
    ).labels[0]
    result = merge_shadow_labels([source], [label])
    merged = result.rows[0]

    assert source == original
    assert merged["return_label_available"] is True
    assert merged["label_source"] == "shadow_historical"
    assert merged["label_timestamp"] == label["label_timestamp"]
    assert "shadow_historical" in merged["source_records"]
    assert result.summary["shadow_labels_applied"] == 1


def test_observed_label_is_never_overwritten_by_shadow() -> None:
    observed = _decision(labeled=True)
    shadow_source = _decision()
    label = build_shadow_labels(
        [shadow_source], _settings(), FrameProvider(_frame(stop_hit=True))
    ).labels[0]
    result = merge_shadow_labels([observed], [label])
    merged = result.rows[0]

    assert merged["realized_r"] == 0.75
    assert merged["label_source"] == "paper"
    assert result.summary["observed_labels_preserved"] == 1


def test_shadow_label_timestamp_is_used_by_temporal_purge() -> None:
    source = _decision()
    label = build_shadow_labels(
        [source], _settings(), FrameProvider(_frame(target_hit=True))
    ).labels[0]
    merged = merge_shadow_labels([source], [label]).rows[0]

    assert label_available_at(merged, TemporalSplitConfig()) == datetime.fromisoformat(
        str(label["label_timestamp"])
    )


def test_writers_use_new_paths_and_never_overwrite_source(tmp_path) -> None:
    source = _decision()
    report = build_shadow_labels(
        [source], _settings(), FrameProvider(_frame(target_hit=True))
    )
    shadow_outputs = write_shadow_label_report(report, tmp_path)
    merged = merge_shadow_labels([source], report.labels)
    merge_outputs = write_shadow_augmented_dataset(merged, tmp_path)

    assert shadow_outputs["labels"].name == "shadow_decision_labels.jsonl"
    assert merge_outputs["augmented_dataset"].name == "decision_calibration_with_shadow.jsonl"
    assert merge_outputs["augmented_training_dataset"].name == "decision_training_with_shadow.jsonl"
    assert not (tmp_path / "decision_calibration_dataset.jsonl").exists()
    assert not list(tmp_path.glob("*.pkl"))
    assert not list(tmp_path.glob("*.joblib"))


def test_invalid_config_is_rejected() -> None:
    try:
        ShadowLabelConfig(minimum_future_bars=0)
    except ValueError as exc:
        assert "minimum_future_bars" in str(exc)
    else:
        raise AssertionError("invalid minimum_future_bars should fail")
