"""Lightweight backup/recovery types shared with live-gating code."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ContinuityMode(str, Enum):
    """Local continuity mode after backup/restore/recovery actions."""

    NORMAL = "normal"
    DEGRADED = "degraded"
    RESTORE_REVIEW = "restore_review"
    POST_RESTORE_VALIDATION = "post_restore_validation"
    BLOCKED_PENDING_OPERATOR_REVIEW = "blocked_pending_operator_review"


class RecoveryValidationStatus(str, Enum):
    """Post-restore validation result."""

    PENDING = "pending"
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class RecoveryValidationResult(BaseModel):
    """Post-restore/startup continuity validation result."""

    validation_id: str
    validated_at: datetime
    mode: ContinuityMode
    status: RecoveryValidationStatus
    database_path: str
    sqlite_integrity_status: str
    audit_verification_status: str | None = None
    open_incidents: int = 0
    active_alerts: int = 0
    severe_anomalies: int = 0
    open_broker_orders: int = 0
    sensitive_actions_blocked: bool = True
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)

