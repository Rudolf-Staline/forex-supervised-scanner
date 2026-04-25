"""Operator workflow tests for checklist, authorization, and session procedures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.execution.broker import MockBrokerExecutor
from app.execution.models import BrokerAccountState, OrderRequest
from app.execution.operator_workflows import (
    ChecklistItemKey,
    ChecklistStatus,
    TradingSessionRecord,
    HandoverStatus,
    LiveAuthorizationStatus,
    OperatorActionType,
    OperatorWorkflowContext,
    TradingSessionStatus,
    accept_handover,
    acknowledge_checklist,
    authorize_live,
    close_trading_session,
    create_handover,
    evaluate_inter_session_continuity,
    evaluate_pre_session_checklist,
    live_authorization_block_reasons,
    open_trading_session,
    record_operator_action,
    refuse_handover,
)
from app.execution.operations import (
    AlertCategory,
    AlertSeverity,
    AlertStatus,
    BrokerIncident,
    BrokerIncidentCategory,
    BrokerIncidentSeverity,
    BrokerIncidentStatus,
    OperationalAlert,
    OperatorControlState,
    build_broker_health_snapshot,
)
from app.execution.soak import SoakCampaignReadiness, SoakCampaignStatus, create_soak_campaign
from app.reporting.operator import generate_operator_workflow_report
from app.storage.database import Database


def _operator_settings(settings, mode: str = "broker_sandbox"):
    adjusted = settings.model_copy(deep=True)
    adjusted.execution.mode = mode
    adjusted.broker.provider = "mock" if mode != "broker_live" else "mt5"
    if mode == "broker_live":
        adjusted.execution_capabilities.broker_live_enabled = True
        adjusted.broker.live_enabled = True
    return adjusted


def _snapshot(settings, *, mode: str = "broker_sandbox", now: datetime | None = None):
    timestamp = now or datetime.now(timezone.utc)
    account = BrokerAccountState(
        broker=settings.broker.provider,
        mode=mode,
        connected=True,
        can_trade=True,
        balance=100_000.0,
        equity=100_000.0,
        free_margin=90_000.0,
        is_demo=(mode != "broker_live"),
        retrieved_at=timestamp,
    )
    snapshot = build_broker_health_snapshot(account, [], [], settings, now=timestamp, last_reconciliation_at=timestamp)
    return account, snapshot


def _campaign():
    campaign = create_soak_campaign("weekly", "broker_sandbox", "mock", 168.0 * 3600.0)
    return campaign.model_copy(update={"status": SoakCampaignStatus.FINALIZED, "readiness": SoakCampaignReadiness.SUPERVISED_READY})


def _order_request() -> OrderRequest:
    return OrderRequest(
        symbol="EUR/USD",
        style="day_trading",
        setup_family="trend_continuation",
        setup_subtype="shallow_ema20_pullback",
        direction="long",
        quantity_units=0.01,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        source_status="approved",
    )


def test_pre_session_checklist_evaluates_failures_and_warnings(settings) -> None:
    adjusted = _operator_settings(settings)
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, now=now)
    incident = BrokerIncident(
        incident_id="incident-1",
        opened_at=now,
        updated_at=now,
        category=BrokerIncidentCategory.REPEATED_REJECTS,
        severity=BrokerIncidentSeverity.HIGH,
        status=BrokerIncidentStatus.OPEN,
        reason="repeated rejects",
        recommendation="pause",
    )
    alert = OperationalAlert(
        alert_id="alert-1",
        category=AlertCategory.DEGRADED_DATA_QUALITY,
        severity=AlertSeverity.WARNING,
        status=AlertStatus.ACTIVE,
        opened_at=now,
        updated_at=now,
        reason="data quality warning",
        recommendation="review provider",
        dedupe_key="mock:warning",
    )
    context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[incident],
        alerts=[alert],
        anomalies=[],
        controls=OperatorControlState(updated_at=now, degraded_mode=True),
        broker_orders=[],
        latest_campaign=_campaign(),
    )

    checklist = evaluate_pre_session_checklist(adjusted, "alice", context, now=now)
    item_map = {item.item_key: item for item in checklist.items}

    assert checklist.status == ChecklistStatus.FAIL
    assert item_map[ChecklistItemKey.UNRESOLVED_INCIDENTS].status == ChecklistStatus.FAIL
    assert item_map[ChecklistItemKey.DEGRADED_MODE_STATE].status == ChecklistStatus.WARNING
    assert item_map[ChecklistItemKey.DATA_QUALITY_STATUS].status == ChecklistStatus.WARNING
    assert checklist.blockers
    assert checklist.warnings


def test_live_authorization_requires_clean_state_and_expires(settings) -> None:
    adjusted = _operator_settings(settings, mode="broker_live")
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, mode="broker_live", now=now)
    failing_context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[
            BrokerIncident(
                incident_id="incident-1",
                opened_at=now,
                updated_at=now,
                category=BrokerIncidentCategory.MANUAL_INTERVENTION_REQUIRED,
                severity=BrokerIncidentSeverity.CRITICAL,
                status=BrokerIncidentStatus.OPEN,
                reason="manual review needed",
                recommendation="block",
            )
        ],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now),
        broker_orders=[],
        latest_campaign=_campaign(),
    )
    failing_checklist = evaluate_pre_session_checklist(adjusted, "alice", failing_context, now=now)
    failing_checklist, _ = acknowledge_checklist(failing_checklist, "alice", now=now)

    denied, _ = authorize_live(adjusted, "alice", failing_checklist, failing_context, acknowledged=True, now=now)

    assert denied.status == LiveAuthorizationStatus.DENIED
    assert any("resume blocked" in reason or "checklist blocker" in reason for reason in denied.reasons)

    clean_context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now, live_submissions_enabled=True),
        broker_orders=[],
        latest_campaign=_campaign(),
    )
    clean_checklist = evaluate_pre_session_checklist(adjusted, "alice", clean_context, now=now)
    clean_checklist, _ = acknowledge_checklist(clean_checklist, "alice", now=now)
    granted, _ = authorize_live(adjusted, "alice", clean_checklist, clean_context, acknowledged=True, now=now)
    blockers = live_authorization_block_reasons(
        adjusted,
        clean_checklist,
        granted.model_copy(update={"expires_at": now - timedelta(minutes=1)}),
        clean_context,
        now=now,
    )

    assert granted.status == LiveAuthorizationStatus.GRANTED
    assert granted.expires_at is not None
    assert "latest live authorization has expired" in blockers


def test_session_open_blocks_without_confirmation_or_acknowledged_checklist(settings) -> None:
    adjusted = _operator_settings(settings)
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, now=now)
    context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now),
        broker_orders=[],
        latest_campaign=_campaign(),
    )
    checklist = evaluate_pre_session_checklist(adjusted, "alice", context, now=now)

    result = open_trading_session(adjusted, "alice", checklist, context, confirmed=False, now=now)

    assert result.session is None
    assert any("acknowledged" in reason for reason in result.blocked_reasons)
    assert any("confirmation" in reason for reason in result.blocked_reasons)


def test_live_authorization_blocks_mock_provider_for_live_mode(settings) -> None:
    adjusted = _operator_settings(settings, mode="broker_live")
    adjusted.broker.provider = "mock"
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, mode="broker_live", now=now)
    context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now),
        broker_orders=[],
        latest_campaign=_campaign(),
    )
    checklist = evaluate_pre_session_checklist(adjusted, "alice", context, now=now)
    checklist, _ = acknowledge_checklist(checklist, "alice", now=now)

    authorization, _ = authorize_live(adjusted, "alice", checklist, context, acknowledged=True, now=now)

    assert authorization.status == LiveAuthorizationStatus.DENIED
    assert any("mock broker provider" in reason for reason in authorization.reasons)


def test_session_lifecycle_persistence_and_report_generation(settings, tmp_path) -> None:
    adjusted = _operator_settings(settings)
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, now=now)
    broker = MockBrokerExecutor(adjusted)
    open_order = broker.place_order(_order_request())
    database = Database(tmp_path / "operator.sqlite")
    open_context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now),
        broker_orders=[],
        latest_campaign=_campaign(),
    )

    checklist = evaluate_pre_session_checklist(adjusted, "alice", open_context, now=now)
    checklist, checklist_action = acknowledge_checklist(checklist, "alice", now=now)
    open_result = open_trading_session(adjusted, "alice", checklist, open_context, confirmed=True, now=now)
    assert open_result.session is not None

    session = open_result.session
    close_context = open_context.model_copy(update={"broker_orders": [open_order]})
    close_result = close_trading_session(
        session,
        "alice",
        close_context,
        all_orders=[open_order],
        comment="end of session",
        now=now + timedelta(minutes=30),
    )
    manual_action = record_operator_action(
        "alice",
        OperatorActionType.MANUAL_INTERVENTION_COMPLETED,
        adjusted.execution.mode,
        reason="terminal review completed",
        now=now + timedelta(minutes=31),
    )
    handover, handover_actions = create_handover(
        adjusted,
        close_result.session,
        close_context,
        checklist=checklist,
        now=now + timedelta(minutes=30),
    )
    continuity = evaluate_inter_session_continuity(
        adjusted,
        close_context,
        [close_result.session],
        [handover],
        now=now + timedelta(minutes=31),
    )

    database.save_pre_session_checklist(checklist)
    database.save_trading_session(close_result.session)
    database.save_handover(handover)
    database.save_live_authorization(
        authorize_live(
            _operator_settings(settings, mode="broker_live"),
            "alice",
            acknowledge_checklist(
                evaluate_pre_session_checklist(
                    _operator_settings(settings, mode="broker_live"),
                    "alice",
                    OperatorWorkflowContext(
                        snapshot=_snapshot(_operator_settings(settings, mode="broker_live"), mode="broker_live", now=now)[1],
                        incidents=[],
                        alerts=[],
                        anomalies=[],
                        controls=OperatorControlState(updated_at=now, live_submissions_enabled=True),
                        broker_orders=[],
                        latest_campaign=_campaign(),
                    ),
                    now=now,
                ),
                "alice",
                now=now,
            )[0],
            OperatorWorkflowContext(
                snapshot=_snapshot(_operator_settings(settings, mode="broker_live"), mode="broker_live", now=now)[1],
                incidents=[],
                alerts=[],
                anomalies=[],
                controls=OperatorControlState(updated_at=now, live_submissions_enabled=True),
                broker_orders=[],
                latest_campaign=_campaign(),
            ),
            acknowledged=True,
            now=now,
        )[0]
    )
    database.save_operator_actions([checklist_action, *open_result.actions, *close_result.actions, *handover_actions, manual_action])

    outputs = generate_operator_workflow_report(
        database.load_pre_session_checklists(),
        database.load_live_authorizations(),
        database.load_trading_sessions(),
        database.load_operator_actions(),
        ["example blocker"],
        tmp_path / "operator_report",
        handovers=database.load_handovers(),
        continuity=continuity,
    )

    assert close_result.session.status == TradingSessionStatus.HANDOFF_REQUIRED
    assert database.load_latest_pre_session_checklist() is not None
    assert database.load_latest_handover() is not None
    assert database.load_open_trading_session() is None
    assert database.load_operator_actions()
    assert outputs["summary"].exists()
    assert outputs["latest_checklist"].exists()
    assert outputs["current_session"].exists()
    assert outputs["latest_authorization"].exists()
    assert outputs["latest_handover"].exists()
    assert outputs["continuity_summary"].exists()
    assert outputs["session_history"].exists()
    assert outputs["handover_history"].exists()
    assert outputs["pending_handovers"].exists()
    assert outputs["carry_over_items"].exists()
    assert outputs["open_risk_items"].exists()
    assert outputs["operator_actions"].exists()
    assert outputs["unresolved_handoffs"].exists()
    assert "Operator Workflow Summary" in outputs["summary"].read_text(encoding="utf-8")


def test_handover_acceptance_and_refusal_flow(settings) -> None:
    adjusted = _operator_settings(settings)
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, now=now)
    context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now),
        broker_orders=[],
        latest_campaign=_campaign(),
    )
    checklist = evaluate_pre_session_checklist(adjusted, "alice", context, now=now)
    checklist, _ = acknowledge_checklist(checklist, "alice", now=now)
    session = open_trading_session(adjusted, "alice", checklist, context, confirmed=True, now=now).session
    assert session is not None
    closed = close_trading_session(session, "alice", context, handoff_required=True, now=now + timedelta(minutes=5)).session
    handover, _ = create_handover(adjusted, closed, context, checklist=checklist, now=now + timedelta(minutes=5))

    blocked_accept = accept_handover(adjusted, handover, "bob", acknowledged=False, now=now + timedelta(minutes=6))
    accepted = accept_handover(adjusted, handover, "bob", acknowledged=True, now=now + timedelta(minutes=7))
    refused = refuse_handover(handover, "bob", refusal_reason="need fresh reconciliation", now=now + timedelta(minutes=8))

    assert blocked_accept.blocked_reasons
    assert accepted.handover is not None
    assert accepted.handover.status == HandoverStatus.ACCEPTED
    assert accepted.handover.accepted_at is not None
    assert refused.handover is not None
    assert refused.handover.status == HandoverStatus.REFUSED
    assert refused.handover.refusal_reason == "need fresh reconciliation"


def test_continuity_blocks_session_open_until_handover_is_accepted(settings) -> None:
    adjusted = _operator_settings(settings)
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, now=now)
    broker = MockBrokerExecutor(adjusted)
    open_order = broker.place_order(_order_request())
    open_context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now),
        broker_orders=[],
        latest_campaign=_campaign(),
    )
    checklist = evaluate_pre_session_checklist(adjusted, "alice", open_context, now=now)
    checklist, _ = acknowledge_checklist(checklist, "alice", now=now)
    session = open_trading_session(adjusted, "alice", checklist, open_context, confirmed=True, now=now).session
    assert session is not None
    close_context = open_context.model_copy(update={"broker_orders": [open_order]})
    closed = close_trading_session(session, "alice", close_context, all_orders=[open_order], now=now + timedelta(minutes=10)).session
    handover, _ = create_handover(adjusted, closed, close_context, checklist=checklist, now=now + timedelta(minutes=10))

    next_checklist = evaluate_pre_session_checklist(adjusted, "bob", close_context, now=now + timedelta(minutes=11))
    next_checklist, _ = acknowledge_checklist(next_checklist, "bob", now=now + timedelta(minutes=11))
    blocked = open_trading_session(
        adjusted,
        "bob",
        next_checklist,
        close_context,
        confirmed=True,
        sessions=[closed],
        handovers=[handover],
        now=now + timedelta(minutes=11),
    )
    accepted_handover = accept_handover(adjusted, handover, "bob", acknowledged=True, now=now + timedelta(minutes=12)).handover
    assert accepted_handover is not None
    _, refreshed_snapshot = _snapshot(adjusted, now=now + timedelta(minutes=13))
    refreshed_context = close_context.model_copy(update={"snapshot": refreshed_snapshot})
    refreshed_checklist = evaluate_pre_session_checklist(adjusted, "bob", refreshed_context, now=now + timedelta(minutes=13))
    refreshed_checklist, _ = acknowledge_checklist(refreshed_checklist, "bob", now=now + timedelta(minutes=13))
    opened = open_trading_session(
        adjusted,
        "bob",
        refreshed_checklist,
        refreshed_context,
        confirmed=True,
        sessions=[closed],
        handovers=[accepted_handover],
        now=now + timedelta(minutes=13),
    )

    assert blocked.session is None
    assert any("handover" in reason for reason in blocked.blocked_reasons)
    assert opened.session is not None


def test_live_authorization_requires_accepted_handover(settings) -> None:
    adjusted = _operator_settings(settings, mode="broker_live")
    now = datetime.now(timezone.utc)
    _, snapshot = _snapshot(adjusted, mode="broker_live", now=now)
    context = OperatorWorkflowContext(
        snapshot=snapshot,
        incidents=[],
        alerts=[],
        anomalies=[],
        controls=OperatorControlState(updated_at=now, live_submissions_enabled=True),
        broker_orders=[],
        latest_campaign=_campaign(),
    )
    checklist = evaluate_pre_session_checklist(adjusted, "alice", context, now=now)
    checklist, _ = acknowledge_checklist(checklist, "alice", now=now)
    prior_session = TradingSessionRecord(
        session_id="session-prev",
        opened_at=now - timedelta(hours=1),
        closed_at=now - timedelta(minutes=30),
        operator="alice",
        mode=adjusted.execution.mode,
        broker=adjusted.broker.provider,
        status=TradingSessionStatus.HANDOFF_REQUIRED,
        linked_checklist_id=checklist.checklist_id,
        handoff_required=True,
        unresolved_items=["incident:manual"],
    )
    handover, _ = create_handover(adjusted, prior_session, context, checklist=checklist, now=now - timedelta(minutes=30))

    authorization, _ = authorize_live(
        adjusted,
        "bob",
        checklist,
        context,
        acknowledged=True,
        sessions=[prior_session],
        handovers=[handover],
        now=now,
    )

    assert authorization.status == LiveAuthorizationStatus.DENIED
    assert any("handover" in reason for reason in authorization.reasons)
