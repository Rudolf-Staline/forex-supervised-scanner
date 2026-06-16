"""Tests for the OOS-registry decomposition (no network, no re-backtest)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.reporting.edge_decomposition import (
    block_bootstrap_ci,
    cluster_bootstrap_ci,
    decompose,
    effective_sample_size,
    half_split_robustness,
    iid_bootstrap_ci,
    load_registry,
    per_pair_metrics,
)
from app.reporting.score_expectancy import build_report_from_frame, spearman_ci


def _registry(pairs: int = 2, per_pair: int = 60, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    ts0 = pd.Timestamp("2023-01-02 00:00:00", tz="UTC")
    for p in range(pairs):
        n = per_pair
        ts = ts0 + pd.to_timedelta(np.arange(n), unit="h")
        net = rng.normal(0.0, 1.0, size=n)
        gross = net + 0.02  # cost ~0.02 R
        frames.append(pd.DataFrame({
            "pair": f"P{p}", "timestamp": ts, "score": rng.uniform(50, 80, size=n),
            "gross_r": gross, "net_r": net,
            "exit_reason": rng.choice(["take_profit", "stop_loss", "time_exit"], size=n),
        }))
    return pd.concat(frames, ignore_index=True).sort_values("timestamp").reset_index(drop=True)


def test_load_registry_validates_columns(tmp_path: Path) -> None:
    path = tmp_path / "reg.csv"
    _registry().drop(columns=["net_r"]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="missing column"):
        load_registry(path)


def test_load_registry_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "reg.csv"
    _registry(pairs=2, per_pair=30).to_csv(path, index=False)
    df = load_registry(path)
    assert len(df) == 60
    assert str(df["timestamp"].dt.tz) == "UTC"
    assert df["timestamp"].is_monotonic_increasing


def test_iid_and_grouped_bootstrap_bracket_the_mean() -> None:
    df = _registry(pairs=3, per_pair=80)
    mean = float(df["net_r"].mean())
    for lo, hi in (
        iid_bootstrap_ci(df["net_r"].to_numpy(), resamples=500, seed=1),
        cluster_bootstrap_ci(df, resamples=500, seed=1),
        block_bootstrap_ci(df, resamples=500, seed=1),
    ):
        assert lo <= mean <= hi
        assert lo < hi


def test_effective_sample_size_drops_with_correlated_pairs() -> None:
    # Two pairs with IDENTICAL daily net-R -> rho_bar ~1 -> effective_n < raw_n.
    rng = np.random.default_rng(3)
    ts = pd.Timestamp("2023-01-02", tz="UTC") + pd.to_timedelta(np.arange(120), unit="h")
    net = rng.normal(0, 1, size=120)
    a = pd.DataFrame({"pair": "A", "timestamp": ts, "score": 60.0, "gross_r": net + 0.02, "net_r": net, "exit_reason": "x"})
    b = a.assign(pair="B")  # perfectly correlated clone
    df = pd.concat([a, b], ignore_index=True)
    ess = effective_sample_size(df)
    assert ess["raw_n"] == 240
    assert ess["mean_pairwise_daily_corr"] > 0.9
    assert ess["effective_n"] < ess["raw_n"]


def test_effective_sample_size_single_pair_no_deflation() -> None:
    df = _registry(pairs=1, per_pair=100)
    ess = effective_sample_size(df)
    assert ess["n_pairs"] == 1
    assert ess["design_effect"] == 1.0
    assert ess["effective_n"] == ess["raw_n"]


def test_per_pair_metrics_aggregates_by_pair() -> None:
    df = _registry(pairs=2, per_pair=50)
    rows = per_pair_metrics(df, resamples=300, seed=2)
    assert [r["pair"] for r in rows] == ["P0", "P1"]
    for r in rows:
        assert r["n"] == 50
        assert r["net_ci"][0] <= r["net_exp"] <= r["net_ci"][1]


def test_half_split_robustness_splits_by_time() -> None:
    df = _registry(pairs=1, per_pair=100)
    h = half_split_robustness(df, resamples=300, seed=2)
    assert h["first_half"]["n"] == 50 and h["second_half"]["n"] == 50
    assert isinstance(h["both_halves_net_positive"], bool)
    assert h["first_half"]["end"] <= h["second_half"]["start"]


def test_decompose_returns_grouped_cis_and_calibration() -> None:
    df = _registry(pairs=3, per_pair=60)
    result = decompose(df, resamples=300, n_buckets=5)
    assert result["n_oos_trades"] == 180
    assert set(result["pairs"]) == {"P0", "P1", "P2"}
    # Grouped conservative CI must be at least as wide as either component CI.
    cl = result["net_ci_cluster_by_pair"]; bl = result["net_ci_block_bootstrap"]
    gr = result["net_ci_grouped_conservative"]
    assert gr[0] <= min(cl[0], bl[0]) + 1e-9 and gr[1] >= max(cl[1], bl[1]) - 1e-9
    assert "calibration" in result and "per_pair_spearman" in result["calibration"]
    assert abs(result["avg_cost_r"] - 0.02) < 0.01


def test_spearman_ci_positive_for_monotone_relation() -> None:
    x = pd.Series(np.arange(100, dtype=float))
    y = x + np.random.default_rng(0).normal(0, 5, size=100)  # strongly increasing
    rho, lo, hi = spearman_ci(x, y, resamples=500, seed=0)
    assert rho > 0.8
    assert lo > 0  # CI excludes zero on the positive side


def test_build_report_from_frame_consumes_registry_columns() -> None:
    df = _registry(pairs=2, per_pair=60).rename(columns={"score": "final_score"})[["final_score", "net_r"]]
    report = build_report_from_frame(df, n_buckets=5, bootstrap_resamples=200)
    assert report.scored_trades == 120
    assert len(report.buckets) == 5
