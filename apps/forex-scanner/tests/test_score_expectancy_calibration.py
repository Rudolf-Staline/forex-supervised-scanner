"""Score -> expectancy calibration tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradeRecord, TradingStyle
from app.reporting.score_expectancy import (
    bootstrap_ci,
    build_report,
    report_to_dict,
    write_reports,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trade(
    net_r: float,
    final_score: float,
    *,
    day: int,
    technical: float | None = None,
    empirical: float | None = None,
) -> TradeRecord:
    moment = BASE + timedelta(hours=day)
    return TradeRecord(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        entry_time=moment,
        exit_time=moment,
        entry=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        exit_price=1.11 if net_r > 0 else 1.095,
        gross_r=net_r,
        net_r=net_r,
        exit_reason="take_profit" if net_r > 0 else "stop_loss",
        cost_pips=1.0,
        final_score=final_score,
        technical_score=technical,
        empirical_score=empirical,
    )


def test_bootstrap_ci_brackets_the_mean() -> None:
    values = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0], dtype=float)
    low, high = bootstrap_ci(values, resamples=1000, confidence=0.95, seed=7)
    assert low <= values.mean() <= high
    assert low < high


def test_monotonic_calibration_detected_for_score_aligned_outcomes() -> None:
    # Build trades where higher score reliably maps to higher net R.
    trades = []
    day = 0
    for score, net_r in [(40, -1.0), (50, -0.5), (60, 0.2), (70, 0.6), (85, 1.4)]:
        for _ in range(8):
            trades.append(_trade(net_r, float(score), day=day, technical=float(score)))
            day += 1

    report = build_report(trades, n_buckets=5, bootstrap_resamples=500)
    assert report.scored_trades == 40
    assert len(report.buckets) == 5
    assert report.monotonic_non_decreasing is True
    assert report.spearman_score_to_expectancy > 0.5
    # Lowest bucket loses, highest bucket wins.
    assert report.buckets[0].expectancy < report.buckets[-1].expectancy
    # Each bucket carries a confidence interval and a sample count.
    for bucket in report.buckets:
        assert bucket.samples > 0
        assert bucket.ci_low <= bucket.expectancy <= bucket.ci_high


def test_non_separating_component_is_flagged() -> None:
    trades = []
    for day in range(60):
        score = float(40 + day)  # final score rises with day
        net_r = 1.0 if day >= 30 else -1.0  # final score separates outcomes
        # empirical alternates independently of the outcome -> non-separating.
        empirical = 50.0 + float(day % 2)
        trades.append(_trade(net_r, score, day=day, technical=score, empirical=empirical))

    report = build_report(trades, n_buckets=10, bootstrap_resamples=300)
    flagged = set(report.flagged_components)
    assert "empirical" in flagged
    assert "final" not in flagged  # final score clearly separates


def test_write_reports_emits_json_and_text(tmp_path) -> None:
    trades = [_trade(1.0 if i % 2 else -1.0, float(50 + i), day=i, technical=float(50 + i)) for i in range(20)]
    report = build_report(trades, n_buckets=4, bootstrap_resamples=200)
    outputs = write_reports(report, tmp_path)

    payload = json.loads(outputs["json"].read_text())
    assert payload["scored_trades"] == 20
    assert "buckets" in payload and payload["buckets"]
    assert outputs["txt"].read_text().startswith("Score -> Expectancy Calibration")
    assert report_to_dict(report)["requested_buckets"] == 4


def test_empty_trades_produce_empty_report() -> None:
    report = build_report([], n_buckets=10)
    assert report.scored_trades == 0
    assert report.buckets == []
    assert report.monotonic_non_decreasing is True  # vacuously true
