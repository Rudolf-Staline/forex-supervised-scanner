"""Backtest metric tests."""

from datetime import datetime, timezone

from app.backtest.metrics import calculate_metrics
from app.core.types import DirectionBias, SetupFamily, TradeRecord, TradingStyle


def _trade(net_r: float) -> TradeRecord:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return TradeRecord(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
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

