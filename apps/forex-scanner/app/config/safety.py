"""Central paper/demo safety lock for local execution paths."""

from __future__ import annotations

import os

from app.config.settings import AppSettings


class DemoSafetyError(RuntimeError):
    """Raised when the runtime is not explicitly locked to paper/demo mode."""


def ensure_broker_live_disabled(settings: AppSettings, *, context: str = "runtime") -> None:
    """Refuse any live-broker capability for the local paper/demo MVP."""

    reasons = _live_disabled_reasons(settings)
    reasons.extend(_live_environment_reasons(settings))
    if reasons:
        detail = "; ".join(reasons)
        raise DemoSafetyError(f"demo safety lock blocked {context}: {detail}")


def ensure_demo_safe_mode(
    settings: AppSettings,
    *,
    context: str = "runtime",
    require_paper_execution: bool = True,
    allowed_broker_modes: tuple[str, ...] = ("paper",),
) -> None:
    """Refuse execution unless the local MVP is locked to paper/demo mode."""

    reasons = _configuration_reasons(settings, require_paper_execution=require_paper_execution)
    reasons.extend(_environment_reasons(settings, allowed_broker_modes=allowed_broker_modes))
    if reasons:
        detail = "; ".join(reasons)
        raise DemoSafetyError(f"demo safety lock blocked {context}: {detail}")


def ensure_mt5_demo_safe_mode(settings: AppSettings, *, context: str = "runtime") -> None:
    """Refuse MT5 demo execution unless it is explicitly locked to demo-only mode."""

    reasons: list[str] = []
    try:
        ensure_demo_safe_mode(settings, context=context, allowed_broker_modes=("mt5_demo",))
    except DemoSafetyError as exc:
        reasons.append(str(exc).split(": ", 1)[-1])
    reasons.extend(_mt5_demo_environment_reasons())
    if reasons:
        detail = "; ".join(reasons)
        raise DemoSafetyError(f"demo safety lock blocked {context}: {detail}")


def ensure_demo_bot_safe_mode(settings: AppSettings, *, context: str = "demo bot") -> None:
    """Allow demo bot cycles in paper mode, or in explicit MT5 demo mode."""

    broker_mode = os.getenv(settings.safety.broker_mode_env, "").strip().lower()
    if broker_mode == "mt5_demo":
        ensure_mt5_demo_safe_mode(settings, context=context)
        return
    ensure_demo_safe_mode(settings, context=context)


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
    if not safety.require_environment_lock:
        reasons.append("safety.require_environment_lock must remain true for sensitive paths")
    if settings.execution.mode == "broker_live":
        reasons.append("execution.mode=broker_live is not allowed")
    if settings.execution_capabilities.broker_live_enabled:
        reasons.append("execution_capabilities.broker_live_enabled must remain false")
    if settings.broker.live_enabled:
        reasons.append("broker.live_enabled must remain false")
    if require_paper_execution and settings.execution.mode != "paper":
        reasons.append(f"execution.mode must be paper, got {settings.execution.mode}")
    return reasons


def _environment_reasons(settings: AppSettings, *, allowed_broker_modes: tuple[str, ...]) -> list[str]:
    safety = settings.safety
    checks = [
        (safety.execution_mode_env, "paper"),
        (safety.allow_live_trading_env, "false"),
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
    broker_mode = os.getenv(safety.broker_mode_env)
    if broker_mode is None:
        reasons.append(f"missing safety environment variable {safety.broker_mode_env}={allowed_broker_modes[0]}")
    elif broker_mode.strip().lower() not in allowed_broker_modes:
        expected = allowed_broker_modes[0] if len(allowed_broker_modes) == 1 else "one of " + ",".join(allowed_broker_modes)
        reasons.append(f"{safety.broker_mode_env} must be {expected}, got {broker_mode}")
    reasons.extend(_live_environment_reasons(settings))
    return reasons


def _live_disabled_reasons(settings: AppSettings) -> list[str]:
    safety = settings.safety
    reasons: list[str] = []
    if settings.execution.mode == "broker_live":
        reasons.append("execution.mode=broker_live is not allowed in the MVP")
    if settings.execution_capabilities.broker_live_enabled:
        reasons.append("execution_capabilities.broker_live_enabled must remain false")
    if settings.broker.live_enabled:
        reasons.append("broker.live_enabled must remain false")
    if safety.execution_mode == "broker_live":
        reasons.append("safety.execution_mode=broker_live is not allowed")
    if safety.broker_mode == "broker_live":
        reasons.append("safety.broker_mode=broker_live is not allowed")
    if safety.allow_live_trading:
        reasons.append("safety.allow_live_trading must remain false")
    return reasons


def _live_environment_reasons(settings: AppSettings) -> list[str]:
    raw = os.getenv(settings.broker.live_confirmation_env)
    if raw is not None and raw.strip():
        return [f"{settings.broker.live_confirmation_env} must stay unset in MVP paper/demo mode"]
    return []


def _mt5_demo_environment_reasons() -> list[str]:
    reasons: list[str] = []
    raw = os.getenv("MT5_DEMO_ONLY")
    if raw is None:
        reasons.append("missing safety environment variable MT5_DEMO_ONLY=true")
    elif raw.strip().lower() != "true":
        reasons.append(f"MT5_DEMO_ONLY must be true, got {raw}")
    return reasons
