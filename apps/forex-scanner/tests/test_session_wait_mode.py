"""Continuous demo-bot session wait tests."""

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
    calculate_session_wait_seconds,
    next_session_windows_for_symbols,
    recommended_next_run_time,
)
from run_demo_bot import add_session_wait_arguments  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_cycle_arguments(parser)
    add_session_wait_arguments(parser)
    return parser


def test_run_demo_bot_accepts_wait_for_session_argument() -> None:
    args = _parser().parse_args(["--wait-for-session"])

    assert args.wait_for_session is True


def test_run_demo_bot_accepts_max_session_wait_seconds_argument() -> None:
    args = _parser().parse_args(["--max-session-wait-seconds", "60"])

    assert args.max_session_wait_seconds == 60


def test_run_demo_bot_accepts_dry_wait_argument() -> None:
    args = _parser().parse_args(["--dry-wait"])

    assert args.dry_wait is True


def test_session_wait_seconds_are_calculated_from_recommended_time() -> None:
    wait_seconds, capped = calculate_session_wait_seconds(
        "2026-05-21T23:00:00+00:00",
        now_utc=datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc),
        max_wait_seconds=86400,
    )

    assert wait_seconds == 3600
    assert capped is False


def test_session_wait_seconds_are_capped() -> None:
    wait_seconds, capped = calculate_session_wait_seconds(
        "2026-05-23T22:00:00+00:00",
        now_utc=datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc),
        max_wait_seconds=60,
    )

    assert wait_seconds == 60
    assert capped is True


def test_next_session_windows_feed_recommended_next_run_time() -> None:
    now = datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc)
    windows = next_session_windows_for_symbols(["EUR/USD", "WTI/OIL", "NAS100"], now_utc=now)

    assert recommended_next_run_time(windows) == "2026-05-22T00:00:00+00:00"
