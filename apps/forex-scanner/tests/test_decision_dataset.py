"""Tests for the decision-level calibration dataset builder."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.reporting.decision_dataset import (
    DecisionDatasetConfig,
    build_decision_dataset,
    score_band,
    write_decision_dataset,
)


def _initialize_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE scan_results (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                symbol TEXT,
                style TEXT,
                setup_family TEXT,
                regime TEXT,
                direction TEXT,
                score REAL,
                confidence TEXT,
                entry REAL,
                stop_loss REAL,
                take_profit REAL,
                risk_reward REAL,
                setup_subtype TEXT,
                status TEXT,
                provider TEXT,
                technical_score REAL,
                execution_score REAL,
                context_score REAL,
                empirical_score REAL,
                final_score REAL,
                spread REAL,
                atr REAL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE rejected_signals (
                id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE paper_orders (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )


def _scan_payload(
    *,
    timestamp: str,
    symbol: str = "EUR/USD",
    status: str = "approved",
    approved: bool = True,
    family: str = "trend_continuation",
    subtype: str = "ema50_pullback",
    direction: str = "long",
    score: float = 80.0,
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "symbol": symbol,
        "style": "day_trading",
        "setup_family": family,
        "raw_setup_family": None if family == "no_trade" else family,
        "setup_subtype": subtype,
        "regime": "trending",
        "direction": direction,
        "score": score,
        "final_score": score,
        "technical_score": 75.0,
        "execution_score": 70.0,
        "context_score": 65.0,
        "empirical_score": 55.0,
        "confidence": "high",
        "entry": 1.1000 if family != "no_trade" else None,
        "stop_loss": 1.0950 if family != "no_trade" else None,
        "take_profit": 1.1100 if family != "no_trade" else None,
        "tp1": 1.1050 if family != "no_trade" else None,
        "tp2": 1.1080 if family != "no_trade" else None,
        "tp3": 1.1100 if family != "no_trade" else None,
        "risk_reward": 2.0 if family != "no_trade" else None,
        "approved": approved,
        "status": status,
        "provider": "csv",
        "spread": 0.0001,
        "atr": 0.0010,
        "data_quality": {"score": 92.0},
        "score_components": {"trend_clarity": 80.0},
        "detected_patterns": ["inside_bar"],
        "gate_breakdown": {"score": {"passed": approved}},
        "rejection_reason": None if approved else "minimum score gate failed",
        "missing_conditions": [] if approved else ["raise score"],
    }


def _insert_scan(
    database: Path,
    decision_id: str,
    payload: dict[str, object],
    *,
    raw_payload: str | None = None,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO scan_results (
                id, created_at, symbol, style, setup_family, regime, direction,
                score, confidence, entry, stop_loss, take_profit, risk_reward,
                setup_subtype, status, provider, technical_score, execution_score,
                context_score, empirical_score, final_score, spread, atr, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                payload["timestamp"],
                payload["symbol"],
                payload["style"],
                payload["setup_family"],
                payload["regime"],
                payload["direction"],
                payload["score"],
                payload["confidence"],
                payload["entry"],
                payload["stop_loss"],
                payload["take_profit"],
                payload["risk_reward"],
                payload["setup_subtype"],
                payload["status"],
                payload["provider"],
                payload["technical_score"],
                payload["execution_score"],
                payload["context_score"],
                payload["empirical_score"],
                payload["final_score"],
                payload["spread"],
                payload["atr"],
                raw_payload if raw_payload is not None else json.dumps(payload),
            ),
        )


def _insert_order(
    database: Path,
    *,
    order_id: str,
    signal_timestamp: str,
    source_opportunity_id: str | None = None,
    symbol: str = "EUR/USD",
    realized_r: float | None = 1.25,
    status: str = "fully_closed_trade",
) -> None:
    payload = {
        "order_id": order_id,
        "created_at": signal_timestamp,
        "signal_timestamp": signal_timestamp,
        "activated_at": signal_timestamp if status != "missed_trade" else None,
        "closed_at": signal_timestamp if status in {"fully_closed_trade", "closed"} else None,
        "status": status,
        "realized_r": realized_r,
        "realized_pnl": None if realized_r is None else realized_r * 10.0,
        "mae": 0.2,
        "mfe": 1.5,
        "bars_to_activation": 2,
        "time_in_trade_minutes": 45.0,
        "close_reason": "take_profit" if realized_r and realized_r > 0 else "stop_loss",
        "request": {
            "symbol": symbol,
            "style": "day_trading",
            "setup_family": "trend_continuation",
            "setup_subtype": "ema50_pullback",
            "direction": "long",
            "source_opportunity_id": source_opportunity_id,
            "signal_timestamp": signal_timestamp,
        },
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO paper_orders (id, created_at, payload_json) VALUES (?, ?, ?)",
            (order_id, signal_timestamp, json.dumps(payload)),
        )


def test_exact_journal_order_link_attaches_realized_label(tmp_path: Path) -> None:
    database = tmp_path / "scanner.db"
    _initialize_database(database)
    timestamp = "2026-01-05T10:00:00+00:00"
    _insert_scan(database, "decision-1", _scan_payload(timestamp=timestamp))
    _insert_order(
        database,
        order_id="order-1",
        signal_timestamp=timestamp,
        source_opportunity_id="cycle-1",
    )
    journal = tmp_path / "signal_journal.jsonl"
    journal.write_text(
        json.dumps(
            {
                "timestamp_utc": "2026-01-05T10:00:01+00:00",
                "cycle_id": "cycle-1",
                "logical_symbol": "EUR/USD",
                "setup": "ema50_pullback",
                "status": "approved",
                "decision": "accepted",
                "order_ids": ["order-1"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    dataset = build_decision_dataset(database, signal_journal_path=journal)

    assert len(dataset.rows) == 1
    row = dataset.rows[0]
    assert row["cycle_id"] == "cycle-1"
    assert row["order_match_method"] == "signal_journal_order_id"
    assert row["return_label_available"] is True
    assert row["realized_r"] == 1.25
    assert row["target_positive_r"] is True
    assert row["label_source"] == "paper_order"
    assert dataset.summary["return_labeled_rows"] == 1


def test_rejected_signal_is_diagnostic_not_a_loss(tmp_path: Path) -> None:
    database = tmp_path / "scanner.db"
    _initialize_database(database)
    timestamp = "2026-01-05T11:00:00+00:00"
    _insert_scan(
        database,
        "decision-r",
        _scan_payload(timestamp=timestamp, status="watchlist", approved=False, score=62.0),
    )
    rejected = {
        "id": "reject-1",
        "cycle_id": "cycle-r",
        "timestamp": timestamp,
        "symbol": "EUR/USD",
        "setup": "ema50_pullback",
        "status": "watchlist",
        "score": 62.0,
        "risk_reward": 2.0,
        "rejection_reasons": ["demo bot minimum score not met"],
        "provider": "csv",
        "broker": "paper",
        "style": "day_trading",
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO rejected_signals (id, payload_json) VALUES (?, ?)",
            ("reject-1", json.dumps(rejected)),
        )

    dataset = build_decision_dataset(database)
    row = dataset.rows[0]

    assert row["bot_decision"] == "rejected"
    assert row["bot_rejection_reasons"] == ["demo bot minimum score not met"]
    assert row["return_label_available"] is False
    assert row["target_positive_r"] is None
    assert row["realized_r"] is None


def test_ambiguous_temporal_match_is_not_forced(tmp_path: Path) -> None:
    database = tmp_path / "scanner.db"
    _initialize_database(database)
    timestamp = "2026-01-05T12:00:00+00:00"
    _insert_scan(database, "decision-a", _scan_payload(timestamp=timestamp))
    _insert_scan(database, "decision-b", _scan_payload(timestamp=timestamp))
    _insert_order(
        database,
        order_id="order-ambiguous",
        signal_timestamp=timestamp,
        source_opportunity_id=None,
    )

    dataset = build_decision_dataset(database)

    assert dataset.summary["ambiguous_order_matches"] == 1
    assert dataset.summary["return_labeled_rows"] == 0
    assert dataset.ambiguous_matches[0]["candidate_decision_ids"] == [
        "decision-a",
        "decision-b",
    ]
    assert all(row["realized_r"] is None for row in dataset.rows)


def test_malformed_payload_uses_scalar_fallback_and_is_audited(tmp_path: Path) -> None:
    database = tmp_path / "scanner.db"
    _initialize_database(database)
    timestamp = "2026-01-05T13:00:00+00:00"
    payload = _scan_payload(timestamp=timestamp)
    _insert_scan(database, "decision-malformed", payload, raw_payload="{not-json")

    dataset = build_decision_dataset(database)

    assert len(dataset.rows) == 1
    assert dataset.rows[0]["symbol"] == "EUR/USD"
    assert dataset.rows[0]["final_score"] == 80.0
    assert dataset.summary["malformed_source_records"] == 1
    assert dataset.malformed_records[0]["source"] == "scan_results"


def test_writer_separates_complete_and_labeled_training_rows(tmp_path: Path) -> None:
    database = tmp_path / "scanner.db"
    _initialize_database(database)
    first_time = "2026-01-05T14:00:00+00:00"
    second_time = "2026-01-05T15:00:00+00:00"
    _insert_scan(database, "labeled", _scan_payload(timestamp=first_time))
    _insert_scan(
        database,
        "unlabeled",
        _scan_payload(timestamp=second_time, symbol="GBP/USD", score=72.0),
    )
    _insert_order(
        database,
        order_id="order-labeled",
        signal_timestamp=first_time,
        source_opportunity_id="labeled",
    )

    dataset = build_decision_dataset(database)
    outputs = write_decision_dataset(dataset, tmp_path / "reports")

    complete_rows = outputs["dataset"].read_text(encoding="utf-8").splitlines()
    training_rows = outputs["training_dataset"].read_text(encoding="utf-8").splitlines()
    manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))

    assert len(complete_rows) == 2
    assert len(training_rows) == 1
    assert json.loads(training_rows[0])["decision_id"] == "labeled"
    assert manifest["training_guidance"]["random_split_allowed"] is False
    assert outputs["csv"].is_file()
    assert outputs["summary_txt"].read_text(encoding="utf-8").startswith(
        "Decision Calibration Dataset"
    )


def test_no_trade_rows_can_be_excluded(tmp_path: Path) -> None:
    database = tmp_path / "scanner.db"
    _initialize_database(database)
    timestamp = "2026-01-05T16:00:00+00:00"
    payload = _scan_payload(
        timestamp=timestamp,
        family="no_trade",
        subtype="none",
        direction="no_trade",
        status="rejected",
        approved=False,
        score=0.0,
    )
    _insert_scan(database, "no-trade", payload)

    included = build_decision_dataset(database)
    excluded = build_decision_dataset(
        database,
        config=DecisionDatasetConfig(include_no_trade=False),
    )

    assert included.summary["no_trade_rows"] == 1
    assert excluded.rows == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0-50"),
        (49.999, "0-50"),
        (50, "50-60"),
        (60, "60-70"),
        (70, "70-75"),
        (75, "75-80"),
        (80, "80+"),
        (None, "missing"),
    ],
)
def test_score_band_boundaries(value: object, expected: str) -> None:
    assert score_band(value) == expected


def test_invalid_match_window_fails_loudly() -> None:
    with pytest.raises(ValueError, match="match_window_seconds"):
        DecisionDatasetConfig(match_window_seconds=-1)
