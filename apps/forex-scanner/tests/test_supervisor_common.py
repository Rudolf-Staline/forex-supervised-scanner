"""Tests for the shared supervisor helpers (ops consolidation, Phase A)."""

from __future__ import annotations

from app.execution.realtime_paper_supervisor import symbols_from_args
from app.execution.supervisor_common import resolve_supervisor_symbols


def test_explicit_symbols_take_precedence_and_are_normalized() -> None:
    assert resolve_supervisor_symbols([" eur/usd ", "gbp/usd", ""], "major_forex", default=["EUR/USD"]) == [
        "EUR/USD",
        "GBP/USD",
    ]


def test_watchlist_used_when_no_explicit_symbols() -> None:
    resolved = resolve_supervisor_symbols(None, "major_forex", default=["EUR/USD"])
    assert resolved  # watchlist is non-empty
    assert resolved != ["EUR/USD"]


def test_default_used_when_nothing_provided() -> None:
    assert resolve_supervisor_symbols(None, None, default=["EUR/USD"]) == ["EUR/USD"]
    assert resolve_supervisor_symbols([], None, default=["USD/CHF"]) == ["USD/CHF"]


def test_symbols_from_args_delegates_with_eurusd_default() -> None:
    # Public L2 helper must keep its exact historical behaviour.
    assert symbols_from_args(None, None) == ["EUR/USD"]
    assert symbols_from_args(["usd/jpy"], "major_forex") == ["USD/JPY"]
    assert symbols_from_args(None, "major_forex") == resolve_supervisor_symbols(None, "major_forex", default=["EUR/USD"])
