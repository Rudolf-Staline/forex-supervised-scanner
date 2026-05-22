"""Decision audit journal tests."""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.core.types import (  # noqa: E402
    ConfidenceBucket,
    DirectionBias,
    MarketRegime,
    Opportunity,
    OpportunityStatus,
    SetupFamily,
    SetupSubtype,
    Timeframe,
    TradingStyle,
)
from app.execution.demo_bot import DemoBotDecision  # noqa: E402
from app.journal.trade_journal import append_trade_journal, decision_to_journal_record, load_trade_journal  # noqa: E402
from journal_summary import export_journal_rows, filter_journal_rows, summarize_journal  # noqa: E402


def test_decision_to_journal_record_contains_required_audit_fields() -> None:
    record = decision_to_journal_record(
        cycle_id="cycle-1",
        opportunity=_opportunity(),
        decision=_decision(),
        order=None,
        timestamp=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
        broker_mode="paper",
        risk_percent=0.25,
    )

    assert record.cycle_id == "cycle-1"
    assert record.asset_class == "forex"
    assert record.logical_symbol == "EUR/USD"
    assert record.mt5_symbol == "EURUSD"
    assert record.session_name == "london_new_york_overlap"
    assert record.decision == "REJECT"
    assert record.created_order is False
    assert "live_trading_disabled=true" in record.safety_status


def test_append_and_load_trade_journal(tmp_path) -> None:
    path = tmp_path / "trade_journal.csv"
    record = decision_to_journal_record(
        cycle_id="cycle-1",
        opportunity=_opportunity(),
        decision=_decision(),
        order=None,
        timestamp=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
    )

    append_trade_journal([record], path)
    rows = load_trade_journal(path)

    assert len(rows) == 1
    assert rows[0]["logical_symbol"] == "EUR/USD"
    assert rows[0]["decision"] == "REJECT"


def test_journal_summary_counts_near_miss_and_rejections(tmp_path) -> None:
    rows = [
        {
            "timestamp": "2026-05-21T14:00:00+00:00",
            "asset_class": "forex",
            "logical_symbol": "EUR/USD",
            "setup": "ema50_pullback",
            "status": "watchlist",
            "score": "72.0",
            "pattern_score": "0.0",
            "session_name": "london",
            "decision": "REJECT",
            "rejection_reasons": "score below demo bot threshold; status watchlist is not executable",
        },
        {
            "timestamp": "2026-05-21T15:00:00+00:00",
            "asset_class": "indices",
            "logical_symbol": "US30",
            "setup": "none",
            "status": "rejected",
            "score": "0.0",
            "pattern_score": "0.0",
            "session_name": "us_open",
            "decision": "REJECT",
            "rejection_reasons": "no_setup_detected",
        },
    ]

    summary = summarize_journal(rows)

    assert summary["total_decisions"] == 2
    assert summary["rejected"] == 2
    assert summary["watchlist"] == 1
    assert summary["near_miss"] == 1
    assert summary["best_symbols"][0][0] == "EUR/USD"


def test_filter_and_export_journal_rows(tmp_path) -> None:
    rows = [
        {"timestamp": "2026-05-21T14:00:00+00:00", "asset_class": "forex", "logical_symbol": "EUR/USD"},
        {"timestamp": "2026-05-21T14:00:00+00:00", "asset_class": "indices", "logical_symbol": "US30"},
    ]
    filtered = filter_journal_rows(rows, asset_class="forex")
    output = tmp_path / "filtered.csv"

    export_journal_rows(filtered, output)

    with output.open(newline="", encoding="utf-8") as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == 1
    assert exported[0]["logical_symbol"] == "EUR/USD"


def _decision() -> DemoBotDecision:
    return DemoBotDecision(
        symbol="EUR/USD",
        status="watchlist",
        setup_subtype="ema50_pullback",
        accepted=False,
        reasons=["score below demo bot threshold"],
        final_score=72.0,
        risk_reward=1.6,
        detected_patterns=["pin_bar"],
        pattern_score=5.0,
    )


def _opportunity() -> Opportunity:
    return Opportunity(
        timestamp=datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.EMA50_PULLBACK,
        regime=MarketRegime.TRENDING_UP,
        direction=DirectionBias.LONG,
        score=72.0,
        confidence=ConfidenceBucket.MEDIUM,
        entry=1.1,
        stop_loss=1.09,
        take_profit=1.12,
        risk_reward=2.0,
        explanation="test",
        timeframe_higher=Timeframe.H1,
        timeframe_entry=Timeframe.M15,
        timeframe_trigger=Timeframe.M5,
        provider="synthetic",
        status=OpportunityStatus.WATCHLIST,
        final_score=72.0,
        tp1=1.11,
        tp2=1.12,
        tp3=1.13,
        spread=0.0001,
        atr=0.001,
        detected_patterns=["pin_bar"],
        pattern_score=5.0,
    )
