"""Structured records for rejected demo-bot scanner signals."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RejectedSignalRecord(BaseModel):
    """One scanner opportunity rejected by the paper/demo bot."""

    id: str
    cycle_id: str
    timestamp: datetime
    symbol: str
    setup: str
    status: str
    score: float | None = None
    risk_reward: float | None = None
    market_regime: str | None = None
    spread_atr: float | None = None
    rejection_reasons: list[str] = Field(default_factory=list)
    entry: float | None = None
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    provider: str | None = None
    broker: str | None = None
    style: str | None = None
