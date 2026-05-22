"""Multi-asset backtest report tests."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.core.types import DirectionBias, SetupFamily, SetupSubtype, TradeRecord, TradingStyle
from app.execution.rejected_signals import RejectedSignalRecord
from backtest_multi_asset import (  # noqa: E402
    WARNING,
    build_backtest_rows,
    build_backtest_summary,
    export_backtest_csv,
    export_summary_json,
    filter_backtest_trades,
)


def test_filter_backtest_trades_respects_min_score_and_sessions() -> None:
    trades = [
        _trade("EUR/USD", 1.2, score=60.0, entry_time=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc)),
        _trade("GBP/USD", 1.0, score=40.0, entry_time=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc)),
        _trade("US30", 1.0, score=65.0, entry_time=datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc)),
    ]

    filtered = filter_backtest_trades(trades, min_score=55.0, only_tradable_session=True)

    assert [trade.symbol for trade in filtered] == ["EUR/USD"]


def test_build_backtest_rows_groups_trades_and_rejections() -> None:
    trades = [
        _trade("EUR/USD", 1.2, setup=SetupSubtype.EMA50_PULLBACK),
        _trade("EUR/USD", -1.0, setup=SetupSubtype.EMA50_PULLBACK),
    ]
    rejected = [_rejected("EUR/USD", setup="ema50_pullback")]

    rows = build_backtest_rows(trades, rejected)

    row = next(item for item in rows if item.symbol == "EUR/USD" and item.setup == "ema50_pullback")
    assert row.asset_class == "forex"
    assert row.total_signals == 3
    assert row.total_trades_simulated == 2
    assert row.rejected_count == 1
    assert row.best_trade_R == 1.2
    assert row.worst_trade_R == -1.0
    assert row.average_spread_atr == 0.2


def test_build_backtest_summary_contains_required_sections() -> None:
    trades = [
        _trade("EUR/USD", 1.2, setup=SetupSubtype.EMA50_PULLBACK),
        _trade("XAU/USD", 2.0, setup=SetupSubtype.MOMENTUM_BREAKOUT),
        _trade("US30", -1.0, setup=SetupSubtype.RANGE_EDGE_REVERSAL),
    ]
    rows = build_backtest_rows(trades, [])

    summary = build_backtest_summary(trades, rows)

    assert summary["warning"] == WARNING
    assert "best_markets_by_expectancy" in summary
    assert "best_sessions" in summary
    assert "setup_quality" in summary
    assert summary["setup_quality"]["ema50_pullback"]["occurrences"] == 1


def test_backtest_multi_asset_exports_csv_and_json(tmp_path) -> None:
    trades = [_trade("EUR/USD", 1.2)]
    rows = build_backtest_rows(trades, [])
    summary = build_backtest_summary(trades, rows)
    csv_path = tmp_path / "backtest_multi_asset.csv"
    json_path = tmp_path / "backtest_multi_asset_summary.json"

    export_backtest_csv(rows, csv_path)
    export_summary_json(summary, json_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert csv_rows
    assert {"asset_class", "symbol", "setup", "session", "expectancy_R"}.issubset(csv_rows[0])
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["warning"] == WARNING


def _trade(
    symbol: str,
    net_r: float,
    *,
    score: float = 60.0,
    setup: SetupSubtype = SetupSubtype.EMA50_PULLBACK,
    entry_time: datetime | None = None,
) -> TradeRecord:
    entry_time = entry_time or datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc)
    return TradeRecord(
        symbol=symbol,
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=setup,
        direction=DirectionBias.LONG,
        entry_time=entry_time,
        exit_time=entry_time,
        entry=1.1,
        stop_loss=1.0,
        take_profit=1.2,
        exit_price=1.2 if net_r > 0 else 1.0,
        gross_r=net_r,
        net_r=net_r,
        exit_reason="take_profit" if net_r > 0 else "stop_loss",
        cost_pips=1.0,
        final_score=score,
        detected_patterns=["pin_bar"] if net_r > 0 else [],
        pattern_score=5.0 if net_r > 0 else 0.0,
    )


def _rejected(symbol: str, *, setup: str) -> RejectedSignalRecord:
    return RejectedSignalRecord(
        id=f"rejected-{symbol}",
        cycle_id="cycle-1",
        timestamp=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
        symbol=symbol,
        setup=setup,
        status="watchlist",
        score=58.0,
        risk_reward=1.2,
        pattern_score=0.0,
        detected_patterns=[],
        market_regime="trending",
        spread_atr=0.2,
        rejection_reasons=["score below demo bot threshold"],
        provider="mt5",
        broker="paper",
        style="day_trading",
        watchlist="multi_asset_demo",
    )
