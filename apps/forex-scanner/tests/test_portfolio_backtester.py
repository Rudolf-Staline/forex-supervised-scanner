"""Deterministic tests for portfolio-level historical allocation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.portfolio import (
    PortfolioConstraints,
    currency_exposure,
    simulate_portfolio,
    split_symbol,
    trade_currency_exposure,
)
from app.core.types import (
    DirectionBias,
    SetupFamily,
    SetupSubtype,
    TradeRecord,
    TradingStyle,
)

BASE = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def _trade(
    symbol: str,
    direction: DirectionBias,
    *,
    entry_hours: float,
    exit_hours: float,
    net_r: float,
    score: float,
) -> TradeRecord:
    entry = BASE + timedelta(hours=entry_hours)
    exit_ = BASE + timedelta(hours=exit_hours)
    is_win = net_r > 0.0
    return TradeRecord(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=direction,
        entry_time=entry,
        exit_time=exit_,
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        exit_price=1.1100 if is_win else 1.0950,
        gross_r=net_r,
        net_r=net_r,
        exit_reason="take_profit" if is_win else "stop_loss",
        cost_pips=1.0,
        technical_score=score,
        final_score=score,
    )


def test_same_timestamp_candidates_are_ranked_before_concurrency_limit() -> None:
    candidates = [
        _trade("AUD/USD", DirectionBias.LONG, entry_hours=0, exit_hours=3, net_r=0.5, score=65),
        _trade("EUR/JPY", DirectionBias.LONG, entry_hours=0, exit_hours=3, net_r=0.5, score=90),
        _trade("GBP/CHF", DirectionBias.SHORT, entry_hours=0, exit_hours=3, net_r=0.5, score=80),
    ]
    result = simulate_portfolio(
        candidates,
        PortfolioConstraints(
            max_concurrent_positions=2,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    assert [trade.symbol for trade in result.accepted_trades] == ["EUR/JPY", "GBP/CHF"]
    assert len(result.rejected_trades) == 1
    assert result.rejected_trades[0].trade.symbol == "AUD/USD"
    assert result.rejected_trades[0].reason == "max_concurrent_positions"


def test_currency_exposure_blocks_stacked_directional_usd_bets() -> None:
    candidates = [
        _trade("EUR/USD", DirectionBias.LONG, entry_hours=0, exit_hours=3, net_r=0.4, score=90),
        _trade("GBP/USD", DirectionBias.LONG, entry_hours=0, exit_hours=3, net_r=0.4, score=80),
    ]
    result = simulate_portfolio(
        candidates,
        PortfolioConstraints(
            max_concurrent_positions=3,
            max_abs_currency_exposure=1,
            daily_loss_limit_r=None,
        ),
    )

    assert [trade.symbol for trade in result.accepted_trades] == ["EUR/USD"]
    assert result.rejected_trades[0].reason == "currency_exposure_limit"
    assert "USD=-2" in result.rejected_trades[0].detail


def test_position_exiting_at_entry_timestamp_frees_slot() -> None:
    candidates = [
        _trade("EUR/USD", DirectionBias.LONG, entry_hours=0, exit_hours=1, net_r=0.5, score=80),
        _trade("GBP/JPY", DirectionBias.SHORT, entry_hours=1, exit_hours=2, net_r=0.5, score=70),
    ]
    result = simulate_portfolio(
        candidates,
        PortfolioConstraints(
            max_concurrent_positions=1,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    assert len(result.accepted_trades) == 2
    assert not result.rejected_trades


def test_same_symbol_overlap_is_rejected() -> None:
    candidates = [
        _trade("EUR/USD", DirectionBias.LONG, entry_hours=0, exit_hours=3, net_r=0.5, score=90),
        _trade("EUR/USD", DirectionBias.SHORT, entry_hours=1, exit_hours=2, net_r=0.5, score=80),
    ]
    result = simulate_portfolio(
        candidates,
        PortfolioConstraints(
            max_concurrent_positions=3,
            max_same_symbol_positions=1,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    assert len(result.accepted_trades) == 1
    assert result.rejected_trades[0].reason == "same_symbol_limit"


def test_daily_loss_limit_uses_only_losses_realized_before_entry() -> None:
    candidates = [
        _trade("EUR/USD", DirectionBias.LONG, entry_hours=0, exit_hours=1, net_r=-2.0, score=90),
        _trade("GBP/JPY", DirectionBias.LONG, entry_hours=2, exit_hours=3, net_r=1.0, score=80),
    ]
    result = simulate_portfolio(
        candidates,
        PortfolioConstraints(
            max_abs_currency_exposure=None,
            daily_loss_limit_r=1.5,
        ),
    )

    assert len(result.accepted_trades) == 1
    rejection = result.rejected_trades[0]
    assert rejection.reason == "daily_loss_limit"
    assert rejection.realized_day_r == -2.0


def test_unrealized_future_loss_does_not_block_earlier_candidate() -> None:
    candidates = [
        _trade("EUR/USD", DirectionBias.LONG, entry_hours=0, exit_hours=4, net_r=-3.0, score=90),
        _trade("GBP/JPY", DirectionBias.LONG, entry_hours=2, exit_hours=3, net_r=1.0, score=80),
    ]
    result = simulate_portfolio(
        candidates,
        PortfolioConstraints(
            max_concurrent_positions=3,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=1.0,
        ),
    )

    assert len(result.accepted_trades) == 2
    assert not result.rejected_trades


def test_metrics_and_equity_use_only_accepted_trades() -> None:
    candidates = [
        _trade("EUR/USD", DirectionBias.LONG, entry_hours=0, exit_hours=2, net_r=1.0, score=90),
        _trade("GBP/JPY", DirectionBias.LONG, entry_hours=0, exit_hours=1, net_r=-1.0, score=80),
    ]
    result = simulate_portfolio(
        candidates,
        PortfolioConstraints(
            max_concurrent_positions=1,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    assert result.metrics.number_of_trades == 1
    assert result.metrics.expectancy == 1.0
    assert result.equity_curve == [(BASE + timedelta(hours=2), 1.0)]
    assert result.rejection_counts == {"max_concurrent_positions": 1}


def test_currency_exposure_helpers_are_directionally_consistent() -> None:
    long_eurusd = _trade(
        "EUR/USD", DirectionBias.LONG, entry_hours=0, exit_hours=1, net_r=0.5, score=80
    )
    short_gbpusd = _trade(
        "GBPUSD", DirectionBias.SHORT, entry_hours=0, exit_hours=1, net_r=0.5, score=70
    )

    assert split_symbol("EUR/USD") == ("EUR", "USD")
    assert split_symbol("XAUUSD") == ("XAU", "USD")
    assert trade_currency_exposure(long_eurusd) == {"EUR": 1, "USD": -1}
    assert trade_currency_exposure(short_gbpusd) == {"GBP": -1, "USD": 1}
    assert currency_exposure([long_eurusd, short_gbpusd]) == {"EUR": 1, "GBP": -1}


def test_invalid_trade_interval_fails_loudly() -> None:
    invalid = _trade(
        "EUR/USD", DirectionBias.LONG, entry_hours=2, exit_hours=1, net_r=-1.0, score=80
    )
    with pytest.raises(ValueError, match="exits before entry"):
        simulate_portfolio([invalid])


def test_invalid_constraints_fail_loudly() -> None:
    with pytest.raises(ValueError, match="max_concurrent_positions"):
        PortfolioConstraints(max_concurrent_positions=0)
    with pytest.raises(ValueError, match="daily_loss_limit_r"):
        PortfolioConstraints(daily_loss_limit_r=0.0)
