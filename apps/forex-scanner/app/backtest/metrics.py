"""Backtest metric calculations."""

from __future__ import annotations

import math

import numpy as np

from app.core.types import BacktestMetrics, TradeRecord

# Explicit, documented annualization assumption: per-trade Sharpe is scaled by
# sqrt(trades-per-year). 252 mirrors the count of trading days in a year and is a
# rough comparison aid only -- it does NOT model trade clustering or autocorrelation.
DEFAULT_TRADES_PER_YEAR = 252.0
_BOOTSTRAP_RESAMPLES = 2000
_BOOTSTRAP_SEED = 20240601


def calculate_metrics(trades: list[TradeRecord], *, trades_per_year: float = DEFAULT_TRADES_PER_YEAR) -> BacktestMetrics:
    """Calculate performance metrics from completed trades.

    All R-denominated figures use net R per trade. See :class:`BacktestMetrics`
    for the deprecation note on ``sharpe_like``.
    """

    if not trades:
        return BacktestMetrics(
            win_rate=0.0,
            average_win=0.0,
            average_loss=0.0,
            profit_factor=0.0,
            max_drawdown=0.0,
            expectancy=0.0,
            number_of_trades=0,
            sharpe_like=0.0,
            sharpe_per_trade=0.0,
            sharpe_annualized=0.0,
            annualization_trades_per_year=trades_per_year,
        )

    returns = np.array([trade.net_r for trade in trades], dtype=float)
    wins = returns[returns > 0.0]
    losses = returns[returns < 0.0]
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(abs(losses.sum())) if losses.size else 0.0
    equity = np.cumsum(returns)
    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity
    max_drawdown = round(float(drawdown.max()) if drawdown.size else 0.0, 4)
    mean = float(returns.mean())
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0

    # Deprecated: grows with sqrt(N) and is not comparable across run sizes.
    sharpe_like = float(mean / std * math.sqrt(len(returns))) if std > 0.0 else 0.0
    # Sample-size-independent per-trade Sharpe and its annualized projection.
    sharpe_per_trade = float(mean / std) if std > 0.0 else 0.0
    sharpe_annualized = sharpe_per_trade * math.sqrt(trades_per_year)
    ci_low, ci_high = _bootstrap_mean_ci(returns)

    return BacktestMetrics(
        win_rate=round(float((returns > 0.0).mean() * 100.0), 2),
        average_win=round(float(wins.mean()) if wins.size else 0.0, 4),
        average_loss=round(float(losses.mean()) if losses.size else 0.0, 4),
        profit_factor=round(gross_win / gross_loss, 4) if gross_loss > 0.0 else round(gross_win, 4),
        max_drawdown=max_drawdown,
        expectancy=round(mean, 4),
        number_of_trades=len(trades),
        sharpe_like=round(sharpe_like, 4),
        sharpe_per_trade=round(sharpe_per_trade, 4),
        sharpe_annualized=round(sharpe_annualized, 4),
        annualization_trades_per_year=trades_per_year,
        expectancy_ci_low=ci_low,
        expectancy_ci_high=ci_high,
        median_r=round(float(np.median(returns)), 4),
        r_percentile_10=round(float(np.percentile(returns, 10)), 4),
        r_percentile_25=round(float(np.percentile(returns, 25)), 4),
        r_percentile_75=round(float(np.percentile(returns, 75)), 4),
        r_percentile_90=round(float(np.percentile(returns, 90)), 4),
        max_drawdown_r=max_drawdown,
    )


def _bootstrap_mean_ci(
    returns: np.ndarray,
    *,
    resamples: int = _BOOTSTRAP_RESAMPLES,
    confidence: float = 0.95,
    seed: int = _BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Deterministic percentile-bootstrap confidence interval for expectancy."""

    if returns.size <= 1:
        value = round(float(returns.mean()) if returns.size else 0.0, 4)
        return value, value
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, returns.size, size=(resamples, returns.size))
    means = returns[indices].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    return round(float(np.quantile(means, alpha)), 4), round(float(np.quantile(means, 1.0 - alpha)), 4)

