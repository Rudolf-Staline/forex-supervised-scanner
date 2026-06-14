"""Backtest metric tests."""

from datetime import datetime, timezone

from app.backtest.metrics import calculate_metrics
from app.backtest.setup_report import summarize_setups
from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradeRecord, TradingStyle


def _trade(net_r: float, *, symbol: str = "EUR/USD", subtype: SetupSubtype = SetupSubtype.EMA50_PULLBACK) -> TradeRecord:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return TradeRecord(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=subtype,
        direction=DirectionBias.LONG,
        entry_time=now,
        exit_time=now,
        entry=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        exit_price=1.11 if net_r > 0 else 1.095,
        gross_r=net_r,
        net_r=net_r,
        exit_reason="take_profit" if net_r > 0 else "stop_loss",
        cost_pips=1.0,
    )


def test_metrics_calculate_profit_factor_and_drawdown() -> None:
    metrics = calculate_metrics([_trade(1.4), _trade(-1.0), _trade(2.0)])
    assert metrics.number_of_trades == 3
    assert metrics.win_rate == 66.67
    assert metrics.profit_factor == 3.4
    assert metrics.max_drawdown == 1.0


def test_metrics_expose_sample_size_independent_and_distribution_stats() -> None:
    trades = [_trade(value) for value in (2.0, -1.0, 1.5, -1.0, 0.5, -1.0, 3.0, -1.0)]
    metrics = calculate_metrics(trades)

    returns = [trade.net_r for trade in trades]
    mean = sum(returns) / len(returns)

    # Per-trade Sharpe is a plain mean/std ratio (no sqrt(N) inflation).
    assert metrics.sharpe_per_trade != 0.0
    assert abs(metrics.sharpe_per_trade) < abs(metrics.sharpe_like)
    # Annualized variant scales the per-trade Sharpe by sqrt(assumed trades/year).
    assert metrics.annualization_trades_per_year == 252.0
    expected_annualized = metrics.sharpe_per_trade * (252.0 ** 0.5)
    assert abs(metrics.sharpe_annualized - expected_annualized) < 1e-2
    # Distributional stats.
    assert metrics.max_drawdown_r == metrics.max_drawdown
    assert metrics.r_percentile_10 <= metrics.median_r <= metrics.r_percentile_90
    assert metrics.r_percentile_25 <= metrics.median_r <= metrics.r_percentile_75
    # Bootstrap CI brackets the realized expectancy.
    assert metrics.expectancy_ci_low <= round(mean, 4) <= metrics.expectancy_ci_high


def test_sharpe_like_grows_with_sample_size_but_sharpe_per_trade_is_stable() -> None:
    pattern = [1.0, -1.0, 2.0, -1.0]
    small = calculate_metrics([_trade(value) for value in pattern])
    large = calculate_metrics([_trade(value) for value in pattern * 10])

    # Deprecated metric inflates with N (~sqrt(10)x); the per-trade ratio is
    # near-invariant (only the sample-std ddof correction moves it slightly).
    assert large.sharpe_like > 2.5 * small.sharpe_like
    assert abs(large.sharpe_per_trade - small.sharpe_per_trade) < 0.05


def test_setup_report_groups_metrics_by_setup_subtype() -> None:
    summaries = summarize_setups(
        [
            _trade(1.4, symbol="EUR/USD", subtype=SetupSubtype.EMA50_PULLBACK),
            _trade(-1.0, symbol="GBP/USD", subtype=SetupSubtype.EMA50_PULLBACK),
            _trade(2.0, symbol="EUR/USD", subtype=SetupSubtype.MOMENTUM_BREAKOUT),
        ]
    )

    by_setup = {summary.setup: summary for summary in summaries}
    ema = by_setup["ema50_pullback"]
    assert ema.total_trades == 2
    assert ema.wins == 1
    assert ema.losses == 1
    assert ema.best_symbol == "EUR/USD"
    assert ema.worst_symbol == "GBP/USD"
    assert by_setup["momentum_breakout"].profit_factor == 2.0
