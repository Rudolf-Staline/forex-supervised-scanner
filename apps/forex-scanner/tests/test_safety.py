"""Central demo/paper safety lock tests."""

from __future__ import annotations

import pytest

from app.config.safety import DemoSafetyError, demo_safety_status, ensure_demo_safe_mode


def test_demo_safe_mode_accepts_explicit_paper_environment(settings) -> None:
    ensure_demo_safe_mode(settings, context="test")

    status = demo_safety_status(settings)

    assert status["EXECUTION_MODE"] == "paper"
    assert status["ALLOW_LIVE_TRADING"] == "false"
    assert status["BROKER_MODE"] == "paper"
    assert status["AUTO_BOT_ENABLED"] == "false"
    assert status["settings.execution.mode"] == "paper"


def test_demo_safe_mode_requires_safety_environment(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXECUTION_MODE", raising=False)

    with pytest.raises(DemoSafetyError, match="missing safety environment variable EXECUTION_MODE=paper"):
        ensure_demo_safe_mode(settings, context="missing env")


def test_demo_safe_mode_blocks_allow_live_trading_env(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    with pytest.raises(DemoSafetyError, match="ALLOW_LIVE_TRADING must be false"):
        ensure_demo_safe_mode(settings, context="live env")


def test_demo_safe_mode_blocks_execution_mode_live_env(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "broker_live")

    with pytest.raises(DemoSafetyError, match="EXECUTION_MODE must be paper"):
        ensure_demo_safe_mode(settings, context="live env")


def test_demo_safe_mode_blocks_broker_mode_live_env(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROKER_MODE", "broker_live")

    with pytest.raises(DemoSafetyError, match="BROKER_MODE must be paper"):
        ensure_demo_safe_mode(settings, context="live env")


def test_demo_safe_mode_blocks_live_confirmation_env(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(settings.broker.live_confirmation_env, settings.broker.live_confirmation_value)

    with pytest.raises(DemoSafetyError, match=f"{settings.broker.live_confirmation_env} must stay unset"):
        ensure_demo_safe_mode(settings, context="live confirmation")


def test_demo_safe_mode_blocks_broker_live_configuration(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_live"
    adjusted.broker.live_enabled = True
    adjusted.execution_capabilities.broker_live_enabled = True

    with pytest.raises(DemoSafetyError, match="execution.mode=broker_live"):
        ensure_demo_safe_mode(adjusted, context="live config")


def test_demo_safe_mode_blocks_auto_bot_enabled(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.safety.auto_bot_enabled = True

    with pytest.raises(DemoSafetyError, match="auto_bot_enabled"):
        ensure_demo_safe_mode(adjusted, context="bot")


def test_demo_safe_mode_requires_environment_lock_enabled(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.safety.require_environment_lock = False

    with pytest.raises(DemoSafetyError, match="require_environment_lock must remain true"):
        ensure_demo_safe_mode(adjusted, context="env lock")
