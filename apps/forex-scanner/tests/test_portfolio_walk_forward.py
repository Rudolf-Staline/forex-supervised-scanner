"""Integration tests for portfolio-constrained walk-forward aggregation."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone

from app.backtest.metrics import calculate_metrics
from app.backtest.portfolio import PortfolioConstraints
from app.backtest.portfolio_walk_forward import (
    apply_portfolio_constraints,
    portfolio_report_to_dict,
    portfolio_report_to_text,
    write_portfolio_walk_forward_reports,
)
from app.backtest.walk_forward import FoldResult, WalkForwardConfig, WalkForwardReport, WalkForwardWindow
from app.core.types import (
    DirectionBias,
    SetupFamily,
    SetupSubtype,
    TradeRecord,
    TradingStyle,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _trade(
    symbol: str,
    *,
    entry_hour: int,
    exit_hour: int,
    score: float,
    net_r: float,
) -> TradeRecord:
    entry = BASE + timedelta(hours=entry_hour)
    exit_ = BASE + timedelta(hours=exit_hour)
    return TradeRecord(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        direction=DirectionBias.LONG,
        entry_time=entry,
        exit_time=exit_,
        entry=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        exit_price=1.11 if net_r > 0 else 1.095,
        gross_r=net_r,
        net_r=net_r,
        exit_reason="take_profit" if net_r > 0 else "stop_loss",
        cost_pips=1.0,
        final_score=score,
    )


def _fold(index: int, trades: list[TradeRecord]) -> FoldResult:
    start = BASE + timedelta(days=index)
    window = WalkForwardWindow(
        fold_index=index,
        in_sample_start=start,
        in_sample_end=start + timedelta(days=10),
        out_of_sample_start=start + timedelta(days=10),
        out_of_sample_end=start + timedelta(days=20),
    )
    return FoldResult(
        window=window,
        selected_min_score=60.0,
        in_sample_trades=20,
        in_sample_expectancy=0.1,
        out_of_sample_trades=len(trades),
        out_of_sample_metrics=calculate_metrics(trades),
        oos_trade_records=trades,
    )


def _report(folds: list[FoldResult]) -> WalkForwardReport:
    candidates = []
    seen = set()
    for fold in folds:
        for trade in fold.oos_trade_records:
            key = (trade.symbol, trade.entry_time)
            if key not in seen:
                seen.add(key)
                candidates.append(trade)
    candidates.sort(key=lambda trade: (trade.exit_time, trade.symbol, trade.entry_time))
    equity = [(BASE, 0.0)]
    cumulative = 0.0
    for trade in candidates:
        cumulative += trade.net_r
        equity.append((trade.exit_time, round(cumulative, 4)))
    return WalkForwardReport(
        config=WalkForwardConfig(
            in_sample_days=10,
            out_of_sample_days=10,
            step_days=5,
            score_grid=(0.0, 60.0),
            min_in_sample_trades=5,
        ),
        symbols=["EUR/USD", "GBP/USD", "USD/JPY"],
        style=TradingStyle.DAY_TRADING,
        setup_filter="all",
        start=BASE,
        end=BASE + timedelta(days=30),
        folds=folds,
        aggregate_metrics=calculate_metrics(candidates),
        oos_equity_curve=equity,
    )


def test_canonical_candidates_are_deduplicated_before_portfolio_allocation() -> None:
    winner = _trade("EUR/USD", entry_hour=1, exit_hour=4, score=90, net_r=1.0)
    rejected = _trade("GBP/USD", entry_hour=1, exit_hour=3, score=80, net_r=-1.0)
    duplicate = winner.model_copy(deep=True)
    source = _report([_fold(0, [winner, rejected]), _fold(1, [duplicate])])

    result = apply_portfolio_constraints(
        source,
        PortfolioConstraints(
            max_concurrent_positions=1,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    assert len(result.canonical_candidates) == 2
    assert [trade.symbol for trade in result.portfolio.accepted_trades] == ["EUR/USD"]
    assert result.aggregate_metrics.number_of_trades == 1
    assert result.aggregate_metrics.expectancy == 1.0
    assert result.portfolio.rejected_trades[0].trade.symbol == "GBP/USD"
    assert result.portfolio.rejected_trades[0].reason == "max_concurrent_positions"


def test_portfolio_report_exposes_candidate_and_attainable_metrics_separately() -> None:
    accepted = _trade("EUR/USD", entry_hour=1, exit_hour=4, score=90, net_r=1.0)
    rejected = _trade("GBP/USD", entry_hour=1, exit_hour=3, score=80, net_r=-1.0)
    source = _report([_fold(0, [accepted, rejected])])
    result = apply_portfolio_constraints(
        source,
        PortfolioConstraints(
            max_concurrent_positions=1,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    payload = portfolio_report_to_dict(result)
    oos = payload["out_of_sample"]
    assert oos["canonical_candidate_trades"] == 2
    assert oos["accepted_trades"] == 1
    assert oos["rejected_trades"] == 1
    assert oos["candidate_metrics"]["expectancy"] == 0.0
    assert oos["metrics"]["expectancy"] == 1.0
    assert oos["aggregation"] == "deduplicated_then_portfolio_constrained"
    assert payload["candidate_out_of_sample"]["total_trades"] == 2
    assert payload["portfolio"]["rejections"][0]["reason"] == "max_concurrent_positions"

    text = portfolio_report_to_text(result)
    assert "Attainable portfolio OUT-OF-SAMPLE performance" in text
    assert "canonical OOS candidates   : 2" in text
    assert "accepted trades          : 1" in text


def test_report_writers_keep_headline_registry_aligned_with_portfolio_metrics(tmp_path) -> None:
    accepted = _trade("EUR/USD", entry_hour=1, exit_hour=4, score=90, net_r=1.0)
    rejected = _trade("GBP/USD", entry_hour=1, exit_hour=3, score=80, net_r=-1.0)
    source = _report([_fold(0, [accepted, rejected])])
    result = apply_portfolio_constraints(
        source,
        PortfolioConstraints(
            max_concurrent_positions=1,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    outputs = write_portfolio_walk_forward_reports(result, tmp_path)

    with outputs["registry"].open() as handle:
        accepted_rows = list(csv.DictReader(handle))
    with outputs["candidate_registry"].open() as handle:
        candidate_rows = list(csv.DictReader(handle))
    with outputs["rejections"].open() as handle:
        rejection_rows = list(csv.DictReader(handle))

    assert len(accepted_rows) == result.aggregate_metrics.number_of_trades == 1
    assert accepted_rows[0]["pair"] == "EUR/USD"
    assert {row["pair"] for row in candidate_rows} == {"EUR/USD", "GBP/USD"}
    assert rejection_rows[0]["pair"] == "GBP/USD"
    assert rejection_rows[0]["reason"] == "max_concurrent_positions"
    assert json.loads(outputs["json"].read_text())["out_of_sample"]["accepted_trades"] == 1
    assert outputs["txt"].read_text().startswith("Portfolio-Constrained")


def test_no_rejections_still_writes_a_header_only_rejection_registry(tmp_path) -> None:
    first = _trade("EUR/USD", entry_hour=1, exit_hour=2, score=90, net_r=0.5)
    second = _trade("USD/JPY", entry_hour=3, exit_hour=4, score=80, net_r=0.5)
    source = _report([_fold(0, [first, second])])
    result = apply_portfolio_constraints(
        source,
        PortfolioConstraints(
            max_concurrent_positions=1,
            max_abs_currency_exposure=None,
            daily_loss_limit_r=None,
        ),
    )

    outputs = write_portfolio_walk_forward_reports(result, tmp_path)
    with outputs["rejections"].open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows == []
    assert result.aggregate_metrics.number_of_trades == 2
    assert result.oos_equity_curve[0] == (BASE, 0.0)
