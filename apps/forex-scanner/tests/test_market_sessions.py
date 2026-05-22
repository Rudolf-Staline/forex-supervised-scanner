"""Market session diagnostics tests."""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config.instruments import AssetClass
from app.market.sessions import best_session_for_asset_class, explain_off_hours, get_market_session

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from market_sessions_report import build_market_sessions_report, export_market_sessions_csv  # noqa: E402


def test_forex_london_new_york_overlap_is_tradable() -> None:
    info = get_market_session(datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc), AssetClass.FOREX, "EUR/USD")

    assert info.session_name == "london_new_york_overlap"
    assert info.is_tradable_session is True
    assert "next_tradable_window" not in info.reason


def test_commodities_off_hours_explains_next_window() -> None:
    moment = datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc)
    info = get_market_session(moment, AssetClass.COMMODITIES, "WTI/OIL")

    assert info.session_name == "off_hours"
    assert info.is_tradable_session is False
    assert "london" in info.next_tradable_window
    assert "asset_class=commodities" in explain_off_hours("WTI/OIL", AssetClass.COMMODITIES, moment)


def test_indices_sessions_have_named_cash_windows() -> None:
    europe = get_market_session(datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc), AssetClass.INDICES, "GER40")
    us_open = get_market_session(datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc), AssetClass.INDICES, "NAS100")

    assert europe.session_name == "europe_cash"
    assert us_open.session_name == "us_open"
    assert europe.is_tradable_session is True
    assert us_open.is_tradable_session is True


def test_weekend_is_blocked_for_all_asset_classes() -> None:
    info = get_market_session(datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc), "forex", "EUR/USD")

    assert info.session_name == "weekend_closed"
    assert info.is_tradable_session is False
    assert "weekend" in info.reason


def test_best_sessions_are_defined_per_asset_class() -> None:
    assert best_session_for_asset_class("forex") == "london_new_york_overlap"
    assert best_session_for_asset_class("commodities") == "high_liquidity_overlap"
    assert best_session_for_asset_class("indices") == "us_open"


def test_market_sessions_report_contains_requested_sections() -> None:
    report = build_market_sessions_report(
        "multi_asset_demo",
        "all",
        datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc),
    )

    assert report["current_utc_time"] == "2026-05-21T22:00:00+00:00"
    assert "tradable_now" in report
    assert "off_hours_now" in report
    assert "next_windows_by_asset_class" in report
    assert "recommended_next_run_time" in report
    assert "recommended_watchlist_now" in report
    assert set(report["next_windows_by_asset_class"]) == {"forex", "commodities", "indices"}


def test_market_sessions_report_exports_csv(tmp_path) -> None:
    report = build_market_sessions_report(
        "multi_asset_demo",
        "all",
        datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc),
    )
    csv_path = tmp_path / "market_sessions_report.csv"

    export_market_sessions_csv(report["rows"], csv_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"current_utc_time", "symbol", "asset_class", "session_name", "is_tradable_session"}.issubset(rows[0])
