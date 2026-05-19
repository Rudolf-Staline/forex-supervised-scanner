"""UI helper tests that avoid launching a Streamlit browser session."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.types import (
    ConfidenceBucket,
    DirectionBias,
    GateBreakdown,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    PriceLevel,
    RejectionCategory,
    SessionName,
    SetupGrade,
    SetupFamily,
    SetupSubtype,
    Timeframe,
    TradeRecord,
    TradingStyle,
)
from app.indicators.calculations import add_indicators
from app.indicators.levels import LevelSet
from app.ui.streamlit_app import _backtest_equity_frame, _backtest_family_performance, _backtest_trade_table, _build_chart, _filter_backtest_trades, _fmt_price, _gate_table, _opportunity_label, _opportunity_table
from tests.conftest import make_ohlcv


def _opportunity(direction: DirectionBias = DirectionBias.LONG) -> Opportunity:
    no_trade = direction == DirectionBias.NO_TRADE
    return Opportunity(
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.NO_TRADE if no_trade else SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.BREAKOUT_RETEST if no_trade else SetupSubtype.SHALLOW_EMA20_PULLBACK,
        regime=MarketRegime.RANGING if no_trade else MarketRegime.TRENDING_UP,
        direction=direction,
        score=52.5 if no_trade else 72.5,
        confidence=ConfidenceBucket.LOW if no_trade else ConfidenceBucket.MEDIUM,
        entry=None if no_trade else 1.1,
        stop_loss=None if no_trade else 1.095,
        take_profit=None if no_trade else 1.109,
        risk_reward=None if no_trade else 1.8,
        explanation="No ranked setup: mixed evidence" if no_trade else "Bullish pullback recovered.",
        timeframe_higher=Timeframe.H1,
        timeframe_entry=Timeframe.M15,
        timeframe_trigger=Timeframe.M5,
        score_components={} if no_trade else {"trend_clarity": 72.0},
        provider="synthetic",
        rejection_reason="mixed evidence" if no_trade else None,
        approved=not no_trade,
        status=OpportunityStatus.WATCHLIST if no_trade else OpportunityStatus.APPROVED,
        raw_setup_family=SetupFamily.BREAKOUT_CONFIRMATION if no_trade else SetupFamily.TREND_CONTINUATION,
        pre_gate_score=52.5 if no_trade else 72.5,
        technical_score=61.0 if no_trade else 74.0,
        execution_score=44.0 if no_trade else 66.0,
        context_score=58.0 if no_trade else 72.0,
        empirical_score=55.0,
        final_score=52.5 if no_trade else 72.5,
        grade=SetupGrade.C if no_trade else SetupGrade.A,
        gate_breakdown=GateBreakdown(
            trend=True,
            structure=True,
            momentum=False if no_trade else True,
            volatility=True,
            multi_timeframe_alignment=True,
            minimum_rr=True,
            score_threshold=False if no_trade else True,
        ),
        failed_gates=["momentum", "score threshold"] if no_trade else [],
        rejection_category=RejectionCategory.WEAK_MOMENTUM if no_trade else None,
        required_min_rr=1.5,
        missing_conditions=["momentum", "score threshold"] if no_trade else [],
        invalidation="test invalidation",
        tp1=None if no_trade else 1.105,
        tp2=None if no_trade else 1.109,
        tp3=None if no_trade else 1.113,
        activation_quality=49.0 if no_trade else 82.0,
        invalidation_quality=57.0 if no_trade else 77.0,
        spread=0.0001,
        atr=0.0012,
        key_level_distances={"setup_level_atr": 0.4},
        session=SessionName.LONDON,
        htf_regime=MarketRegime.RANGING if no_trade else MarketRegime.TRENDING_UP,
        entry_regime=MarketRegime.RANGING if no_trade else MarketRegime.TRENDING_UP,
        trigger_regime=MarketRegime.TRANSITION if no_trade else MarketRegime.TRENDING_UP,
    )


def test_opportunity_table_includes_no_trade_reason() -> None:
    table = _opportunity_table([_opportunity(DirectionBias.NO_TRADE)])
    assert table.loc[0, "status_badge"] == "[WATCHLIST]"
    assert table.loc[0, "status"] == "watchlist"
    assert table.loc[0, "direction"] == "no-trade"
    assert table.loc[0, "no_trade_reason"] == "mixed evidence"
    assert table.loc[0, "raw_setup"] == "breakout_confirmation"
    assert table.loc[0, "subtype"] == "breakout_retest"
    assert table.loc[0, "context_score"] == 58.0
    assert table.loc[0, "empirical_score"] == 55.0
    assert table.loc[0, "session"] == "london"
    assert table.loc[0, "activation_quality"] == 49.0
    assert table.loc[0, "pre_gate_score"] == 52.5
    assert table.loc[0, "technical_score"] == 61.0
    assert table.loc[0, "execution_score"] == 44.0
    assert table.loc[0, "failed_gates"] == "momentum, score threshold"
    assert table.loc[0, "rejection_category"] == "weak momentum"


def test_opportunity_label_handles_trade_and_no_trade() -> None:
    assert _opportunity_label(_opportunity(DirectionBias.NO_TRADE)) == "EUR/USD: watchlist breakout_retest (52.5)"
    assert "approved long shallow_ema20_pullback" in _opportunity_label(_opportunity(DirectionBias.LONG))


def test_gate_table_serializes_pass_fail_status() -> None:
    table = _gate_table(_opportunity(DirectionBias.NO_TRADE))
    assert table.loc[table["gate"] == "Momentum", "status"].iloc[0] == "fail"
    assert table.loc[table["gate"] == "Minimum RR", "status"].iloc[0] == "pass"


def test_build_chart_contains_indicators_and_annotated_levels() -> None:
    df = add_indicators(make_ohlcv(rows=260)).tail(160)
    levels = LevelSet(
        supports=[PriceLevel(price=float(df["close"].iloc[-1]) - 0.003, kind="support", strength=80.0, touches=3, label="support")],
        resistances=[PriceLevel(price=float(df["close"].iloc[-1]) + 0.003, kind="resistance", strength=80.0, touches=2, label="resistance")],
    )
    fig = _build_chart(df, _opportunity(DirectionBias.LONG), levels)
    trace_names = {str(trace.name) for trace in fig.data}

    assert {"Price", "EMA_20", "EMA_50", "EMA_200", "BB Upper", "BB Lower"}.issubset(trace_names)
    assert fig.layout.shapes is not None
    assert len(fig.layout.shapes) >= 5


def test_format_price_handles_optional_values() -> None:
    assert _fmt_price(None) == "n/a"
    assert _fmt_price(1.234567) == "1.23457"


def test_backtest_display_helpers_filter_and_summarize() -> None:
    trades = [_trade(1.2, 80.0, SetupFamily.TREND_CONTINUATION), _trade(-0.8, 55.0, SetupFamily.BREAKOUT_CONFIRMATION)]

    filtered = _filter_backtest_trades(trades, 70.0)
    equity = _backtest_equity_frame(filtered, trades[0].entry_time, 10_000.0, 1.0)
    families = _backtest_family_performance(trades)
    table = _backtest_trade_table(trades)

    assert filtered == [trades[0]]
    assert equity["equity"].iloc[-1] == 10120.0
    assert families.iloc[0]["setup_family"] == "trend_continuation"
    assert {"symbol", "net_r", "final_score", "outcome"}.issubset(table.columns)


def _trade(net_r: float, final_score: float, family: SetupFamily) -> TradeRecord:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return TradeRecord(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=family,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
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
        final_score=final_score,
    )
