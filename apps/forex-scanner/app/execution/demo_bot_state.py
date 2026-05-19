"""Runtime state and logs for the Streamlit demo bot controls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field


class DemoBotLogEntry(BaseModel):
    """Operator-facing log line for demo bot activity."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    level: str = "info"
    message: str


class DemoBotRuntimeState(BaseModel):
    """Transient UI state; persisted trade decisions live in SQLite events."""

    running: bool = False
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_cycle_at: datetime | None = None
    logs: list[DemoBotLogEntry] = Field(default_factory=list)

    @property
    def status(self) -> str:
        return "RUNNING" if self.running else "STOPPED"

    def start(self) -> "DemoBotRuntimeState":
        now = datetime.now(timezone.utc)
        return self.model_copy(
            update={
                "running": True,
                "started_at": self.started_at or now,
                "stopped_at": None,
                "logs": _append_log(self.logs, "info", "Demo bot started."),
            }
        )

    def stop(self) -> "DemoBotRuntimeState":
        return self.model_copy(
            update={
                "running": False,
                "stopped_at": datetime.now(timezone.utc),
                "logs": _append_log(self.logs, "info", "Demo bot stopped."),
            }
        )

    def mark_cycle(self, message: str) -> "DemoBotRuntimeState":
        now = datetime.now(timezone.utc)
        return self.model_copy(update={"last_cycle_at": now, "logs": _append_log(self.logs, "info", message)})

    def due_for_cycle(self, interval_seconds: int) -> bool:
        if not self.running:
            return False
        if self.last_cycle_at is None:
            return False
        return datetime.now(timezone.utc) - self.last_cycle_at >= timedelta(seconds=interval_seconds)


def _append_log(logs: list[DemoBotLogEntry], level: str, message: str) -> list[DemoBotLogEntry]:
    return [DemoBotLogEntry(level=level, message=message), *logs][:100]
