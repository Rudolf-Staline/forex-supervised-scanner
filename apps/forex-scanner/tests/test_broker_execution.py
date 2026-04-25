"""Broker adapter, safety validation, reconciliation, and reporting tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config.settings import AppSettings
from app.core.types import ConfidenceBucket, DataQualityDiagnostic, DirectionBias, MarketRegime, Opportunity, OpportunityStatus, SessionName, SetupFamily, SetupSubtype, Timeframe, TradingStyle
from app.execution.broker import BrokerExecutionError, MockBrokerExecutor, build_execution_adapter
from app.execution.broker_service import BrokerExecutionService
from app.execution.broker_validation import BrokerPreTradeValidator, BrokerValidationContext
from app.execution.models import BrokerAccountState, BrokerOrderSnapshot, BrokerOrderState, OrderRequest, TradeEventType
from app.execution.mt5 import MT5BrokerExecutor
from app.execution.operations import AlertSeverity, AlertStatus, OperatorControlState, ResumeReadinessStatus, assess_resume_readiness, build_operational_metrics, build_broker_health_snapshot, build_reliability_summary, classify_broker_incidents, generate_operational_alerts, merge_operational_incidents, operational_events_from_snapshot_and_incidents, operator_control_block_reasons, resolve_operational_alerts, resolve_recovered_incidents, run_startup_recovery
from app.execution.paper import PaperExecutor
from app.execution.reconciliation import ReconciliationAnomalyType, reconcile_broker_state
from app.reporting.broker import generate_broker_execution_report
from app.reporting.monitoring import build_prometheus_text, write_prometheus_textfile
from app.storage.database import Database


def _broker_settings(settings):
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_sandbox"
    adjusted.broker.provider = "mock"
    adjusted.broker.default_volume_lots = 0.01
    adjusted.broker.max_volume_lots = 0.02
    return adjusted


def _request() -> OrderRequest:
    return OrderRequest(
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        direction=DirectionBias.LONG,
        quantity_units=0.01,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        source_status="approved",
    )


def _opportunity() -> Opportunity:
    return Opportunity(
        timestamp=datetime.now(timezone.utc),
        symbol="EUR/USD",
        style=TradingStyle.DAY_TRADING,
        setup_family=SetupFamily.TREND_CONTINUATION,
        setup_subtype=SetupSubtype.SHALLOW_EMA20_PULLBACK,
        regime=MarketRegime.TRENDING_UP,
        direction=DirectionBias.LONG,
        score=78.0,
        confidence=ConfidenceBucket.HIGH,
        entry=1.1,
        stop_loss=1.095,
        take_profit=1.11,
        risk_reward=2.0,
        explanation="broker test",
        timeframe_higher=Timeframe.H1,
        timeframe_entry=Timeframe.M15,
        timeframe_trigger=Timeframe.M5,
        score_components={},
        provider="synthetic",
        approved=True,
        status=OpportunityStatus.APPROVED,
        raw_setup_family=SetupFamily.TREND_CONTINUATION,
        technical_score=78.0,
        execution_score=72.0,
        context_score=70.0,
        empirical_score=58.0,
        final_score=76.0,
        tp1=1.105,
        tp2=1.11,
        tp3=1.115,
        spread=0.0001,
        atr=0.0012,
        data_quality=DataQualityDiagnostic(score=95.0, missing_bars=0, spread_available=True, resampled=False),
        session=SessionName.LONDON,
    )


def test_execution_mode_selection_keeps_paper_default_and_supports_mock_sandbox(settings) -> None:
    paper = build_execution_adapter(settings)
    sandbox_settings = _broker_settings(settings)
    broker = build_execution_adapter(sandbox_settings)

    assert isinstance(paper, PaperExecutor)
    assert isinstance(broker, MockBrokerExecutor)


def test_mock_broker_state_machine_and_account_query(settings) -> None:
    broker = MockBrokerExecutor(_broker_settings(settings))

    order = broker.place_order(_request())
    modified = broker.modify_order(order.order_id, stop_loss=1.0960)
    closed = broker.close_order(order.order_id, 1.1040)
    account = broker.query_account_state()

    states = [transition.state for transition in closed.broker_transitions]
    assert BrokerOrderState.INTENT_CREATED in states
    assert BrokerOrderState.SUBMITTED in states
    assert BrokerOrderState.ACKNOWLEDGED in states
    assert BrokerOrderState.MODIFIED in states
    assert BrokerOrderState.CLOSE_REQUESTED in states
    assert BrokerOrderState.CLOSED in states
    assert modified.request.stop_loss == 1.0960
    assert account.connected and account.can_trade
    assert any(event.event_type == TradeEventType.BROKER_CLOSED for event in closed.events)


def test_broker_validation_allows_clean_sandbox_and_blocks_operational_risks(settings, monkeypatch) -> None:
    sandbox_settings = _broker_settings(settings)
    validator = BrokerPreTradeValidator(sandbox_settings)
    account = BrokerAccountState(broker="mock", mode="broker_sandbox", connected=True, can_trade=True, balance=100_000.0, equity=100_000.0, free_margin=90_000.0, is_demo=True)

    allowed = validator.validate_request(_request(), [], account)
    blocked_rejects = validator.validate_request(_request(), [], account, context=BrokerValidationContext(account_state=account, repeated_rejects=2))

    assert allowed.allowed
    assert not blocked_rejects.allowed
    assert any("repeated broker reject" in reason for reason in blocked_rejects.reasons)

    live_settings = settings.model_copy(deep=True)
    live_settings.execution.mode = "broker_live"
    live_settings.broker.live_enabled = True
    monkeypatch.delenv(live_settings.broker.live_confirmation_env, raising=False)
    live_validator = BrokerPreTradeValidator(live_settings)
    live_block = live_validator.validate_request(_request(), [], account)

    assert not live_block.allowed
    assert any("missing live confirmation" in reason for reason in live_block.reasons)


def test_broker_validation_blocks_kill_switch_stale_account_and_connectivity(settings, monkeypatch) -> None:
    live_settings = settings.model_copy(deep=True)
    live_settings.execution.mode = "broker_live"
    live_settings.broker.live_enabled = True
    monkeypatch.setenv(live_settings.broker.live_confirmation_env, live_settings.broker.live_confirmation_value)
    monkeypatch.setenv(live_settings.broker.kill_switch_env, "true")
    account = BrokerAccountState(
        broker="mt5",
        mode="broker_live",
        connected=True,
        can_trade=True,
        balance=100_000.0,
        equity=100_000.0,
        free_margin=90_000.0,
        retrieved_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        consecutive_failures=3,
    )
    validator = BrokerPreTradeValidator(live_settings)

    decision = validator.validate_request(_request(), [], account, context=BrokerValidationContext(account_state=account, connectivity_failures=3, daily_risk_used_pct=2.0))

    assert not decision.allowed
    assert any("kill switch" in reason for reason in decision.reasons)
    assert any("account state age" in reason for reason in decision.reasons)
    assert any("connectivity failures" in reason for reason in decision.reasons)
    assert any("daily broker risk budget" in reason for reason in decision.reasons)


def test_broker_health_snapshot_and_incident_classification_for_degraded_state(settings) -> None:
    adjusted = _broker_settings(settings)
    account = BrokerAccountState(
        broker="mt5",
        mode="broker_sandbox",
        connected=False,
        can_trade=False,
        last_error="MT5 terminal not reachable",
        consecutive_failures=3,
    )

    snapshot = build_broker_health_snapshot(account, [], [], adjusted)
    incidents = classify_broker_incidents(account, [], [], adjusted)
    events = operational_events_from_snapshot_and_incidents(snapshot, incidents)

    assert snapshot.health_status == "unavailable"
    assert "broker_unavailable" in snapshot.degraded_flags
    assert any(incident.blocks_execution for incident in incidents)
    assert any(event.event_type == TradeEventType.BROKER_HEALTH_DEGRADED for event in events)
    assert any(event.event_type == TradeEventType.BROKER_EXECUTION_BLOCKED_OPERATIONAL for event in events)


def test_operational_metrics_and_alerts_are_generated_and_deduplicated(settings) -> None:
    adjusted = _broker_settings(settings)
    account = BrokerAccountState(
        broker="mt5",
        mode="broker_sandbox",
        connected=False,
        can_trade=False,
        last_error="terminal unavailable",
        consecutive_failures=2,
    )
    snapshot = build_broker_health_snapshot(account, [], [], adjusted)
    incidents = classify_broker_incidents(account, [], [], adjusted)

    metrics = build_operational_metrics(snapshot, incidents, [], [])
    alerts = generate_operational_alerts(snapshot, incidents, [], [], adjusted, [])
    repeated = generate_operational_alerts(snapshot, incidents, [], [], adjusted, alerts)
    resolved = resolve_operational_alerts(alerts, [])

    assert any(metric.name == "broker_connected" and metric.value == 0.0 for metric in metrics)
    assert any(alert.category.value == "broker_down" for alert in alerts)
    assert any(alert.status == AlertStatus.SUPPRESSED for alert in repeated)
    assert resolved and all(alert.status == AlertStatus.RESOLVED for alert in resolved)


def test_alert_aging_escalates_unresolved_alerts(settings) -> None:
    adjusted = _broker_settings(settings)
    previous_time = datetime.now(timezone.utc) - timedelta(minutes=adjusted.monitoring.alert_critical_age_minutes + 5.0)
    account = BrokerAccountState(broker="mt5", mode="broker_sandbox", connected=False, can_trade=False, last_error="terminal unavailable")
    old_snapshot = build_broker_health_snapshot(account, [], [], adjusted, now=previous_time)
    previous_alerts = generate_operational_alerts(old_snapshot, classify_broker_incidents(account, [], [], adjusted, now=previous_time), [], [], adjusted, [])
    current_snapshot = build_broker_health_snapshot(account, [], [], adjusted)

    aged = generate_operational_alerts(current_snapshot, classify_broker_incidents(account, [], [], adjusted), [], [], adjusted, previous_alerts)

    assert any(alert.severity == AlertSeverity.CRITICAL for alert in aged)
    assert any("escalated" in alert.reason for alert in aged)


def test_operator_controls_and_resume_readiness_block_live_until_clean(settings) -> None:
    adjusted = _broker_settings(settings)
    controls = OperatorControlState(updated_at=datetime.now(timezone.utc), maintenance_mode=True, live_submissions_enabled=False)
    account = BrokerAccountState(broker="mock", mode="broker_live", connected=True, can_trade=True, balance=100_000.0, equity=100_000.0, free_margin=90_000.0)
    snapshot = build_broker_health_snapshot(account, [], [], adjusted)

    readiness = assess_resume_readiness(snapshot, [], [], [], controls, adjusted)
    reasons = operator_control_block_reasons("broker_live", controls)

    assert readiness.status == ResumeReadinessStatus.BLOCKED_PENDING_MANUAL_REVIEW
    assert any("maintenance" in reason for reason in reasons)
    assert any("live submissions" in reason for reason in reasons)


def test_reliability_summary_and_prometheus_export(settings, tmp_path) -> None:
    adjusted = _broker_settings(settings)
    broker = MockBrokerExecutor(adjusted)
    order = broker.place_order(_request())
    account = broker.query_account_state()
    report, updated = reconcile_broker_state([order], broker.broker_order_snapshots(), broker.broker_position_snapshots())
    snapshot = build_broker_health_snapshot(account, updated, report.anomalies, adjusted, last_reconciliation_at=report.created_at)
    incidents = classify_broker_incidents(account, updated, report.anomalies, adjusted)
    metrics = build_operational_metrics(snapshot, incidents, report.anomalies, updated)
    alerts = generate_operational_alerts(snapshot, incidents, report.anomalies, updated, adjusted, [])

    summary = build_reliability_summary([snapshot], metrics, alerts, incidents, updated, report.anomalies)
    prom_path = write_prometheus_textfile(tmp_path / "forex.prom", snapshots=[snapshot], metrics=metrics, alerts=alerts, incidents=incidents, anomalies=report.anomalies, orders=updated)
    text = prom_path.read_text(encoding="utf-8")

    assert summary.broker_uptime_pct == 100.0
    assert prom_path.exists()
    assert 'forex_scanner_execution_mode{broker_adapter="mock",execution_mode="broker_sandbox"} 1' in text
    assert 'forex_scanner_broker_connected{broker_adapter="mock",execution_mode="broker_sandbox"} 1' in text
    assert 'forex_scanner_reconciliation_fresh{broker_adapter="mock",execution_mode="broker_sandbox"} 1' in text
    assert 'forex_scanner_live_submission_attempts_total{broker_adapter="mock",execution_mode="broker_sandbox"} 0' in text
    assert "symbol=" not in text


def test_prometheus_export_exposes_unavailable_broker_alerts_and_incidents(settings) -> None:
    adjusted = _broker_settings(settings)
    account = BrokerAccountState(broker="mt5", mode="broker_sandbox", connected=False, can_trade=False, last_error="terminal unavailable", consecutive_failures=2)
    snapshot = build_broker_health_snapshot(account, [], [], adjusted)
    incidents = classify_broker_incidents(account, [], [], adjusted)
    metrics = build_operational_metrics(snapshot, incidents, [], [])
    alerts = generate_operational_alerts(snapshot, incidents, [], [], adjusted, [])

    text = build_prometheus_text(snapshots=[snapshot], metrics=metrics, alerts=alerts, incidents=incidents, anomalies=[], orders=[])

    assert 'forex_scanner_broker_connected{broker_adapter="mt5",execution_mode="broker_sandbox"} 0' in text
    assert 'forex_scanner_broker_connectivity_failures_total{broker_adapter="mt5",execution_mode="broker_sandbox"} 1' in text
    assert 'forex_scanner_operational_alerts_active{broker_adapter="mt5",category="broker_down",execution_mode="broker_sandbox",severity="high"} 1' in text
    assert 'forex_scanner_operational_incidents_active{broker_adapter="mt5",category="mt5_terminal_not_reachable",execution_mode="broker_sandbox",severity="high"} 1' in text
    assert 'forex_scanner_metric_export_info{broker_adapter="mt5",execution_mode="broker_sandbox",state="ok"} 1' in text


def test_incident_merge_reuses_existing_id_and_updates_timestamp(settings) -> None:
    adjusted = _broker_settings(settings)
    unavailable = BrokerAccountState(broker="mt5", mode="broker_sandbox", connected=False, can_trade=False, last_error="terminal unavailable")
    previous = classify_broker_incidents(unavailable, [], [], adjusted)
    current = classify_broker_incidents(unavailable, [], [], adjusted)

    merged = merge_operational_incidents(previous, current)

    assert merged[0].incident_id == previous[0].incident_id
    assert merged[0].opened_at == previous[0].opened_at


def test_operational_incidents_block_broker_validation(settings) -> None:
    sandbox_settings = _broker_settings(settings)
    validator = BrokerPreTradeValidator(sandbox_settings)
    account = BrokerAccountState(broker="mock", mode="broker_sandbox", connected=True, can_trade=True, balance=100_000.0, equity=100_000.0, free_margin=90_000.0, is_demo=True)

    decision = validator.validate_request(
        _request(),
        [],
        account,
        context=BrokerValidationContext(account_state=account, severe_incidents=1, degraded_state_flags=["manual_intervention_required"]),
    )

    assert not decision.allowed
    assert any("operational incidents" in reason for reason in decision.reasons)
    assert any("manual intervention" in reason for reason in decision.reasons)


def test_live_capability_gate_blocks_adapter_when_disabled(settings) -> None:
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_live"
    adjusted.broker.live_enabled = True
    adjusted.execution_capabilities.broker_live_enabled = False

    with pytest.raises(BrokerExecutionError, match="broker_live capability"):
        build_execution_adapter(adjusted)


def test_broker_execution_service_journals_validation_failures(settings) -> None:
    sandbox_settings = _broker_settings(settings)
    broker = MockBrokerExecutor(sandbox_settings, connected=False, can_trade=False)
    service = BrokerExecutionService(sandbox_settings, adapter=broker)

    result = service.submit_approved([_opportunity()])

    assert result.submitted == []
    assert len(result.blocked) == 1
    blocked = result.blocked[0]
    assert blocked.broker_state == BrokerOrderState.VALIDATION_FAILED
    assert any(event.event_type == TradeEventType.BROKER_VALIDATION_FAILED for event in blocked.events)
    assert any(event.event_type == TradeEventType.LIVE_GUARDRAIL_TRIGGERED for event in blocked.events)


def test_broker_live_mode_requires_explicit_config(settings) -> None:
    payload = settings.model_dump(mode="json")
    payload["execution"]["mode"] = "broker_live"
    payload["broker"]["live_enabled"] = False

    with pytest.raises(ValueError, match="broker_live mode requires"):
        AppSettings.model_validate(payload)


def test_reconciliation_detects_missing_and_mismatched_broker_state(settings) -> None:
    broker = MockBrokerExecutor(_broker_settings(settings))
    order = broker.place_order(_request())
    snapshot = BrokerOrderSnapshot(
        broker_order_id=order.broker_order_id or "",
        symbol="EUR/USD",
        direction=DirectionBias.LONG,
        state=BrokerOrderState.ACKNOWLEDGED,
        quantity=0.01,
        filled_quantity=0.005,
        entry_price=1.1000,
        stop_loss=1.0960,
        take_profit=1.1100,
    )

    report, updated = reconcile_broker_state([order], [snapshot], [])
    old_order = order.model_copy(update={"created_at": datetime.now(timezone.utc) - timedelta(minutes=10)})
    missing_report, _ = reconcile_broker_state([old_order], [], [], delayed_update_grace_minutes=0.0)

    assert any(item.anomaly_type == ReconciliationAnomalyType.PARTIAL_FILL_DIFFERENCE for item in report.anomalies)
    assert any(item.anomaly_type == ReconciliationAnomalyType.STOP_TARGET_MISMATCH for item in report.anomalies)
    assert updated[0].reconciliation_status == "mismatch"
    assert missing_report.has_blocking_anomalies


def test_reconciliation_detects_duplicate_and_stale_broker_snapshots(settings) -> None:
    old_time = datetime.now(timezone.utc) - timedelta(hours=1)
    snapshots = [
        BrokerOrderSnapshot(
            broker_order_id="broker-1",
            symbol="EUR/USD",
            direction=DirectionBias.LONG,
            state=BrokerOrderState.ACKNOWLEDGED,
            updated_at=old_time,
        ),
        BrokerOrderSnapshot(
            broker_order_id="broker-2",
            symbol="EUR/USD",
            direction=DirectionBias.LONG,
            state=BrokerOrderState.ACKNOWLEDGED,
            updated_at=datetime.now(timezone.utc),
        ),
    ]

    report, _ = reconcile_broker_state([], snapshots, [], broker_snapshot_stale_minutes=10)

    assert any(item.anomaly_type == ReconciliationAnomalyType.DUPLICATE_ORDER_SUSPICION for item in report.anomalies)
    assert any(item.anomaly_type == ReconciliationAnomalyType.STALE_BROKER_SNAPSHOT for item in report.anomalies)


def test_broker_persistence_and_reports(settings, tmp_path) -> None:
    broker = MockBrokerExecutor(_broker_settings(settings))
    order = broker.place_order(_request())
    report, updated = reconcile_broker_state([order], [], [])
    database = Database(tmp_path / "broker.sqlite")

    database.save_broker_orders(updated)
    database.save_reconciliation_report(report)
    recovery = run_startup_recovery(_broker_settings(settings), broker, database.load_broker_orders())
    database.save_broker_health_snapshot(recovery.snapshot)
    database.save_broker_incidents(recovery.incidents)
    database.save_trade_events(recovery.events)
    metrics = build_operational_metrics(recovery.snapshot, recovery.incidents, recovery.reconciliation_report.anomalies, recovery.updated_orders)
    alerts = generate_operational_alerts(recovery.snapshot, recovery.incidents, recovery.reconciliation_report.anomalies, recovery.updated_orders, _broker_settings(settings), [])
    controls = database.load_operator_controls()
    readiness = assess_resume_readiness(recovery.snapshot, recovery.incidents, alerts, recovery.reconciliation_report.anomalies, controls, _broker_settings(settings))
    database.save_operational_metrics(metrics)
    database.save_operational_alerts(alerts)
    outputs = generate_broker_execution_report(
        database.load_broker_orders(),
        database.load_reconciliation_anomalies(),
        tmp_path / "broker_report",
        incidents=database.load_broker_incidents(),
        health_snapshots=database.load_broker_health_snapshots(),
        alerts=database.load_operational_alerts(),
        metrics=database.load_operational_metrics(),
        operator_controls=controls,
        resume_readiness=readiness,
    )

    loaded = database.load_broker_orders()
    events = database.load_trade_events(order.order_id)
    assert loaded[0].broker_order_id == order.broker_order_id
    assert events
    assert outputs["summary"].exists()
    assert outputs["broker_orders"].exists()
    assert outputs["reconciliation_anomalies"].exists()
    assert outputs["broker_health"].exists()
    assert outputs["order_lifecycle"].exists()
    assert outputs["manual_intervention"].exists()
    assert outputs["incident_report"].exists()
    assert outputs["restart_recovery"].exists()
    assert outputs["alert_summary"].exists()
    assert outputs["operational_metrics"].exists()
    assert outputs["broker_reliability"].exists()
    assert outputs["long_term_reliability"].exists()
    assert outputs["operator_controls"].exists()
    assert outputs["resume_readiness"].exists()
    assert "Broker Execution Report" in outputs["summary"].read_text(encoding="utf-8")


def test_startup_recovery_detects_unknown_broker_state_and_persists_operational_events(settings, tmp_path) -> None:
    sandbox_settings = _broker_settings(settings)
    broker = MockBrokerExecutor(sandbox_settings)
    broker.place_order(_request())
    database = Database(tmp_path / "recovery.sqlite")

    recovery = run_startup_recovery(sandbox_settings, broker, [])
    database.save_broker_orders(recovery.updated_orders)
    database.save_reconciliation_report(recovery.reconciliation_report)
    database.save_broker_health_snapshot(recovery.snapshot)
    database.save_broker_incidents(recovery.incidents)
    database.save_operational_metrics(build_operational_metrics(recovery.snapshot, recovery.incidents, recovery.reconciliation_report.anomalies, recovery.updated_orders))
    database.save_operational_alerts(generate_operational_alerts(recovery.snapshot, recovery.incidents, recovery.reconciliation_report.anomalies, recovery.updated_orders, sandbox_settings, []))
    database.save_trade_events(recovery.events)

    assert recovery.reconciliation_report.has_blocking_anomalies
    assert any(incident.category.value == "unknown_broker_state" for incident in recovery.incidents)
    assert database.load_broker_health_snapshots()
    assert database.load_broker_incidents()
    assert database.load_operational_metrics()
    assert database.load_operational_alerts()
    assert any(event.event_type == TradeEventType.BROKER_INCIDENT_OPENED for event in database.load_trade_events())


def test_recovery_closes_resolved_incidents_and_journals_close_event(settings) -> None:
    unavailable = BrokerAccountState(broker="mt5", mode="broker_sandbox", connected=False, can_trade=False, last_error="terminal unavailable")
    previous = classify_broker_incidents(unavailable, [], [], _broker_settings(settings))

    resolution = resolve_recovered_incidents(previous, [])

    assert resolution.closed_incidents
    assert resolution.closed_incidents[0].status.value == "closed"
    assert any(event.event_type == TradeEventType.BROKER_INCIDENT_CLOSED for event in resolution.events)


def test_mt5_unavailable_fails_safely(settings, monkeypatch) -> None:
    import app.execution.mt5 as mt5_module

    monkeypatch.setattr(mt5_module, "_load_mt5_module", lambda: None)
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_sandbox"
    executor = MT5BrokerExecutor(adjusted, mode="broker_sandbox")

    account = executor.query_account_state()

    assert not account.connected
    assert not account.can_trade
    assert account.error_category is not None
    assert "MetaTrader5 package" in (account.last_error or "")


def test_mt5_account_state_retry_is_bounded(settings, monkeypatch) -> None:
    import app.execution.mt5 as mt5_module

    class FakeMT5:
        def __init__(self) -> None:
            self.account_calls = 0

        def initialize(self, **kwargs: object) -> bool:
            return True

        def account_info(self) -> object | None:
            self.account_calls += 1
            if self.account_calls == 1:
                return None
            return SimpleNamespace(trade_allowed=True, server="Demo", balance=100_000.0, equity=100_000.0, margin_free=90_000.0, currency="USD", login=123)

        def positions_get(self) -> list[object]:
            return []

        def orders_get(self) -> list[object]:
            return []

    fake = FakeMT5()
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_sandbox"
    adjusted.broker_retry.max_attempts = 2
    adjusted.broker_retry.backoff_seconds = 0.0
    monkeypatch.setattr(mt5_module, "_load_mt5_module", lambda: fake)
    executor = MT5BrokerExecutor(adjusted, mode="broker_sandbox")

    account = executor.query_account_state()

    assert account.connected
    assert account.can_trade
    assert fake.account_calls == 2


def test_mt5_order_send_no_ack_requires_manual_intervention(settings, monkeypatch) -> None:
    import app.execution.mt5 as mt5_module

    class FakeMT5:
        TRADE_ACTION_PENDING = 5
        ORDER_TIME_GTC = 0
        ORDER_FILLING_RETURN = 0
        ORDER_TYPE_BUY_LIMIT = 2
        ORDER_TYPE_BUY_STOP = 4
        ORDER_TYPE_SELL_LIMIT = 3
        ORDER_TYPE_SELL_STOP = 5
        TRADE_RETCODE_DONE = 10009
        TRADE_RETCODE_PLACED = 10008

        def initialize(self, **kwargs: object) -> bool:
            return True

        def symbol_info_tick(self, symbol: str) -> object:
            return SimpleNamespace(ask=1.1010, bid=1.1008)

        def order_send(self, payload: dict[str, object]) -> object | None:
            return None

    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = "broker_sandbox"
    adjusted.broker_retry.retry_order_send_on_no_result = False
    monkeypatch.setattr(mt5_module, "_load_mt5_module", lambda: FakeMT5())
    executor = MT5BrokerExecutor(adjusted, mode="broker_sandbox")

    with pytest.raises(Exception, match="manual broker review"):
        executor.place_order(_request())

    order = executor.reconcile()[0]
    assert order.broker_state == BrokerOrderState.MANUAL_INTERVENTION_REQUIRED
    assert any(event.event_type == TradeEventType.MANUAL_INTERVENTION_REQUIRED for event in order.events)
