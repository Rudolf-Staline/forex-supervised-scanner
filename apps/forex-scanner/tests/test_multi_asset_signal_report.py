"""Multi-asset signal report tests."""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.execution.rejected_signals import RejectedSignalRecord
from multi_asset_signal_report import (  # noqa: E402
    build_multi_asset_signal_report,
    export_near_miss_csv,
    filter_report_records,
    is_near_miss,
)


def test_multi_asset_report_groups_signals_by_asset_class() -> None:
    report = build_multi_asset_signal_report(_records(), min_score=55.0)

    assert report["total_signals"] == 4
    assert report["signals_by_asset_class"]["forex"] == 2
    assert report["signals_by_asset_class"]["commodities"] == 1
    assert report["signals_by_asset_class"]["indices"] == 1
    assert report["best_score_by_asset_class"]["forex"] == "62.00"
    assert report["best_score_by_asset_class"]["commodities"] == "51.00"
    assert report["best_score_by_asset_class"]["indices"] == "49.00"


def test_near_miss_definition_includes_patterns_watchlist_and_scan_only() -> None:
    score_near_miss = _record("EUR/USD", score=56.0, status="rejected")
    pattern_near_miss = _record("XAU/USD", score=30.0, pattern_score=8.0, detected_patterns=["pin_bar"])
    status_near_miss = _record("US30", score=20.0, status="watchlist")
    scan_only_near_miss = _record("WTI/OIL", score=20.0, setup="momentum_breakout", reasons=["scan_only asset class"])
    weak_reject = _record("GBP/USD", score=20.0, status="rejected", setup="none")

    assert is_near_miss(score_near_miss, min_score=55.0)
    assert is_near_miss(pattern_near_miss, min_score=55.0)
    assert is_near_miss(status_near_miss, min_score=55.0)
    assert is_near_miss(scan_only_near_miss, min_score=55.0)
    assert not is_near_miss(weak_reject, min_score=55.0)


def test_recommended_focus_returns_three_buckets() -> None:
    report = build_multi_asset_signal_report(_records(), min_score=55.0)

    assert report["recommended_focus"]["forex"][0] == "EUR/USD"
    assert report["recommended_focus"]["commodities"] == ["XAU/USD"]
    assert report["recommended_focus"]["indices"] == ["US30"]


def test_filter_report_records_uses_watchlist_and_asset_class() -> None:
    filtered = filter_report_records(_records(), asset_class="commodities", watchlist="multi_asset_demo")

    assert [record.symbol for record in filtered] == ["XAU/USD"]


def test_multi_asset_report_exports_near_miss_csv(tmp_path) -> None:
    report = build_multi_asset_signal_report(_records(), min_score=55.0)
    output = tmp_path / "multi_asset_signal_report.csv"

    export_near_miss_csv(report["near_miss_records"], output)

    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"asset_class", "symbol", "score", "rejection_reasons"}.issubset(rows[0])


def _records() -> list[RejectedSignalRecord]:
    return [
        _record("EUR/USD", score=62.0, status="watchlist", setup="ema50_pullback", spread_atr=0.2),
        _record("GBP/USD", score=12.0, status="rejected", setup="none", spread_atr=0.4),
        _record("XAU/USD", score=51.0, pattern_score=10.0, detected_patterns=["pin_bar"], spread_atr=0.12),
        _record("US30", score=49.0, setup="momentum_breakout", reasons=["scan_only asset class"], spread_atr=0.05),
    ]


def _record(
    symbol: str,
    *,
    score: float,
    status: str = "rejected",
    setup: str = "ema50_pullback",
    pattern_score: float = 0.0,
    detected_patterns: list[str] | None = None,
    reasons: list[str] | None = None,
    spread_atr: float | None = None,
) -> RejectedSignalRecord:
    return RejectedSignalRecord(
        id=f"id-{symbol}-{score}",
        cycle_id="cycle-1",
        timestamp=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        symbol=symbol,
        setup=setup,
        status=status,
        score=score,
        risk_reward=1.4,
        pattern_score=pattern_score,
        detected_patterns=detected_patterns or [],
        market_regime="trending",
        spread_atr=spread_atr,
        rejection_reasons=reasons or ["score below demo bot threshold"],
        provider="mt5",
        broker="paper",
        style="day_trading",
        watchlist="multi_asset_demo",
    )
