"""Grouped-bootstrap decomposition of a deduplicated OOS trade registry.

Consumes the registry written by the walk-forward harness
(``oos_trade_registry.csv``: ``pair, timestamp, score, gross_r, net_r,
exit_reason``) and produces an honest statistical decomposition **without any
re-backtest**:

* gross vs net expectancy and cost/trade;
* **grouped** 95% bootstrap CIs that respect correlation, two ways —
  (a) cluster bootstrap by pair, (b) temporal moving-block bootstrap;
* an **effective sample size** estimate for correlated majors (design-effect
  approximation; the grouped CIs remain the primary inference);
* per-pair metrics (expectancy, CI, N, win, profit factor);
* robustness across the first vs second half of the period.

Reporting only; paper/demo. No orders are sent.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.reporting.score_expectancy import build_report_from_frame, spearman_ci

REGISTRY_COLUMNS = ["pair", "timestamp", "score", "gross_r", "net_r", "exit_reason"]
DEFAULT_BLOCK_SIZE = 20  # trades per temporal block (must exceed the autocorr horizon)


def load_registry(path: Path) -> pd.DataFrame:
    """Load and validate the OOS trade registry CSV."""

    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REGISTRY_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"registry '{path}' missing column(s): {', '.join(missing)}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    for column in ("score", "gross_r", "net_r"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["timestamp", "gross_r", "net_r"]).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        raise ValueError(f"registry '{path}' has no usable rows")
    return df


def _percentile_ci(samples: np.ndarray, confidence: float) -> tuple[float, float]:
    alpha = (1.0 - confidence) / 2.0
    return round(float(np.quantile(samples, alpha)), 4), round(float(np.quantile(samples, 1.0 - alpha)), 4)


def iid_bootstrap_ci(values: np.ndarray, *, resamples: int = 2000, confidence: float = 0.95, seed: int = 1729) -> tuple[float, float]:
    """Plain percentile bootstrap CI of the mean (assumes i.i.d. draws)."""

    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return 0.0, 0.0
    if values.size == 1:
        return round(float(values[0]), 4), round(float(values[0]), 4)
    rng = np.random.default_rng(seed)
    means = values[rng.integers(0, values.size, size=(resamples, values.size))].mean(axis=1)
    return _percentile_ci(means, confidence)


def cluster_bootstrap_ci(
    df: pd.DataFrame,
    value_col: str = "net_r",
    group_col: str = "pair",
    *,
    resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 1729,
) -> tuple[float, float]:
    """Cluster bootstrap CI: resample whole clusters (pairs) with replacement.

    Captures between-pair (cross-sectional) correlation: simultaneous trades on
    correlated majors are not independent, so we resample at the pair level.
    """

    clusters = [group[value_col].to_numpy(dtype=float) for _, group in df.groupby(group_col)]
    clusters = [c for c in clusters if c.size]
    if len(clusters) <= 1:
        return iid_bootstrap_ci(df[value_col].to_numpy(), resamples=resamples, confidence=confidence, seed=seed)
    rng = np.random.default_rng(seed)
    k = len(clusters)
    means = np.empty(resamples)
    for i in range(resamples):
        drawn = [clusters[j] for j in rng.integers(0, k, size=k)]
        means[i] = np.concatenate(drawn).mean()
    return _percentile_ci(means, confidence)


def block_bootstrap_ci(
    df: pd.DataFrame,
    value_col: str = "net_r",
    *,
    block_size: int = DEFAULT_BLOCK_SIZE,
    resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 1729,
) -> tuple[float, float]:
    """Moving-block bootstrap CI over time-ordered trades.

    Preserves serial correlation by resampling contiguous blocks (length
    ``block_size`` trades) rather than individual trades. ``df`` must be sorted by
    timestamp (``load_registry`` guarantees this).
    """

    values = df[value_col].to_numpy(dtype=float)
    n = values.size
    if n <= 1:
        return iid_bootstrap_ci(values, resamples=resamples, confidence=confidence, seed=seed)
    block = max(1, min(block_size, n))
    n_blocks = int(np.ceil(n / block))
    max_start = n - block
    rng = np.random.default_rng(seed)
    means = np.empty(resamples)
    for i in range(resamples):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        sample = np.concatenate([values[s : s + block] for s in starts])[:n]
        means[i] = sample.mean()
    return _percentile_ci(means, confidence)


def effective_sample_size(df: pd.DataFrame) -> dict[str, object]:
    """Approximate effective N for correlated majors (design-effect heuristic).

    Builds each pair's daily-mean net-R series, aligns them on common days, and
    takes the mean off-diagonal Pearson correlation ``rho_bar``. With ``k_bar``
    pairs active per day on average, the design effect is
    ``DEFF = 1 + (k_bar - 1) * max(rho_bar, 0)`` and ``N_eff = N / DEFF``.

    This is a documented approximation; the grouped bootstrap CIs (cluster +
    block) are the primary inference and already widen for this correlation.
    """

    n = int(len(df))
    pairs = sorted(df["pair"].unique())
    day = df["timestamp"].dt.floor("D")
    daily = df.assign(day=day).groupby(["pair", "day"])["net_r"].mean().unstack("pair")
    rho_bar = 0.0
    if daily.shape[1] >= 2:
        corr = daily.corr(min_periods=5).to_numpy()
        off = corr[~np.eye(corr.shape[0], dtype=bool)]
        off = off[np.isfinite(off)]
        rho_bar = float(off.mean()) if off.size else 0.0
    counts = df.assign(day=day).groupby("day")["pair"].nunique()
    k_bar = float(counts.mean()) if len(counts) else 1.0
    deff = 1.0 + (k_bar - 1.0) * max(rho_bar, 0.0)
    n_eff = n / deff if deff > 0 else float(n)
    return {
        "raw_n": n,
        "n_pairs": len(pairs),
        "mean_pairwise_daily_corr": round(rho_bar, 4),
        "avg_concurrent_pairs_per_day": round(k_bar, 4),
        "design_effect": round(deff, 4),
        "effective_n": int(round(n_eff)),
    }


def _profit_factor(net: np.ndarray) -> float:
    wins = net[net > 0].sum()
    losses = abs(net[net < 0].sum())
    return round(float(wins / losses), 4) if losses > 0 else round(float(wins), 4)


def per_pair_metrics(df: pd.DataFrame, *, resamples: int = 2000, confidence: float = 0.95, seed: int = 1729) -> list[dict[str, object]]:
    """Per-pair expectancy, IID bootstrap CI, N, win rate, profit factor."""

    rows: list[dict[str, object]] = []
    for pair, group in df.groupby("pair"):
        net = group["net_r"].to_numpy(dtype=float)
        gross = group["gross_r"].to_numpy(dtype=float)
        lo, hi = iid_bootstrap_ci(net, resamples=resamples, confidence=confidence, seed=seed)
        rows.append(
            {
                "pair": str(pair),
                "n": int(net.size),
                "gross_exp": round(float(gross.mean()), 4),
                "net_exp": round(float(net.mean()), 4),
                "net_ci": [lo, hi],
                "win_rate": round(float((net > 0).mean() * 100.0), 2),
                "profit_factor": _profit_factor(net),
            }
        )
    return sorted(rows, key=lambda r: r["pair"])


def half_split_robustness(df: pd.DataFrame, *, resamples: int = 2000, confidence: float = 0.95, seed: int = 1729) -> dict[str, object]:
    """Net expectancy + CI on the first vs second half of the period (by time)."""

    ordered = df.sort_values("timestamp").reset_index(drop=True)
    mid = len(ordered) // 2
    halves = {"first_half": ordered.iloc[:mid], "second_half": ordered.iloc[mid:]}
    out: dict[str, object] = {}
    for name, part in halves.items():
        net = part["net_r"].to_numpy(dtype=float)
        lo, hi = iid_bootstrap_ci(net, resamples=resamples, confidence=confidence, seed=seed)
        span = (part["timestamp"].min(), part["timestamp"].max()) if len(part) else (None, None)
        out[name] = {
            "n": int(net.size),
            "net_exp": round(float(net.mean()), 4) if net.size else 0.0,
            "net_ci": [lo, hi],
            "start": span[0].isoformat() if span[0] is not None else None,
            "end": span[1].isoformat() if span[1] is not None else None,
        }
    out["both_halves_net_positive"] = bool(
        out["first_half"]["net_exp"] > 0 and out["second_half"]["net_exp"] > 0
    )
    return out


def decompose(
    df: pd.DataFrame,
    *,
    resamples: int = 2000,
    confidence: float = 0.95,
    block_size: int = DEFAULT_BLOCK_SIZE,
    n_buckets: int = 10,
    seed: int = 1729,
) -> dict[str, object]:
    """Full decomposition dictionary from a loaded registry frame."""

    gross = df["gross_r"].to_numpy(dtype=float)
    net = df["net_r"].to_numpy(dtype=float)
    cost = gross - net
    gross_lo, gross_hi = iid_bootstrap_ci(gross, resamples=resamples, confidence=confidence, seed=seed)
    net_lo, net_hi = iid_bootstrap_ci(net, resamples=resamples, confidence=confidence, seed=seed)
    cl_lo, cl_hi = cluster_bootstrap_ci(df, "net_r", "pair", resamples=resamples, confidence=confidence, seed=seed)
    bl_lo, bl_hi = block_bootstrap_ci(df, "net_r", block_size=block_size, resamples=resamples, confidence=confidence, seed=seed)
    # Most conservative (widest) grouped CI governs the verdict.
    grouped_lo = min(cl_lo, bl_lo)
    grouped_hi = max(cl_hi, bl_hi)

    calib_frame = df.rename(columns={"score": "final_score"})[["final_score", "net_r"]]
    calib = build_report_from_frame(calib_frame, n_buckets=n_buckets, bootstrap_resamples=resamples, seed=seed)
    rho, rho_lo, rho_hi = spearman_ci(df["score"], df["net_r"], resamples=resamples, confidence=confidence, seed=seed)

    pair_spearman = {}
    for pair, group in df.groupby("pair"):
        r, lo, hi = spearman_ci(group["score"], group["net_r"], resamples=resamples, confidence=confidence, seed=seed)
        pair_spearman[str(pair)] = {"spearman": r, "ci": [lo, hi], "n": int(len(group))}

    return {
        "n_oos_trades": int(len(df)),
        "pairs": sorted(df["pair"].unique().tolist()),
        "period": [df["timestamp"].min().isoformat(), df["timestamp"].max().isoformat()],
        "gross_expectancy": round(float(gross.mean()), 4),
        "gross_ci_iid": [gross_lo, gross_hi],
        "net_expectancy": round(float(net.mean()), 4),
        "net_ci_iid": [net_lo, net_hi],
        "net_ci_cluster_by_pair": [cl_lo, cl_hi],
        "net_ci_block_bootstrap": [bl_lo, bl_hi],
        "net_ci_grouped_conservative": [grouped_lo, grouped_hi],
        "avg_cost_r": round(float(cost.mean()), 4),
        "net_std": round(float(net.std(ddof=1)), 4) if net.size > 1 else 0.0,
        "win_rate": round(float((net > 0).mean() * 100.0), 2),
        "profit_factor": _profit_factor(net),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
        "effective_sample_size": effective_sample_size(df),
        "per_pair": per_pair_metrics(df, resamples=resamples, confidence=confidence, seed=seed),
        "robustness_halves": half_split_robustness(df, resamples=resamples, confidence=confidence, seed=seed),
        "calibration": {
            "monotonic_non_decreasing": calib.monotonic_non_decreasing,
            "spearman": rho,
            "spearman_ci": [rho_lo, rho_hi],
            "flagged_components": calib.flagged_components,
            "buckets": [
                {"label": b.label, "n": b.samples, "score_min": b.score_min, "score_max": b.score_max,
                 "expectancy": b.expectancy, "ci": [b.ci_low, b.ci_high], "win_rate": b.win_rate}
                for b in calib.buckets
            ],
            "per_pair_spearman": pair_spearman,
        },
        "block_size": block_size,
    }


def report_to_text(result: dict[str, object]) -> str:
    g = result
    lines = [
        "Edge decomposition (OOS registry — paper-only)",
        "==============================================",
        f"pairs           : {', '.join(g['pairs'])}",
        f"period          : {g['period'][0]} -> {g['period'][1]}",
        f"OOS trades (raw): {g['n_oos_trades']}",
        f"effective N     : {g['effective_sample_size']['effective_n']} "
        f"(DEFF={g['effective_sample_size']['design_effect']}, "
        f"rho_bar={g['effective_sample_size']['mean_pairwise_daily_corr']})",
        "",
        f"gross expectancy: {g['gross_expectancy']:+.4f} R  CI(iid) {g['gross_ci_iid']}",
        f"net expectancy  : {g['net_expectancy']:+.4f} R  CI(iid) {g['net_ci_iid']}",
        f"  cluster-by-pair CI : {g['net_ci_cluster_by_pair']}",
        f"  block-bootstrap CI : {g['net_ci_block_bootstrap']}",
        f"  GROUPED (governs)  : {g['net_ci_grouped_conservative']}",
        f"cost/trade      : {g['avg_cost_r']:.4f} R   net_std={g['net_std']}",
        f"win/PF          : {g['win_rate']:.2f}% / {g['profit_factor']}",
        f"exit reasons    : {g['exit_reasons']}",
        "",
        "Per-pair net expectancy:",
    ]
    for row in g["per_pair"]:
        lines.append(f"  {row['pair']:<10} n={row['n']:<5} net={row['net_exp']:+.4f} CI={row['net_ci']} win={row['win_rate']:.1f}% PF={row['profit_factor']}")
    h = g["robustness_halves"]
    lines += [
        "",
        "Robustness (halves):",
        f"  first : n={h['first_half']['n']} net={h['first_half']['net_exp']:+.4f} CI={h['first_half']['net_ci']}",
        f"  second: n={h['second_half']['n']} net={h['second_half']['net_exp']:+.4f} CI={h['second_half']['net_ci']}",
        f"  both halves net positive: {h['both_halves_net_positive']}",
        "",
        f"Calibration: monotonic={g['calibration']['monotonic_non_decreasing']} "
        f"spearman={g['calibration']['spearman']} CI={g['calibration']['spearman_ci']} "
        f"flagged={g['calibration']['flagged_components']}",
    ]
    return "\n".join(lines) + "\n"


def write_reports(result: dict[str, object], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "edge_decomposition.json"
    text_path = output_dir / "edge_decomposition.txt"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text_path.write_text(report_to_text(result), encoding="utf-8")
    return {"json": json_path, "txt": text_path}
