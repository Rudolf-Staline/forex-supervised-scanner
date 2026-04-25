"""Backtest metric calculations."""

from __future__ import annotations

import math

import numpy as np

from app.core.types import BacktestMetrics, TradeRecord


def calculate_metrics(trades: list[TradeRecord]) -> BacktestMetrics:
    """Calculate V1 performance metrics from completed trades."""

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
        )

    returns = np.array([trade.net_r for trade in trades], dtype=float)
    wins = returns[returns > 0.0]
    losses = returns[returns < 0.0]
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(abs(losses.sum())) if losses.size else 0.0
    equity = np.cumsum(returns)
    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe_like = float(returns.mean() / std * math.sqrt(len(returns))) if std > 0.0 else 0.0

    return BacktestMetrics(
        win_rate=round(float((returns > 0.0).mean() * 100.0), 2),
        average_win=round(float(wins.mean()) if wins.size else 0.0, 4),
        average_loss=round(float(losses.mean()) if losses.size else 0.0, 4),
        profit_factor=round(gross_win / gross_loss, 4) if gross_loss > 0.0 else round(gross_win, 4),
        max_drawdown=round(float(drawdown.max()) if drawdown.size else 0.0, 4),
        expectancy=round(float(returns.mean()), 4),
        number_of_trades=len(trades),
        sharpe_like=round(sharpe_like, 4),
    )

