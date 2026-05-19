"""Configuration helpers for the paper-only demo bot."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field, field_validator

from app.config.settings import AppSettings

EXECUTABLE_DEMO_STATUSES = {"approved", "premium"}


class DemoBotConfig(BaseModel):
    """Effective runtime thresholds for one demo bot cycle."""

    auto_bot_enabled: bool = False
    interval_seconds: int = Field(default=300, ge=5, le=86400)
    min_score: float = Field(default=75.0, ge=0.0, le=100.0)
    allowed_statuses: list[str] = Field(default_factory=lambda: ["approved", "premium"])
    max_open_trades: int = Field(default=3, ge=1, le=100)
    max_trades_per_day: int = Field(default=5, ge=1, le=1000)
    cooldown_minutes: float = Field(default=30.0, ge=0.0, le=10080.0)
    min_rr: float = Field(default=1.5, ge=0.0, le=20.0)

    @field_validator("allowed_statuses")
    @classmethod
    def ensure_only_executable_statuses(cls, value: list[str]) -> list[str]:
        statuses = [item.strip().lower() for item in value if item.strip()]
        invalid = [item for item in statuses if item not in EXECUTABLE_DEMO_STATUSES]
        if invalid:
            raise ValueError("demo bot allowed_statuses can only contain approved,premium")
        if not statuses:
            raise ValueError("demo bot allowed_statuses cannot be empty")
        return statuses

    @classmethod
    def from_settings(cls, settings: AppSettings) -> "DemoBotConfig":
        bot = settings.demo_bot
        return cls(
            auto_bot_enabled=_env_bool("AUTO_BOT_ENABLED", bot.auto_bot_enabled),
            interval_seconds=_env_int("AUTO_BOT_INTERVAL_SECONDS", bot.interval_seconds),
            min_score=_env_float("AUTO_BOT_MIN_SCORE", bot.min_score),
            allowed_statuses=_env_statuses("AUTO_BOT_ALLOWED_STATUSES", bot.allowed_statuses),
            max_open_trades=_env_int("AUTO_BOT_MAX_OPEN_TRADES", bot.max_open_trades),
            max_trades_per_day=_env_int("AUTO_BOT_MAX_TRADES_PER_DAY", bot.max_trades_per_day),
            cooldown_minutes=_env_float("AUTO_BOT_COOLDOWN_MINUTES", bot.cooldown_minutes),
            min_rr=_env_float("AUTO_BOT_MIN_RR", bot.min_rr),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_statuses(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)
    statuses = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return statuses or list(default)
