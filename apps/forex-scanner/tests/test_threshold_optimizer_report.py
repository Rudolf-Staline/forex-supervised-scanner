"""Threshold optimizer report tests."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.execution.rejected_signals import RejectedSignalRecord
from threshold_optimizer_report import (  # noqa: E402
    ALERT,
    MAX_SPREAD_ATR_GRID,
    MIN_RISK_REWARD_GRID,
    MIN_SCORE_GRID,
    build_candidate_thresholds,
    build_threshold_scenarios,
    export_scenarios_csv,
    export_summary_json,
    filter_backtest_rows,
)


def test_threshold_grid_generates_expected_number_of_scenarios() -> None:
    scenarios = build_threshold_scenarios(_rows(), _rejected(), min_sample_size=30)

    assert len(scenarios) == len(_rows()) * len(MIN_SCORE_GRID) * len(MIN_RISK_REWARD_GRID) * len(MAX_SPREAD_ATR_GRID)


def test_threshold_scenarios_mark_small_samples() -> None:
    scenarios = build_threshold_scenarios(_rows(), _rejected(), min_sample_size=30)

    assert any(scenario.sample_size_warning for scenario in scenarios)


def test_candidate_thresholds_are_informational_only() -> None:
    scenarios = build_threshold_scenarios(_rows(), _rejected(), min_sample_size=1)

    candidates = build_candidate_thresholds(scenarios)

    assert candidates["forex"]["status"] == "informational_only"
    assert "min_score" in candidates["forex"]
    assert candidates["commodities"]["status"] in {"informational_only", "insufficient_data"}


def test_filter_backtest_rows_respects_asset_class_and_watchlist() -> None:
    filtered = filter_backtest_rows(_rows(), asset_class="forex", watchlist="multi_asset_demo")

    assert [row["symbol"] for row in filtered] == ["EUR/USD"]


def test_threshold_optimizer_exports_csv_and_json(tmp_path) -> None:
    scenarios = build_threshold_scenarios(_rows(), _rejected(), min_sample_size=1)
    candidates = build_candidate_thresholds(scenarios)
    csv_path = tmp_path / "threshold_optimizer_report.csv"
    json_path = tmp_path / "threshold_optimizer_summary.json"

    export_scenarios_csv(scenarios, csv_path)
    export_summary_json(candidates, json_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"asset_class", "symbol", "min_score", "expectancy_R", "sample_size_warning"}.issubset(rows[0])
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["alert"] == ALERT
    assert payload["config_modified"] is False
    assert payload["orders_sent"] is False


def _rows() -> list[dict]:
    return [
        {
            "asset_class": "forex",
            "symbol": "EUR/USD",
            "setup": "ema50_pullback",
            "session": "london",
            "total_signals": "40",
            "total_trades_simulated": "20",
            "win_rate": "55.0",
            "expectancy_R": "0.25",
            "profit_factor": "1.6",
            "max_drawdown_R": "2.0",
            "average_R": "0.25",
            "rejected_count": "20",
        },
        {
            "asset_class": "commodities",
            "symbol": "XAU/USD",
            "setup": "ema50_pullback",
            "session": "new_york",
            "total_signals": "6",
            "total_trades_simulated": "3",
            "win_rate": "33.3",
            "expectancy_R": "-0.1",
            "profit_factor": "0.8",
            "max_drawdown_R": "1.0",
            "average_R": "-0.1",
            "rejected_count": "3",
        },
    ]


def _rejected() -> list[RejectedSignalRecord]:
    return [
        RejectedSignalRecord(
            id="r1",
            cycle_id="c1",
            timestamp=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
            symbol="EUR/USD",
            setup="ema50_pullback",
            status="watchlist",
            score=72.0,
            risk_reward=1.8,
            spread_atr=0.21,
            rejection_reasons=["score below demo bot threshold"],
        ),
        RejectedSignalRecord(
            id="r2",
            cycle_id="c1",
            timestamp=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
            symbol="XAU/USD",
            setup="ema50_pullback",
            status="watchlist",
            score=70.0,
            risk_reward=1.8,
            spread_atr=0.45,
            rejection_reasons=["scan_only reason=ALLOW_MULTI_ASSET_DEMO_TRADING is false"],
        ),
    ]
