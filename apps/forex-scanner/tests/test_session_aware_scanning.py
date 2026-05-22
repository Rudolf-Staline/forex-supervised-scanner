"""Session-aware terminal scan tests."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _demo_bot_cli import (  # noqa: E402
    add_cycle_arguments,
    filter_tradable_session_symbols_if_requested,
    print_next_session_windows,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_cycle_arguments(parser)
    return parser


def test_run_one_cycle_accepts_only_tradable_session_argument() -> None:
    args = _parser().parse_args(["--only-tradable-session"])

    assert args.only_tradable_session is True


def test_run_one_cycle_accepts_show_next_windows_argument() -> None:
    args = _parser().parse_args(["--show-next-windows"])

    assert args.show_next_windows is True


def test_run_demo_bot_accepts_only_tradable_session_argument() -> None:
    args = _parser().parse_args(["--only-tradable-session"])

    assert args.only_tradable_session is True


def test_run_demo_bot_accepts_show_next_windows_argument() -> None:
    args = _parser().parse_args(["--show-next-windows"])

    assert args.show_next_windows is True


def test_off_hours_symbols_are_skipped_before_scan(capsys) -> None:
    symbols = ["EUR/USD", "WTI/OIL", "NAS100"]
    weekend = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)

    filtered = filter_tradable_session_symbols_if_requested(symbols, True, now_utc=weekend)

    assert filtered == []
    output = capsys.readouterr().out
    assert "skipped_off_hours symbol=EUR/USD" in output
    assert "skipped_off_hours symbol=WTI/OIL" in output
    assert "skipped_off_hours symbol=NAS100" in output


def test_no_orders_possible_when_all_symbols_are_off_hours(capsys) -> None:
    weekend = datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc)

    filtered = filter_tradable_session_symbols_if_requested(["EUR/USD"], True, now_utc=weekend)

    assert filtered == []
    output = capsys.readouterr().out
    assert "no_tradable_symbols_now=true" in output
    assert "recommended_next_run_time=" in output


def test_show_next_windows_prints_all_asset_classes(capsys) -> None:
    moment = datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc)

    print_next_session_windows(["EUR/USD", "WTI/OIL", "NAS100"], now_utc=moment)

    output = capsys.readouterr().out
    assert "current_utc_time=2026-05-21T22:00:00+00:00" in output
    assert "next_forex_window=" in output
    assert "next_commodities_window=" in output
    assert "next_indices_window=" in output
    assert "recommended_next_run_time=" in output
