"""Central paper/demo safety lock for local execution paths."""

from __future__ import annotations

import os

from app.config.settings import AppSettings


class DemoSafetyError(RuntimeError):
    """Raised when the runtime is not explicitly locked to paper/demo mode."""


def ensure_demo_safe_mode(
    settings: AppSettings,
    *,
    context: str = "runtime",
    require_paper_execution: bool = True,
) -> None:
    """Refuse execution unless the local MVP is locked to paper/demo mode."""

    reasons = _configuration_reasons(settings, require_paper_execution=require_paper_execution)
    reasons.extend(_environment_reasons(settings))
    if reasons:
        detail = "; ".join(reasons)
        raise DemoSafetyError(f"demo safety lock blocked {context}: {detail}")


def demo_safety_status(settings: AppSettings) -> dict[str, str | bool]:
    """Return a compact operator-readable safety snapshot."""

    safety = settings.safety
    return {
        safety.execution_mode_env: os.getenv(safety.execution_mode_env, ""),
        safety.allow_live_trading_env: os.getenv(safety.allow_live_trading_env, ""),
        safety.broker_mode_env: os.getenv(safety.broker_mode_env, ""),
        safety.auto_bot_enabled_env: os.getenv(safety.auto_bot_enabled_env, ""),
        "settings.execution.mode": settings.execution.mode,
        "settings.broker.live_enabled": settings.broker.live_enabled,
        "settings.execution_capabilities.broker_live_enabled": settings.execution_capabilities.broker_live_enabled,
        "settings.safety.execution_mode": safety.execution_mode,
        "settings.safety.allow_live_trading": safety.allow_live_trading,
        "settings.safety.broker_mode": safety.broker_mode,
        "settings.safety.auto_bot_enabled": safety.auto_bot_enabled,
    }


def _configuration_reasons(settings: AppSettings, *, require_paper_execution: bool) -> list[str]:
    safety = settings.safety
    reasons: list[str] = []
    if safety.execution_mode != "paper":
        reasons.append(f"safety.execution_mode must be paper, got {safety.execution_mode}")
    if safety.allow_live_trading:
        reasons.append("safety.allow_live_trading must remain false")
    if safety.broker_mode != "paper":
        reasons.append(f"safety.broker_mode must be paper, got {safety.broker_mode}")
    if safety.auto_bot_enabled:
        reasons.append("safety.auto_bot_enabled must remain false for the MVP")
    if settings.execution.mode == "broker_live":
        reasons.append("execution.mode=broker_live is not allowed")
    if settings.execution_capabilities.broker_live_enabled:
        reasons.append("execution_capabilities.broker_live_enabled must remain false")
    if settings.broker.live_enabled:
        reasons.append("broker.live_enabled must remain false")
    if require_paper_execution and settings.execution.mode != "paper":
        reasons.append(f"execution.mode must be paper, got {settings.execution.mode}")
    return reasons


def _environment_reasons(settings: AppSettings) -> list[str]:
    safety = settings.safety
    if not safety.require_environment_lock:
        return []
    checks = [
        (safety.execution_mode_env, "paper"),
        (safety.allow_live_trading_env, "false"),
        (safety.broker_mode_env, "paper"),
        (safety.auto_bot_enabled_env, "false"),
    ]
    reasons: list[str] = []
    for name, expected in checks:
        actual = os.getenv(name)
        if actual is None:
            reasons.append(f"missing safety environment variable {name}={expected}")
            continue
        if actual.strip().lower() != expected:
            reasons.append(f"{name} must be {expected}, got {actual}")
    if os.getenv(settings.broker.live_confirmation_env):
        reasons.append(f"{settings.broker.live_confirmation_env} must stay unset in MVP paper/demo mode")
    return reasons
