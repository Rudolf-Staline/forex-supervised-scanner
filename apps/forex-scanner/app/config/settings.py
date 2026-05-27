"""Validated JSON settings for scanner, risk, scoring, and providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.core.types import SetupFamily, Timeframe, TradingStyle

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = Path(__file__).with_name("default_settings.json")
LOCAL_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"


class ProviderSettings(BaseModel):
    """Market data provider configuration."""

    name: Literal["auto", "yahoo", "synthetic", "mt5"] = "auto"
    environment: Literal["development", "test", "production"] = "development"
    fallback_to_synthetic: bool = True
    allow_synthetic_in_production: bool = False
    synthetic_seed: int = 42690
    max_bars: int = Field(default=650, ge=220, le=5000)

    @model_validator(mode="after")
    def prevent_unapproved_synthetic_production(self) -> "ProviderSettings":
        if self.environment == "production" and not self.allow_synthetic_in_production:
            if self.name == "synthetic" or self.fallback_to_synthetic:
                raise ValueError("synthetic provider is disabled in production unless explicitly allowed")
        return self


class StyleSettings(BaseModel):
    """Timeframe, risk, and holding-period settings for one trading style."""

    higher_timeframe: Timeframe
    entry_timeframe: Timeframe
    trigger_timeframe: Timeframe
    min_rr: float = Field(gt=0.0, le=10.0)
    atr_stop_multiplier: float = Field(gt=0.1, le=10.0)
    atr_target_multiplier: float = Field(gt=0.1, le=20.0)
    swing_buffer_atr: float = Field(ge=0.0, le=3.0)
    lookback_bars: int = Field(ge=120, le=5000)
    max_hold_bars: int = Field(ge=1, le=500)
    transaction_cost_pips: float = Field(ge=0.0, le=20.0)


class ScoreWeights(BaseModel):
    """Relative component weights for the 0-100 score."""

    trend_clarity: float = Field(ge=0.0)
    structure_quality: float = Field(ge=0.0)
    multi_timeframe_alignment: float = Field(ge=0.0)
    volatility_suitability: float = Field(ge=0.0)
    momentum_confirmation: float = Field(ge=0.0)
    spread_friction: float = Field(ge=0.0)
    risk_reward: float = Field(ge=0.0)
    level_proximity: float = Field(ge=0.0)

    @model_validator(mode="after")
    def ensure_positive_total(self) -> "ScoreWeights":
        if sum(self.as_dict().values()) <= 0:
            raise ValueError("at least one score weight must be positive")
        return self

    def as_dict(self) -> dict[str, float]:
        return {
            "trend_clarity": self.trend_clarity,
            "structure_quality": self.structure_quality,
            "multi_timeframe_alignment": self.multi_timeframe_alignment,
            "volatility_suitability": self.volatility_suitability,
            "momentum_confirmation": self.momentum_confirmation,
            "spread_friction": self.spread_friction,
            "risk_reward": self.risk_reward,
            "level_proximity": self.level_proximity,
        }


class LayerWeights(BaseModel):
    """Blend weights for the multi-layer final score."""

    technical: float = Field(default=0.30, ge=0.0)
    execution: float = Field(default=0.30, ge=0.0)
    context: float = Field(default=0.24, ge=0.0)
    empirical: float = Field(default=0.16, ge=0.0)

    @model_validator(mode="after")
    def ensure_positive_total(self) -> "LayerWeights":
        if self.technical + self.execution + self.context + self.empirical <= 0.0:
            raise ValueError("at least one layer weight must be positive")
        return self

    def as_dict(self) -> dict[str, float]:
        return {
            "technical": self.technical,
            "execution": self.execution,
            "context": self.context,
            "empirical": self.empirical,
        }


class ContextSettings(BaseModel):
    """Context-scoring thresholds for session and data-quality penalties."""

    minimum_data_quality: float = Field(default=60.0, ge=0.0, le=100.0)
    dead_session_penalty: float = Field(default=12.0, ge=0.0, le=50.0)
    stale_data_penalty: float = Field(default=12.0, ge=0.0, le=50.0)
    poor_spread_atr_penalty: float = Field(default=16.0, ge=0.0, le=50.0)


class EmpiricalSettings(BaseModel):
    """Calibration-history settings used for empirical scores."""

    neutral_score: float = Field(default=55.0, ge=0.0, le=100.0)
    minimum_samples: int = Field(default=20, ge=1, le=10000)
    min_condition_samples: int = Field(default=4, ge=1, le=10000)
    shrinkage_samples: int = Field(default=36, ge=1, le=10000)
    max_adjustment: float = Field(default=18.0, ge=0.0, le=50.0)


class ApprovalSettings(BaseModel):
    """Minimum quality gates for approved and premium lifecycle states."""

    minimum_execution_score: float = Field(default=54.0, ge=0.0, le=100.0)
    minimum_context_score: float = Field(default=52.0, ge=0.0, le=100.0)
    minimum_empirical_score: float = Field(default=45.0, ge=0.0, le=100.0)
    minimum_data_quality_score: float = Field(default=58.0, ge=0.0, le=100.0)
    minimum_activation_quality: float = Field(default=45.0, ge=0.0, le=100.0)
    minimum_invalidation_quality: float = Field(default=48.0, ge=0.0, le=100.0)
    premium_final_score: float = Field(default=80.0, ge=0.0, le=100.0)
    premium_technical_score: float = Field(default=70.0, ge=0.0, le=100.0)
    premium_execution_score: float = Field(default=70.0, ge=0.0, le=100.0)
    premium_context_score: float = Field(default=68.0, ge=0.0, le=100.0)
    premium_empirical_score: float = Field(default=60.0, ge=0.0, le=100.0)
    premium_data_quality_score: float = Field(default=72.0, ge=0.0, le=100.0)
    premium_activation_quality: float = Field(default=68.0, ge=0.0, le=100.0)
    premium_invalidation_quality: float = Field(default=66.0, ge=0.0, le=100.0)


class PartialExitFractions(BaseModel):
    """Fractions closed at TP1, TP2, and TP3 during paper simulation."""

    tp1: float = Field(default=0.33, ge=0.0, le=1.0)
    tp2: float = Field(default=0.33, ge=0.0, le=1.0)
    tp3: float = Field(default=0.34, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def ensure_total_not_above_one(self) -> "PartialExitFractions":
        if self.tp1 + self.tp2 + self.tp3 > 1.000001:
            raise ValueError("partial exit fractions cannot sum above 1.0")
        return self


class ExecutionSettings(BaseModel):
    """Execution-mode settings for paper trading and future broker adapters."""

    mode: Literal["disabled", "paper", "broker_sandbox", "broker_live"] = "paper"
    default_quantity_units: float = Field(default=1.0, gt=0.0, le=1_000_000.0)
    estimated_slippage_pips: float = Field(default=0.2, ge=0.0, le=20.0)
    activation_timeout_bars: int = Field(default=12, ge=1, le=500)
    spread_aware_fills: bool = True
    partial_exit_fractions: PartialExitFractions = Field(default_factory=PartialExitFractions)
    move_stop_to_breakeven_after_tp1: bool = True
    gap_through_entry_policy: Literal["miss", "fill_at_open"] = "miss"
    cancel_on_invalidation_before_activation: bool = True


class ExecutionCapabilitySettings(BaseModel):
    """Explicit capability gates for execution paths exposed to operators."""

    paper_enabled: bool = True
    broker_sandbox_enabled: bool = True
    broker_live_enabled: bool = False


class SafetySettings(BaseModel):
    """Central demo/paper safety lock for the local MVP."""

    execution_mode: Literal["paper", "disabled", "broker_sandbox", "broker_live"] = "paper"
    allow_live_trading: bool = False
    broker_mode: Literal["paper", "broker_sandbox", "broker_live"] = "paper"
    auto_bot_enabled: bool = False
    require_environment_lock: bool = True
    execution_mode_env: str = "EXECUTION_MODE"
    allow_live_trading_env: str = "ALLOW_LIVE_TRADING"
    broker_mode_env: str = "BROKER_MODE"
    auto_bot_enabled_env: str = "AUTO_BOT_ENABLED"

    @model_validator(mode="after")
    def ensure_env_names(self) -> "SafetySettings":
        env_names = [
            self.execution_mode_env,
            self.allow_live_trading_env,
            self.broker_mode_env,
            self.auto_bot_enabled_env,
        ]
        if any(not name.strip() for name in env_names):
            raise ValueError("safety environment variable names cannot be empty")
        if len(set(env_names)) != len(env_names):
            raise ValueError("safety environment variable names must be unique")
        return self


class DemoBotSettings(BaseModel):
    """Paper-only demo bot defaults; never enables live or broker execution."""

    auto_bot_enabled: bool = False
    interval_seconds: int = Field(default=300, ge=5, le=86400)
    min_score: float = Field(default=75.0, ge=0.0, le=100.0)
    allowed_statuses: list[Literal["approved", "premium"]] = Field(default_factory=lambda: ["approved", "premium"])
    max_open_trades: int = Field(default=3, ge=1, le=100)
    max_trades_per_day: int = Field(default=5, ge=1, le=1000)
    cooldown_minutes: float = Field(default=30.0, ge=0.0, le=10080.0)
    min_rr: float = Field(default=1.5, ge=0.0, le=20.0)

    @model_validator(mode="after")
    def ensure_statuses(self) -> "DemoBotSettings":
        if not self.allowed_statuses:
            raise ValueError("demo bot allowed_statuses cannot be empty")
        return self


class PortfolioRiskSettings(BaseModel):
    """Optional portfolio/session guardrails applied before paper execution."""

    enabled: bool = True
    max_simultaneous_trades: int = Field(default=3, ge=1, le=100)
    max_exposure_per_currency: int = Field(default=2, ge=1, le=100)
    max_gross_exposure_per_currency: int = Field(default=3, ge=1, le=100)
    max_correlated_symbol_exposure: int = Field(default=2, ge=1, le=100)
    max_exposure_per_symbol: int = Field(default=1, ge=1, le=100)
    max_exposure_per_setup_family: int = Field(default=2, ge=1, le=100)
    max_exposure_per_setup_subtype: int = Field(default=1, ge=1, le=100)
    max_exposure_per_session: int = Field(default=3, ge=1, le=100)
    max_daily_loss_r: float = Field(default=3.0, gt=0.0, le=100.0)
    cooldown_after_consecutive_losses: int = Field(default=3, ge=1, le=20)
    cooldown_bars: int = Field(default=12, ge=1, le=500)
    min_data_quality_for_entry: float = Field(default=60.0, ge=0.0, le=100.0)
    max_spread_to_atr_ratio: float = Field(default=0.22, ge=0.0, le=2.0)
    block_off_hours: bool = False


class PreLiveValidationSettings(BaseModel):
    """Checks required before paper/future-live execution intent creation."""

    enabled: bool = True
    max_signal_age_minutes: float = Field(default=240.0, gt=0.0, le=10080.0)
    require_complete_levels: bool = True
    require_data_quality: bool = True
    block_invalidated_setups: bool = True
    allow_off_hours: bool = True


class BrokerSettings(BaseModel):
    """Broker adapter configuration with live trading disabled by default."""

    provider: Literal["mt5", "mock"] = "mt5"
    sandbox_enabled: bool = True
    live_enabled: bool = False
    live_confirmation_env: str = "FOREX_SCANNER_BROKER_LIVE_CONFIRM"
    live_confirmation_value: str = "ENABLE_LIVE_TRADING"
    kill_switch_env: str = "FOREX_SCANNER_BROKER_KILL_SWITCH"
    mt5_login_env: str = "FOREX_SCANNER_MT5_LOGIN"
    mt5_password_env: str = "FOREX_SCANNER_MT5_PASSWORD"
    mt5_server_env: str = "FOREX_SCANNER_MT5_SERVER"
    mt5_path_env: str = "FOREX_SCANNER_MT5_PATH"
    sandbox_requires_demo_account: bool = True
    default_volume_lots: float = Field(default=0.01, gt=0.0, le=100.0)
    max_volume_lots: float = Field(default=0.10, gt=0.0, le=100.0)
    order_deviation_points: int = Field(default=10, ge=0, le=500)
    magic_number: int = Field(default=42690, ge=0)
    comment_prefix: str = Field(default="forex-scanner", min_length=1, max_length=24)
    connect_timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)

    @model_validator(mode="after")
    def ensure_volume_bounds(self) -> "BrokerSettings":
        if self.default_volume_lots > self.max_volume_lots:
            raise ValueError("default broker volume cannot exceed max broker volume")
        return self


class BrokerSafetySettings(BaseModel):
    """Hard safety caps for broker sandbox/live execution."""

    max_notional_per_trade: float = Field(default=10_000.0, gt=0.0)
    max_risk_per_trade_pct: float = Field(default=0.5, gt=0.0, le=10.0)
    max_daily_submitted_trades: int = Field(default=3, ge=1, le=100)
    max_daily_risk_pct: float = Field(default=1.5, gt=0.0, le=25.0)
    max_repeated_rejects: int = Field(default=2, ge=1, le=20)
    max_reconciliation_anomalies: int = Field(default=1, ge=0, le=100)
    max_open_broker_positions: int = Field(default=3, ge=1, le=100)
    max_account_state_age_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    max_connectivity_failures: int = Field(default=1, ge=0, le=20)
    block_on_reconciliation_anomaly: bool = True
    block_on_unstable_connectivity: bool = True
    block_poor_session: bool = False
    require_account_state: bool = True
    require_connectivity: bool = True
    prevent_duplicate_symbol: bool = True


class BrokerRetrySettings(BaseModel):
    """Bounded broker retry policy; never infinite and never blind."""

    max_attempts: int = Field(default=2, ge=1, le=5)
    backoff_seconds: float = Field(default=0.25, ge=0.0, le=10.0)
    retry_account_state: bool = True
    retry_order_status: bool = True
    retry_position_sync: bool = True
    retry_reconciliation_refresh: bool = True
    retry_order_send_on_no_result: bool = False


class MonitoringSettings(BaseModel):
    """Local monitoring and alert thresholds for supervised broker operations."""

    enabled: bool = True
    metrics_export_enabled: bool = True
    metrics_export_path: str = "reports/broker/forex_scanner.prom"
    dashboard_output_dir: str = "docs/dashboards"
    alert_rules_enabled: bool = True
    alert_suppression_minutes: float = Field(default=30.0, ge=0.0, le=1440.0)
    alert_escalation_minutes: float = Field(default=60.0, ge=1.0, le=10080.0)
    alert_critical_age_minutes: float = Field(default=240.0, ge=1.0, le=10080.0)
    stale_account_alert_seconds: float = Field(default=180.0, gt=0.0, le=86400.0)
    stale_position_alert_seconds: float = Field(default=300.0, gt=0.0, le=86400.0)
    stale_reconciliation_alert_seconds: float = Field(default=300.0, gt=0.0, le=86400.0)
    broker_unavailable_alert_samples: int = Field(default=2, ge=1, le=10000)
    prolonged_degraded_alert_samples: int = Field(default=3, ge=1, le=10000)
    repeated_reject_alert_threshold: int = Field(default=2, ge=1, le=100)
    retry_exhausted_alert_threshold: int = Field(default=1, ge=1, le=100)
    severe_anomaly_alert_threshold: int = Field(default=1, ge=1, le=100)
    guardrail_trigger_spike_threshold: int = Field(default=3, ge=1, le=10000)
    live_submission_failure_threshold: int = Field(default=1, ge=1, le=10000)
    alert_local_sink_enabled: bool = True
    alert_local_sink_path: str = "reports/alerts/alert_events.jsonl"
    alert_webhook_enabled: bool = False
    alert_webhook_url_env: str = "FOREX_SCANNER_ALERT_WEBHOOK_URL"
    alert_webhook_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    alert_webhook_max_attempts: int = Field(default=2, ge=1, le=5)
    alert_webhook_resolved_notifications: bool = True
    persist_metric_history: bool = True

    @model_validator(mode="after")
    def ensure_alert_threshold_order(self) -> "MonitoringSettings":
        if self.alert_critical_age_minutes < self.alert_escalation_minutes:
            raise ValueError("critical alert age must be greater than or equal to escalation age")
        return self


class SoakValidationSettings(BaseModel):
    """Long-duration supervised validation settings with safe defaults."""

    default_duration_minutes: float = Field(default=30.0, gt=0.0, le=10080.0)
    default_interval_seconds: float = Field(default=60.0, ge=0.0, le=86400.0)
    output_dir: str = "reports/soak"
    allowed_modes: list[Literal["paper", "broker_sandbox", "broker_live"]] = Field(default_factory=lambda: ["paper", "broker_sandbox"])
    allow_broker_live_checks: bool = False
    max_samples: int = Field(default=10080, ge=1, le=100000)
    repeated_anomaly_sample_threshold: int = Field(default=3, ge=1, le=10000)
    unresolved_incident_minutes_warning: float = Field(default=30.0, gt=0.0, le=10080.0)
    fail_max_total_incidents: int = Field(default=10, ge=0, le=10000)
    warning_max_total_incidents: int = Field(default=2, ge=0, le=10000)
    fail_max_unresolved_severe_incidents: int = Field(default=0, ge=0, le=10000)
    fail_max_broker_unavailable_pct: float = Field(default=5.0, ge=0.0, le=100.0)
    warning_max_broker_unavailable_pct: float = Field(default=1.0, ge=0.0, le=100.0)
    fail_max_reconciliation_failure_pct: float = Field(default=5.0, ge=0.0, le=100.0)
    warning_max_reconciliation_failure_pct: float = Field(default=1.0, ge=0.0, le=100.0)
    fail_max_retry_exhausted_count: int = Field(default=0, ge=0, le=10000)
    fail_max_stale_state_detections: int = Field(default=3, ge=0, le=10000)
    fail_max_manual_intervention_count: int = Field(default=0, ge=0, le=10000)
    warning_max_health_flaps: int = Field(default=2, ge=0, le=10000)
    fail_max_health_flaps: int = Field(default=6, ge=0, le=10000)
    warning_max_degraded_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    campaign_default_duration_hours: float = Field(default=168.0, gt=0.0, le=4320.0)
    campaign_default_session_minutes: float = Field(default=30.0, gt=0.0, le=1440.0)
    campaign_output_dir: str = "reports/soak_campaigns"
    campaign_default_name: str = "supervised-validation"
    campaign_resume_existing: bool = True
    campaign_min_limited_hours: float = Field(default=4.0, ge=0.0, le=4320.0)
    campaign_min_supervised_hours: float = Field(default=24.0, ge=0.0, le=4320.0)
    campaign_limited_broker_unavailable_pct: float = Field(default=0.5, ge=0.0, le=100.0)
    campaign_not_ready_broker_unavailable_pct: float = Field(default=2.0, ge=0.0, le=100.0)
    campaign_limited_reconciliation_failure_pct: float = Field(default=0.5, ge=0.0, le=100.0)
    campaign_not_ready_reconciliation_failure_pct: float = Field(default=2.0, ge=0.0, le=100.0)
    campaign_limited_degraded_pct: float = Field(default=3.0, ge=0.0, le=100.0)
    campaign_not_ready_degraded_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    campaign_not_ready_max_unresolved_severe_incidents: int = Field(default=0, ge=0, le=10000)
    campaign_not_ready_max_retry_exhausted: int = Field(default=0, ge=0, le=10000)
    campaign_not_ready_max_manual_intervention: int = Field(default=0, ge=0, le=10000)
    campaign_limited_alert_burden_per_day: float = Field(default=5.0, ge=0.0, le=10000.0)
    campaign_not_ready_alert_burden_per_day: float = Field(default=20.0, ge=0.0, le=10000.0)
    campaign_recurring_issue_min_count: int = Field(default=2, ge=1, le=10000)
    campaign_suggested_rerun_hours: float = Field(default=24.0, gt=0.0, le=4320.0)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "SoakValidationSettings":
        if self.warning_max_broker_unavailable_pct > self.fail_max_broker_unavailable_pct:
            raise ValueError("warning broker-unavailable threshold cannot exceed fail threshold")
        if self.warning_max_reconciliation_failure_pct > self.fail_max_reconciliation_failure_pct:
            raise ValueError("warning reconciliation-failure threshold cannot exceed fail threshold")
        if self.warning_max_total_incidents > self.fail_max_total_incidents:
            raise ValueError("warning incident threshold cannot exceed fail threshold")
        if self.warning_max_health_flaps > self.fail_max_health_flaps:
            raise ValueError("warning health-flap threshold cannot exceed fail threshold")
        if self.campaign_min_limited_hours > self.campaign_min_supervised_hours:
            raise ValueError("campaign limited-readiness hours cannot exceed supervised-readiness hours")
        if self.campaign_limited_broker_unavailable_pct > self.campaign_not_ready_broker_unavailable_pct:
            raise ValueError("campaign limited broker-unavailable threshold cannot exceed not-ready threshold")
        if self.campaign_limited_reconciliation_failure_pct > self.campaign_not_ready_reconciliation_failure_pct:
            raise ValueError("campaign limited reconciliation-failure threshold cannot exceed not-ready threshold")
        if self.campaign_limited_degraded_pct > self.campaign_not_ready_degraded_pct:
            raise ValueError("campaign limited degraded threshold cannot exceed not-ready threshold")
        if self.campaign_limited_alert_burden_per_day > self.campaign_not_ready_alert_burden_per_day:
            raise ValueError("campaign limited alert-burden threshold cannot exceed not-ready threshold")
        if "broker_live" in self.allowed_modes and not self.allow_broker_live_checks:
            raise ValueError("broker_live cannot be in soak allowed_modes unless allow_broker_live_checks=true")
        return self


class OperatorWorkflowSettings(BaseModel):
    """Pre-session, authorization, and session-procedure safety settings."""

    required_checklist_items: list[
        Literal[
            "execution_mode",
            "broker_connectivity",
            "account_sync_freshness",
            "position_sync_freshness",
            "reconciliation_freshness",
            "unresolved_incidents",
            "active_severe_alerts",
            "degraded_mode_state",
            "kill_switch_state",
            "data_quality_status",
            "spread_sanity",
            "guardrail_configuration",
            "monitoring_exporter_health",
            "campaign_readiness",
        ]
    ] = Field(
        default_factory=lambda: [
            "execution_mode",
            "broker_connectivity",
            "account_sync_freshness",
            "position_sync_freshness",
            "reconciliation_freshness",
            "unresolved_incidents",
            "active_severe_alerts",
            "degraded_mode_state",
            "kill_switch_state",
            "data_quality_status",
            "spread_sanity",
            "guardrail_configuration",
            "monitoring_exporter_health",
            "campaign_readiness",
        ]
    )
    warning_open_incident_count: int = Field(default=1, ge=0, le=10000)
    fail_severe_incident_count: int = Field(default=1, ge=1, le=10000)
    warning_active_alert_count: int = Field(default=1, ge=0, le=10000)
    fail_active_severe_alert_count: int = Field(default=1, ge=1, le=10000)
    authorization_expiry_minutes: float = Field(default=120.0, gt=0.0, le=10080.0)
    minimum_readiness_for_live_authorization: Literal["limited_ready", "supervised_ready"] = "supervised_ready"
    require_campaign_readiness_for_live_authorization: bool = True
    require_checklist_acknowledgement_for_session_open: bool = True
    require_checklist_acknowledgement_for_live_authorization: bool = True
    require_handover_acceptance_before_session_open: bool = True
    require_handover_acceptance_before_live_authorization: bool = True
    handover_expiry_hours: float = Field(default=24.0, gt=0.0, le=720.0)
    mandatory_acknowledgement_min_severity: Literal["warning", "high", "critical"] = "high"
    dual_confirmation_required: bool = False

    @model_validator(mode="after")
    def validate_thresholds(self) -> "OperatorWorkflowSettings":
        if self.warning_open_incident_count > self.fail_severe_incident_count:
            raise ValueError("warning open-incident threshold cannot exceed fail severe-incident threshold")
        if self.warning_active_alert_count > self.fail_active_severe_alert_count:
            raise ValueError("warning alert threshold cannot exceed fail severe-alert threshold")
        if not self.required_checklist_items:
            raise ValueError("at least one required checklist item is required")
        return self


class OperatorIdentityDefinition(BaseModel):
    """One lightweight local operator identity bootstrap definition."""

    operator_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    role: Literal["viewer", "operator", "supervisor", "admin"]
    active: bool = True
    team: str | None = Field(default=None, max_length=128)
    shift: str | None = Field(default=None, max_length=128)
    secret_sha256: str = Field(min_length=64, max_length=64)

    @field_validator("operator_id")
    @classmethod
    def normalize_operator_id(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if not normalized:
            raise ValueError("operator_id cannot be empty")
        return normalized

    @field_validator("secret_sha256")
    @classmethod
    def validate_secret_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("secret_sha256 must be a 64-character lowercase hex digest")
        return normalized


class OperatorAuthSettings(BaseModel):
    """Local operator identity and lightweight auth/session settings."""

    identities: list[OperatorIdentityDefinition] = Field(default_factory=list)
    session_expiry_minutes: float = Field(default=480.0, gt=1.0, le=10080.0)
    approval_expiry_minutes: float = Field(default=240.0, gt=1.0, le=10080.0)
    reauth_window_minutes: float = Field(default=15.0, gt=0.0, le=1440.0)
    reauth_required_actions: list[
        Literal[
            "pre_live_authorization",
            "resume_after_major_incident",
            "accept_severe_handover",
            "enable_sensitive_execution",
            "clear_severe_blocker",
            "disable_kill_switch",
        ]
    ] = Field(
        default_factory=lambda: [
            "pre_live_authorization",
            "resume_after_major_incident",
            "accept_severe_handover",
            "enable_sensitive_execution",
            "disable_kill_switch",
        ]
    )
    approval_comment_required_actions: list[
        Literal[
            "pre_live_authorization",
            "resume_after_major_incident",
            "accept_severe_handover",
            "enable_sensitive_execution",
            "clear_severe_blocker",
            "disable_kill_switch",
        ]
    ] = Field(
        default_factory=lambda: [
            "pre_live_authorization",
            "resume_after_major_incident",
            "accept_severe_handover",
            "enable_sensitive_execution",
            "clear_severe_blocker",
            "disable_kill_switch",
        ]
    )

    @model_validator(mode="after")
    def validate_identities(self) -> "OperatorAuthSettings":
        if not self.identities:
            raise ValueError("at least one operator identity must be configured")
        operator_ids = [identity.operator_id for identity in self.identities]
        if len(operator_ids) != len(set(operator_ids)):
            raise ValueError("operator identity ids must be unique")
        if not any(identity.active for identity in self.identities):
            raise ValueError("at least one active operator identity is required")
        if not any(identity.active and identity.role in {"supervisor", "admin"} for identity in self.identities):
            raise ValueError("at least one active supervisor or admin identity is required")
        return self


class AuditIntegritySettings(BaseModel):
    """Audit integrity verification, sealing, and export behavior."""

    enabled: bool = True
    protected_record_types: list[
        Literal[
            "trade_event",
            "broker_incident",
            "operator_control",
            "pre_session_checklist",
            "live_authorization",
            "trading_session",
            "operator_action",
            "handover",
            "operator_auth_session",
            "approval_signature",
        ]
    ] = Field(
        default_factory=lambda: [
            "trade_event",
            "broker_incident",
            "operator_control",
            "pre_session_checklist",
            "live_authorization",
            "trading_session",
            "operator_action",
            "handover",
            "operator_auth_session",
            "approval_signature",
        ]
    )
    auto_seal_triggers: list[Literal["session_close", "handover", "soak_campaign", "incident_close"]] = Field(
        default_factory=lambda: ["session_close", "handover", "soak_campaign"]
    )
    strict_verification: bool = True
    verification_max_age_hours: float = Field(default=24.0, gt=0.0, le=720.0)
    block_sensitive_actions_on_verification_failure: bool = False
    failure_mode: Literal["warn", "manual_review", "block_sensitive_actions"] = "manual_review"
    report_output_dir: str = "reports/audit_integrity"
    export_output_dir: str = "reports/audit_evidence"

    @model_validator(mode="after")
    def validate_audit_integrity(self) -> "AuditIntegritySettings":
        if not self.protected_record_types:
            raise ValueError("at least one protected_record_type is required")
        if self.failure_mode == "block_sensitive_actions" and not self.block_sensitive_actions_on_verification_failure:
            raise ValueError("failure_mode=block_sensitive_actions requires block_sensitive_actions_on_verification_failure=true")
        return self


class RetentionArchiveSettings(BaseModel):
    """Local retention, archive packaging, and restore-for-review behavior."""

    enabled: bool = True
    archive_output_dir: str = "archives/operational"
    restore_output_dir: str = "archives/restore_review"
    report_output_dir: str = "reports/archives"
    archive_name_prefix: str = Field(default="forex_scanner_archive", min_length=1, max_length=80)
    audit_records_retention_days: int = Field(default=365, ge=1, le=3650)
    journal_events_retention_days: int = Field(default=365, ge=1, le=3650)
    alerts_incidents_retention_days: int = Field(default=180, ge=1, le=3650)
    monitoring_snapshots_retention_days: int = Field(default=90, ge=1, le=3650)
    soak_campaign_retention_days: int = Field(default=365, ge=1, le=3650)
    reports_exports_retention_days: int = Field(default=180, ge=1, le=3650)
    checkpoint_seals_retention_days: int = Field(default=1095, ge=1, le=3650)
    compression: Literal["zip"] = "zip"
    max_archive_records: int = Field(default=100000, ge=1, le=1000000)
    report_file_size_rotation_mb: float = Field(default=50.0, gt=0.0, le=10240.0)
    rotation_dry_run_default: bool = True
    allow_database_purge: bool = False
    allow_file_rotation: bool = False
    verify_after_archive: bool = True
    restore_overwrite_existing: bool = False
    preserve_integrity_metadata: bool = True

    @model_validator(mode="after")
    def validate_archive_safety(self) -> "RetentionArchiveSettings":
        if self.allow_database_purge and not self.verify_after_archive:
            raise ValueError("database purge requires verify_after_archive=true")
        if not self.preserve_integrity_metadata:
            raise ValueError("archive retention must preserve integrity metadata")
        return self


class AdaptiveThresholdSettings(BaseModel):
    """Configuration for dynamic minimum score adjustments based on history and style."""

    enabled: bool = False
    mode: Literal["report_only", "scanner_effective"] = "report_only"
    min_sample_size: int = Field(default=30, ge=1)
    max_daily_change: float = Field(default=2.0, ge=0.0)
    hard_floor_forex: float = Field(default=70.0, ge=0.0)
    hard_floor_commodities: float = Field(default=78.0, ge=0.0)
    hard_floor_indices: float = Field(default=80.0, ge=0.0)
    hard_cap: float = Field(default=92.0, ge=0.0, le=100.0)
    persist_latest_report: bool = True

    @model_validator(mode="after")
    def validate_adaptive_floors(self) -> "AdaptiveThresholdSettings":
        if self.hard_floor_forex > self.hard_cap or self.hard_floor_commodities > self.hard_cap or self.hard_floor_indices > self.hard_cap:
            raise ValueError("Adaptive hard floors cannot exceed hard cap")
        return self


class BackupRecoverySettings(BaseModel):
    """Local backup, restore, and service-continuity safety behavior."""

    enabled: bool = True
    backup_output_dir: str = "backups/local"
    restore_review_dir: str = "backups/restore_review"
    pre_restore_backup_dir: str = "backups/pre_restore_safety"
    report_output_dir: str = "reports/backup_recovery"
    recovery_state_path: str = "data/recovery_state.json"
    backup_name_prefix: str = Field(default="forex_scanner_backup", min_length=1, max_length=80)
    include_database: bool = True
    include_config_snapshot: bool = True
    include_archive_manifests: bool = True
    include_critical_reports: bool = False
    compression: Literal["zip"] = "zip"
    retention_count: int = Field(default=20, ge=1, le=1000)
    retention_days: int = Field(default=90, ge=1, le=3650)
    require_audit_verification_before_backup: bool = True
    verify_after_backup: bool = True
    verify_before_restore: bool = True
    allow_active_restore: bool = False
    active_restore_requires_confirmation: bool = True
    restore_overwrite_existing: bool = False
    pre_maintenance_backup_enabled: bool = True
    startup_recovery_validation_required: bool = True
    block_sensitive_actions_until_recovery_validation: bool = True

    @model_validator(mode="after")
    def validate_backup_recovery(self) -> "BackupRecoverySettings":
        if not self.include_database:
            raise ValueError("backup scope must include the active operational database")
        if self.allow_active_restore and not self.verify_before_restore:
            raise ValueError("active restore requires verify_before_restore=true")
        return self


class SetupSettings(BaseModel):
    """Rules and thresholds for setup detection."""

    enabled: dict[SetupFamily, bool]
    minimum_scores: dict[SetupFamily, float]
    pullback_ema_tolerance_atr: float = Field(gt=0.0, le=5.0)
    breakout_buffer_atr: float = Field(ge=0.0, le=5.0)
    range_rsi_low: float = Field(gt=0.0, lt=50.0)
    range_rsi_high: float = Field(gt=50.0, lt=100.0)
    level_tolerance_atr: float = Field(gt=0.0, le=5.0)

    @model_validator(mode="after")
    def ensure_required_setup_keys(self) -> "SetupSettings":
        required = {
            SetupFamily.TREND_CONTINUATION,
            SetupFamily.BREAKOUT_CONFIRMATION,
            SetupFamily.MEAN_REVERSION,
        }
        missing_enabled = required - set(self.enabled)
        missing_scores = required - set(self.minimum_scores)
        if missing_enabled:
            raise ValueError(f"missing enabled setup keys: {sorted(v.value for v in missing_enabled)}")
        if missing_scores:
            raise ValueError(f"missing minimum score keys: {sorted(v.value for v in missing_scores)}")
        return self


class RiskSettings(BaseModel):
    """Global risk-management behavior."""

    conservative_stop: bool = True
    reject_if_nearest_level_blocks_min_rr: bool = True
    max_spread_to_atr_ratio: float = Field(default=0.18, ge=0.0, le=2.0)
    target_profile: Literal["conservative", "balanced", "aggressive"] = "balanced"


class ConfidenceThresholds(BaseModel):
    """Thresholds for low, medium, and high confidence buckets."""

    medium: float = Field(default=60.0, ge=0.0, le=100.0)
    high: float = Field(default=76.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def ensure_order(self) -> "ConfidenceThresholds":
        if self.high <= self.medium:
            raise ValueError("high confidence threshold must be above medium threshold")
        return self


class AppSettings(BaseModel):
    """Top-level application settings."""

    symbols: list[str]
    provider: ProviderSettings
    database_path: str
    styles: dict[TradingStyle, StyleSettings]
    weights: ScoreWeights
    layer_weights: LayerWeights = Field(default_factory=LayerWeights)
    context: ContextSettings = Field(default_factory=ContextSettings)
    empirical: EmpiricalSettings = Field(default_factory=EmpiricalSettings)
    approval: ApprovalSettings = Field(default_factory=ApprovalSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    execution_capabilities: ExecutionCapabilitySettings = Field(default_factory=ExecutionCapabilitySettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    demo_bot: DemoBotSettings = Field(default_factory=DemoBotSettings)
    portfolio_risk: PortfolioRiskSettings = Field(default_factory=PortfolioRiskSettings)
    pre_live_validation: PreLiveValidationSettings = Field(default_factory=PreLiveValidationSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    broker_safety: BrokerSafetySettings = Field(default_factory=BrokerSafetySettings)
    broker_retry: BrokerRetrySettings = Field(default_factory=BrokerRetrySettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    soak: SoakValidationSettings = Field(default_factory=SoakValidationSettings)
    operator_workflow: OperatorWorkflowSettings = Field(default_factory=OperatorWorkflowSettings)
    operator_auth: OperatorAuthSettings = Field(default_factory=OperatorAuthSettings)
    audit_integrity: AuditIntegritySettings = Field(default_factory=AuditIntegritySettings)
    retention_archive: RetentionArchiveSettings = Field(default_factory=RetentionArchiveSettings)
    backup_recovery: BackupRecoverySettings = Field(default_factory=BackupRecoverySettings)
    setups: SetupSettings
    risk: RiskSettings
    confidence_thresholds: ConfidenceThresholds
    adaptive_thresholds: AdaptiveThresholdSettings = Field(default_factory=AdaptiveThresholdSettings)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, value: list[str]) -> list[str]:
        symbols = [symbol.strip().upper() for symbol in value if symbol.strip()]
        if not symbols:
            raise ValueError("at least one symbol is required")
        return symbols

    @field_validator("styles")
    @classmethod
    def ensure_required_styles(
        cls,
        value: dict[TradingStyle, StyleSettings],
        info: ValidationInfo,
    ) -> dict[TradingStyle, StyleSettings]:
        required = {
            TradingStyle.SCALPING,
            TradingStyle.DAY_TRADING,
            TradingStyle.SWING_TRADING,
        }
        missing = required - set(value)
        if missing:
            raise ValueError(f"missing style settings: {sorted(v.value for v in missing)}")
        return value

    @property
    def database_absolute_path(self) -> Path:
        path = Path(self.database_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @model_validator(mode="after")
    def validate_broker_mode(self) -> "AppSettings":
        if self.execution.mode == "paper" and not self.execution_capabilities.paper_enabled:
            raise ValueError("paper execution mode requires execution_capabilities.paper_enabled=true")
        if self.execution.mode == "broker_sandbox" and not self.execution_capabilities.broker_sandbox_enabled:
            raise ValueError("broker_sandbox mode requires execution_capabilities.broker_sandbox_enabled=true")
        if self.execution.mode == "broker_live" and not self.execution_capabilities.broker_live_enabled:
            raise ValueError("broker_live mode requires execution_capabilities.broker_live_enabled=true")
        if self.execution.mode == "broker_live" and not self.broker.live_enabled:
            raise ValueError("broker_live mode requires broker.live_enabled=true")
        if self.execution.mode == "broker_sandbox" and not self.broker.sandbox_enabled:
            raise ValueError("broker_sandbox mode requires broker.sandbox_enabled=true")
        if self.execution.mode == "broker_live" and self.broker.provider == "mock":
            raise ValueError("mock broker provider cannot be used for broker_live mode")
        return self


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings from an explicit path, env var, local override, or defaults."""

    chosen = _resolve_settings_path(path)
    with chosen.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return AppSettings.model_validate(payload)


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    """Persist settings as JSON and return the written path."""

    target = path or LOCAL_SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump(mode="json")
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return target


def load_settings_payload(path: Path | None = None) -> dict[str, object]:
    """Return raw settings JSON for UI editing."""

    chosen = _resolve_settings_path(path)
    with chosen.open("r", encoding="utf-8") as handle:
        payload: dict[str, object] = json.load(handle)
    return payload


def _resolve_settings_path(path: Path | None) -> Path:
    if path is not None:
        return path
    env_path = os.getenv("FOREX_SCANNER_CONFIG")
    if env_path:
        return Path(env_path)
    if LOCAL_SETTINGS_PATH.exists():
        return LOCAL_SETTINGS_PATH
    return DEFAULT_SETTINGS_PATH
