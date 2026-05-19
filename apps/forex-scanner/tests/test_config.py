"""Configuration loading tests."""

from app.config.settings import load_settings
from app.core.types import Timeframe, TradingStyle


def test_default_config_loads_required_styles() -> None:
    settings = load_settings()
    assert settings.styles[TradingStyle.SCALPING].higher_timeframe == Timeframe.M15
    assert settings.styles[TradingStyle.DAY_TRADING].min_rr == 1.5
    assert settings.weights.as_dict()["trend_clarity"] > 0
    assert settings.layer_weights.as_dict()["execution"] >= settings.layer_weights.as_dict()["technical"] - 0.05
    assert settings.layer_weights.as_dict()["context"] >= 0.20
    assert settings.layer_weights.as_dict()["empirical"] >= 0.15
    assert settings.empirical.neutral_score == 55.0
    assert settings.empirical.shrinkage_samples >= settings.empirical.minimum_samples
    assert settings.approval.minimum_execution_score > 0
    assert settings.approval.minimum_data_quality_score >= settings.context.minimum_data_quality - 5.0
    assert settings.approval.premium_data_quality_score > settings.approval.minimum_data_quality_score
    assert settings.approval.premium_activation_quality > settings.approval.minimum_activation_quality
    assert settings.approval.premium_invalidation_quality > settings.approval.minimum_invalidation_quality
    assert settings.execution.mode == "paper"
    assert settings.execution_capabilities.paper_enabled
    assert settings.execution_capabilities.broker_sandbox_enabled
    assert not settings.execution_capabilities.broker_live_enabled
    assert settings.safety.execution_mode == "paper"
    assert not settings.safety.allow_live_trading
    assert settings.safety.broker_mode == "paper"
    assert not settings.safety.auto_bot_enabled
    assert settings.safety.require_environment_lock
    assert not settings.demo_bot.auto_bot_enabled
    assert settings.demo_bot.interval_seconds == 300
    assert settings.demo_bot.min_score == 75.0
    assert settings.demo_bot.allowed_statuses == ["approved", "premium"]
    assert settings.demo_bot.max_open_trades == 3
    assert settings.demo_bot.max_trades_per_day == 5
    assert settings.demo_bot.cooldown_minutes == 30.0
    assert settings.demo_bot.min_rr == 1.5
    assert settings.execution.spread_aware_fills
    assert settings.execution.partial_exit_fractions.tp1 + settings.execution.partial_exit_fractions.tp2 + settings.execution.partial_exit_fractions.tp3 <= 1.0
    assert settings.portfolio_risk.enabled
    assert settings.portfolio_risk.max_exposure_per_symbol >= 1
    assert settings.portfolio_risk.max_exposure_per_setup_subtype >= 1
    assert settings.portfolio_risk.max_gross_exposure_per_currency >= settings.portfolio_risk.max_exposure_per_currency
    assert settings.portfolio_risk.max_correlated_symbol_exposure >= 1
    assert settings.pre_live_validation.enabled
    assert settings.pre_live_validation.max_signal_age_minutes > 0
    assert settings.broker.provider == "mt5"
    assert settings.broker.sandbox_enabled
    assert not settings.broker.live_enabled
    assert settings.broker.default_volume_lots <= settings.broker.max_volume_lots
    assert settings.broker_safety.block_on_reconciliation_anomaly
    assert settings.broker_safety.max_daily_submitted_trades >= 1
    assert settings.broker_safety.max_account_state_age_seconds > 0
    assert settings.broker_safety.max_daily_risk_pct > 0
    assert settings.broker_retry.max_attempts >= 1
    assert not settings.broker_retry.retry_order_send_on_no_result
    assert settings.monitoring.enabled
    assert settings.monitoring.alert_suppression_minutes >= 0
    assert settings.monitoring.metrics_export_enabled
    assert settings.monitoring.metrics_export_path.endswith(".prom")
    assert settings.monitoring.severe_anomaly_alert_threshold >= 1
    assert settings.monitoring.alert_escalation_minutes > 0
    assert settings.monitoring.alert_critical_age_minutes > settings.monitoring.alert_escalation_minutes
    assert settings.monitoring.dashboard_output_dir.endswith("dashboards")
    assert settings.monitoring.alert_rules_enabled
    assert settings.monitoring.alert_local_sink_enabled
    assert settings.monitoring.alert_local_sink_path.endswith(".jsonl")
    assert not settings.monitoring.alert_webhook_enabled
    assert settings.monitoring.alert_webhook_url_env == "FOREX_SCANNER_ALERT_WEBHOOK_URL"
    assert settings.soak.campaign_default_duration_hours >= 24.0
    assert settings.soak.campaign_min_limited_hours <= settings.soak.campaign_min_supervised_hours
    assert settings.soak.campaign_output_dir.endswith("soak_campaigns")
    assert "execution_mode" in settings.operator_workflow.required_checklist_items
    assert settings.operator_workflow.authorization_expiry_minutes > 0
    assert settings.operator_workflow.minimum_readiness_for_live_authorization in {"limited_ready", "supervised_ready"}
    assert settings.operator_workflow.require_checklist_acknowledgement_for_session_open
    assert settings.operator_workflow.require_checklist_acknowledgement_for_live_authorization
    assert settings.operator_workflow.require_handover_acceptance_before_session_open
    assert settings.operator_workflow.require_handover_acceptance_before_live_authorization
    assert settings.operator_workflow.handover_expiry_hours > 0
    assert settings.operator_workflow.mandatory_acknowledgement_min_severity in {"warning", "high", "critical"}
    assert settings.operator_workflow.fail_active_severe_alert_count >= settings.operator_workflow.warning_active_alert_count
    assert settings.operator_auth.identities
    assert any(identity.role in {"supervisor", "admin"} for identity in settings.operator_auth.identities if identity.active)
    assert settings.operator_auth.session_expiry_minutes > 0
    assert settings.operator_auth.approval_expiry_minutes > 0
    assert settings.operator_auth.reauth_window_minutes > 0
    assert "pre_live_authorization" in settings.operator_auth.reauth_required_actions
    assert "disable_kill_switch" in settings.operator_auth.approval_comment_required_actions
    assert settings.audit_integrity.enabled
    assert settings.audit_integrity.report_output_dir.endswith("audit_integrity")
    assert settings.retention_archive.enabled
    assert settings.retention_archive.archive_output_dir.endswith("operational")
    assert settings.retention_archive.restore_output_dir.endswith("restore_review")
    assert settings.retention_archive.audit_records_retention_days >= settings.retention_archive.journal_events_retention_days
    assert settings.retention_archive.report_file_size_rotation_mb > 0
    assert not settings.retention_archive.allow_database_purge
    assert settings.retention_archive.rotation_dry_run_default
    assert settings.retention_archive.preserve_integrity_metadata
    assert settings.backup_recovery.enabled
    assert settings.backup_recovery.backup_output_dir.endswith("local")
    assert settings.backup_recovery.restore_review_dir.endswith("restore_review")
    assert settings.backup_recovery.include_database
    assert settings.backup_recovery.include_config_snapshot
    assert settings.backup_recovery.include_archive_manifests
    assert settings.backup_recovery.verify_before_restore
    assert not settings.backup_recovery.allow_active_restore
    assert settings.backup_recovery.block_sensitive_actions_until_recovery_validation
    assert settings.soak.default_duration_minutes > 0
    assert settings.soak.default_interval_seconds >= 0
    assert "broker_sandbox" in settings.soak.allowed_modes
    assert "broker_live" not in settings.soak.allowed_modes
    assert not settings.soak.allow_broker_live_checks
    assert settings.soak.fail_max_broker_unavailable_pct >= settings.soak.warning_max_broker_unavailable_pct
    assert "EUR/JPY" in settings.symbols
    assert not settings.provider.allow_synthetic_in_production
