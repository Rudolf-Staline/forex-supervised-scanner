"""SQLite migration and analytics persistence tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.core.types import (
    ConfidenceBucket,
    DirectionBias,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    ScanReport,
    SessionName,
    SetupFamily,
    SetupSubtype,
    Timeframe,
    TradeOutcomeLabel,
    TradingStyle,
)
from app.execution.models import PaperBlockRecord, OrderRequest, TradeEventType
from app.execution.paper import PaperExecutor
from app.paper.journal import journal_entries_from_orders
from app.storage.database import Database


def _opportunity() -> Opportunity:
    return Opportunity(
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        regime=MarketRegime.TRENDING_UP,
        direction=DirectionBias.LONG,
        score=74.0,
        confidence=ConfidenceBucket.HIGH,
        entry=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        risk_reward=2.0,
        explanation="test",
        timeframe_higher=Timeframe.H1,
        timeframe_entry=Timeframe.M15,
        timeframe_trigger=Timeframe.M5,
        score_components={"trend_clarity": 80.0},
        provider="synthetic",
        approved=True,
        status=OpportunityStatus.APPROVED,
        raw_setup_family=SetupFamily.TREND_CONTINUATION,
        technical_score=78.0,
        execution_score=70.0,
        context_score=72.0,
        empirical_score=55.0,
        final_score=74.0,
        activation_quality=84.0,
        invalidation_quality=76.0,
        spread=0.0001,
        atr=0.0012,
        key_level_distances={"setup_level_atr": 0.3},
        session=SessionName.LONDON,
        outcome=TradeOutcomeLabel.WIN_CLEAN,
        tp1_hit=True,
        tp2_hit=True,
        tp3_hit=False,
        mae=0.2,
        mfe=2.1,
        bars_to_activation=0,
        bars_to_tp1=3,
    )


def test_database_migrates_existing_scan_schema(tmp_path) -> None:
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE scan_results (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                style TEXT NOT NULL,
                setup_family TEXT NOT NULL,
                regime TEXT NOT NULL,
                direction TEXT NOT NULL,
                score REAL NOT NULL,
                confidence TEXT NOT NULL,
                entry REAL,
                stop_loss REAL,
                take_profit REAL,
                risk_reward REAL,
                explanation TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )

    Database(path)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(scan_results)").fetchall()}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    assert "setup_subtype" in columns
    assert "context_score" in columns
    assert "bars_to_tp3" in columns
    assert "trade_events" in tables
    assert "trading_journal" in tables
    assert "broker_orders" in tables
    assert "reconciliation_anomalies" in tables


def test_save_scan_report_persists_new_analytics_fields(tmp_path) -> None:
    database = Database(tmp_path / "scan.sqlite")
    report = ScanReport(timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc), style=TradingStyle.DAY_TRADING, opportunities=[_opportunity()])
    database.save_scan_report(report)

    with sqlite3.connect(database.path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM scan_results").fetchone()

    assert row["setup_subtype"] == "shallow_ema20_pullback"
    assert row["session"] == "london"
    assert row["technical_score"] == 78.0
    assert row["context_score"] == 72.0
    assert json.loads(row["key_level_distances_json"]) == {"setup_level_atr": 0.3}
    assert row["outcome"] == "win_clean"
    assert row["tp1_hit"] == 1


def test_save_and_load_paper_orders(tmp_path, settings) -> None:
    database = Database(tmp_path / "paper.sqlite")
    executor = PaperExecutor(settings)
    order = executor.place_order(
        OrderRequest(
            symbol="EUR/USD",
            style=TradingStyle.DAY_TRADING,
            setup_family=SetupFamily.TREND_CONTINUATION,
            setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
            direction=DirectionBias.LONG,
            quantity_units=1.0,
            entry_price=1.1,
            stop_loss=1.095,
            take_profit=1.11,
        )
    )

    database.save_paper_orders([order])
    loaded = database.load_paper_orders()
    events = database.load_trade_events(order.order_id)

    assert len(loaded) == 1
    assert loaded[0].order_id == order.order_id
    assert loaded[0].request.symbol == "EUR/USD"
    assert events[0].event_type == TradeEventType.SIGNAL_APPROVED


def test_save_and_load_paper_blocks(tmp_path) -> None:
    database = Database(tmp_path / "paper_blocks.sqlite")
    block = PaperBlockRecord(
        block_id="block-1",
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        symbol="EUR/USD",
        status="approved",
        setup_family="trend_continuation",
        setup_subtype="shallow_ema20_pullback",
        direction="long",
        final_score=72.0,
        reasons=["max exposure for EUR would be exceeded"],
        portfolio_snapshot={"open_orders": 2},
    )

    database.save_paper_blocks([block])
    loaded = database.load_paper_blocks()

    assert len(loaded) == 1
    assert loaded[0].block_id == "block-1"
    assert loaded[0].reasons == ["max exposure for EUR would be exceeded"]


def test_save_and_load_trading_journal_entries(tmp_path, settings) -> None:
    database = Database(tmp_path / "journal.sqlite")
    executor = PaperExecutor(settings)
    order = executor.place_order(
        OrderRequest(
            symbol="EUR/USD",
            style=TradingStyle.DAY_TRADING,
            setup_family=SetupFamily.TREND_CONTINUATION,
            setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
            direction=DirectionBias.LONG,
            quantity_units=1.0,
            entry_price=1.1,
            stop_loss=1.095,
            take_profit=1.11,
            source_status="approved",
            entry_rationale="storage journal test",
        )
    )
    entries = journal_entries_from_orders([order])

    database.save_journal_entries(entries)
    loaded = database.load_journal_entries()

    assert len(loaded) == 1
    assert loaded[0].trade_id == order.order_id
    assert loaded[0].entry_rationale_summary == "storage journal test"


def test_empirical_score_uses_persisted_backtest_trades(tmp_path) -> None:
    database = Database(tmp_path / "empirical.sqlite")
    trades = [
        {
            "symbol": "EUR/USD",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": 1.2,
        },
        {
            "symbol": "EUR/USD",
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "shallow_ema20_pullback",
            "session": "london",
            "regime": "trending up",
            "net_r": 0.8,
        },
    ]
    with database._connect() as connection:
        connection.execute(
            """
            INSERT INTO backtest_runs (
                id, created_at, style, symbols_json, setup_filter, start_at, end_at,
                metrics_json, trades_json, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run", "2025-01-01T00:00:00+00:00", "day_trading", "[]", "all", "2025", "2025", "{}", json.dumps(trades), "{}"),
        )

    score = database.lookup_empirical_score(
        symbol="EUR/USD",
        style="day_trading",
        family=SetupFamily.TREND_CONTINUATION,
        subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        session=SessionName.LONDON,
        regime=MarketRegime.TRENDING_UP,
        minimum_samples=2,
        neutral_score=55.0,
        min_condition_samples=1,
    )

    assert score > 55.0
