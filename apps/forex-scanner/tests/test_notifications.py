"""Read-only notification tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.execution.demo_bot import DemoBotDecision
from app.market.sessions import MarketSessionInfo
from app.notifications.notifier import (
    AlertPayload,
    FileNotificationChannel,
    NotificationSettings,
    build_signal_alert,
    notify_session_transition,
    send_alerts,
    should_alert_decision,
)


def test_notifications_disabled_sends_nothing(tmp_path) -> None:
    settings = NotificationSettings(enabled=False, channel="file", alerts_log_path=tmp_path / "alerts.log")
    alert = _alert()

    sent = send_alerts([alert], settings=settings)

    assert sent == []
    assert not settings.alerts_log_path.exists()


def test_file_notification_channel_writes_json_line(tmp_path) -> None:
    path = tmp_path / "alerts.log"
    channel = FileNotificationChannel(path)

    channel.send(_alert())

    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["symbol"] == "EUR/USD"
    assert payload["action"] == "read_only_no_trade_execution"
    assert "execute" not in payload


def test_should_alert_decision_for_score_pattern_and_watchlist_setup() -> None:
    settings = NotificationSettings(enabled=True, alert_min_score=70.0)

    assert should_alert_decision(_decision(score=70.0), settings)
    assert should_alert_decision(_decision(score=10.0, pattern_score=5.0), settings)
    assert should_alert_decision(_decision(score=10.0, status="watchlist", setup="ema50_pullback"), settings)
    assert not should_alert_decision(_decision(score=10.0, status="rejected", setup="none"), settings)


def test_build_signal_alert_contains_safety_status_and_no_trade_action() -> None:
    alert = build_signal_alert(_decision(score=72.0), broker_mode="paper")

    assert alert.asset_class == "forex"
    assert alert.mt5_symbol == "EURUSD"
    assert "live_trading_disabled=true" in alert.safety_status
    assert alert.action == "read_only_no_trade_execution"


def test_session_transition_alerts_only_when_symbol_becomes_tradable(tmp_path) -> None:
    settings = NotificationSettings(
        enabled=True,
        channel="file",
        alerts_log_path=tmp_path / "alerts.log",
        session_state_path=tmp_path / "session_state.json",
    )
    off_hours = MarketSessionInfo(
        symbol="EUR/USD",
        asset_class="forex",
        session_name="off_hours",
        is_tradable_session=False,
        reason="outside session",
        next_tradable_window="asian 2026-05-25T00:00:00+00:00 to 2026-05-25T07:00:00+00:00",
    )
    tradable = MarketSessionInfo(
        symbol="EUR/USD",
        asset_class="forex",
        session_name="london",
        is_tradable_session=True,
        reason="inside session",
        next_tradable_window="london now until 2026-05-25T13:00:00+00:00",
    )

    assert notify_session_transition(off_hours, broker_mode="paper", settings=settings) is None
    alert = notify_session_transition(tradable, broker_mode="paper", settings=settings)

    assert alert is not None
    assert alert.alert_type == "session_transition"
    payload = json.loads(settings.alerts_log_path.read_text(encoding="utf-8").strip())
    assert payload["setup"] == "session_transition"


def _decision(
    *,
    score: float,
    pattern_score: float = 0.0,
    status: str = "rejected",
    setup: str = "ema50_pullback",
) -> DemoBotDecision:
    return DemoBotDecision(
        symbol="EUR/USD",
        status=status,
        setup_subtype=setup,
        accepted=False,
        reasons=["score below demo bot threshold"],
        final_score=score,
        risk_reward=1.6,
        detected_patterns=["pin_bar"] if pattern_score else [],
        pattern_score=pattern_score,
    )


def _alert() -> AlertPayload:
    return AlertPayload(
        timestamp="2026-05-22T00:00:00+00:00",
        asset_class="forex",
        symbol="EUR/USD",
        mt5_symbol="EURUSD",
        setup="ema50_pullback",
        score=72.0,
        risk_reward=1.6,
        pattern_score=0.0,
        detected_patterns=[],
        session_name="london",
        reasons=["near miss"],
        broker_mode="paper",
        safety_status="demo_only=true live_trading_disabled=true broker=paper",
    )
