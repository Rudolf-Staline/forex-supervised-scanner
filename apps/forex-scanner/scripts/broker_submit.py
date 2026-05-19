"""Guarded broker sandbox/live submission path.

Default behavior is dry-run validation only. Real broker submission requires
`--submit`, and live mode additionally requires `--allow-live` plus config/env
gates enforced by the broker validator.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.safety import DemoSafetyError, ensure_demo_safe_mode
from app.config.settings import load_settings
from app.backup.recovery import load_recovery_state
from app.core.pipeline import ScannerService
from app.core.types import OpportunityStatus, TradingStyle
from app.data.providers import build_provider
from app.execution.broker import build_execution_adapter
from app.execution.broker_service import BrokerExecutionService
from app.execution.broker_validation import BrokerPreTradeValidator, BrokerValidationContext
from app.execution.models import BrokerAccountState, BrokerOrderState
from app.execution.operator_identity import PermissionAction, require_authenticated_context
from app.execution.operator_workflows import OperatorWorkflowContext, latest_campaign_with_readiness, live_authorization_block_reasons
from app.execution.operator_workflows import OperatorActionResult, OperatorActionType, record_operator_action
from app.execution.operations import assess_resume_readiness, build_operational_metrics, generate_operational_alerts, merge_operational_incidents, operator_control_block_reasons, resolve_operational_alerts, resolve_recovered_incidents, run_startup_recovery
from app.storage.database import Database
from app.utils.logging import configure_logging


def main() -> None:
    """Scan opportunities and optionally submit broker orders after validation."""

    parser = argparse.ArgumentParser(description="Dry-run or submit approved/premium opportunities to a guarded broker adapter.")
    parser.add_argument("--mode", choices=["broker_sandbox", "broker_live"], default="broker_sandbox")
    parser.add_argument("--provider", choices=["mt5", "mock"], default=None)
    parser.add_argument("--style", default="day_trading", choices=[style.value for style in TradingStyle])
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--submit", action="store_true", help="Actually call the broker adapter place_order path.")
    parser.add_argument("--allow-live", action="store_true", help="Required in addition to config/env gates for broker_live.")
    parser.add_argument("--operator-id", "--operator", dest="operator_id", default=os.getenv("USERNAME", "operator"))
    parser.add_argument("--auth-session-id", default=None)
    args = parser.parse_args()

    configure_logging()
    settings = load_settings().model_copy(deep=True)
    settings.execution.mode = args.mode
    if args.provider:
        settings.broker.provider = args.provider
    try:
        ensure_demo_safe_mode(settings, context="broker_submit.py")
    except DemoSafetyError as exc:
        raise SystemExit(str(exc))
    if args.mode == "broker_live" and not args.allow_live:
        raise SystemExit("broker_live submission requires --allow-live plus config/env confirmation")
    if args.mode == "broker_live" and settings.broker.provider == "mock":
        raise SystemExit("mock provider is not allowed for broker_live")
    if args.mode == "broker_live" and not settings.execution_capabilities.broker_live_enabled:
        raise SystemExit("broker_live submission requires execution_capabilities.broker_live_enabled=true")
    if args.mode == "broker_live" and not settings.broker.live_enabled:
        raise SystemExit("broker_live submission requires broker.live_enabled=true")
    if args.mode == "broker_live" and os.getenv(settings.broker.live_confirmation_env) != settings.broker.live_confirmation_value:
        raise SystemExit(f"broker_live submission requires {settings.broker.live_confirmation_env}")

    database = Database(settings.database_absolute_path)
    database.sync_operator_identities(settings)
    provider = build_provider(settings)
    adapter = build_execution_adapter(settings)
    recovery = run_startup_recovery(settings, adapter, database.load_broker_orders())
    previous_incidents = database.load_broker_incidents()
    previous_alerts = database.load_operational_alerts()
    incidents = merge_operational_incidents(previous_incidents, recovery.incidents)
    resolution = resolve_recovered_incidents(previous_incidents, incidents)
    metrics = build_operational_metrics(recovery.snapshot, incidents, recovery.reconciliation_report.anomalies, recovery.updated_orders)
    alerts = generate_operational_alerts(recovery.snapshot, incidents, recovery.reconciliation_report.anomalies, recovery.updated_orders, settings, previous_alerts)
    resolved_alerts = resolve_operational_alerts(previous_alerts, alerts)
    database.save_broker_orders(recovery.updated_orders)
    database.save_reconciliation_report(recovery.reconciliation_report)
    database.save_broker_health_snapshot(recovery.snapshot)
    database.save_broker_incidents([*incidents, *resolution.closed_incidents])
    database.save_operational_metrics(metrics)
    database.save_operational_alerts([*alerts, *resolved_alerts])
    database.save_trade_events([*recovery.events, *resolution.events])
    account = recovery.account_state
    symbols = args.symbols or settings.symbols
    style = TradingStyle(args.style)
    report = ScannerService(settings, provider, database).scan(style, symbols)
    executable = [opportunity for opportunity in report.opportunities if opportunity.status in {OpportunityStatus.APPROVED, OpportunityStatus.PREMIUM}]

    print(
        "broker_submit=preflight "
        f"mode={settings.execution.mode} provider={settings.broker.provider} submit={args.submit} "
        f"connected={account.connected} can_trade={account.can_trade} executable={len(executable)}"
    )
    if not args.submit:
        validator = BrokerPreTradeValidator(settings)
        context = _context_from_database(database, account, settings)
        for opportunity in executable:
            decision = validator.validate_opportunity(opportunity, adapter.sync_positions(), [], account, context=context)
            print(f"dry_run {opportunity.symbol}:{opportunity.setup_subtype.value}:{opportunity.status.value} allowed={decision.allowed} reasons={'; '.join(decision.reasons)}")
        return

    permission_action = PermissionAction.SUBMIT_BROKER_LIVE if args.mode == "broker_live" else PermissionAction.SUBMIT_BROKER_SANDBOX
    auth_context, decision = require_authenticated_context(
        database.load_operator_identities(),
        database.load_operator_auth_sessions(),
        operator_id=args.operator_id,
        action=permission_action,
        auth_session_id=args.auth_session_id,
    )
    if auth_context is None:
        denial = record_operator_action(
            operator=args.operator_id,
            action_type=OperatorActionType.LIVE_AUTHORIZATION_DENIED,
            mode=settings.execution.mode,
            result=OperatorActionResult.DENIED,
            target_type="broker_submit",
            reason="; ".join(decision.reasons),
        )
        database.save_operator_actions([denial])
        raise SystemExit("; ".join(decision.reasons))

    result = BrokerExecutionService(settings, adapter=adapter).submit_approved(executable, context=_context_from_database(database, account, settings))
    database.save_broker_orders([*result.submitted, *result.blocked])
    database.rebuild_trading_journal()
    print(f"broker_submit=ok submitted={len(result.submitted)} blocked={len(result.blocked)}")
    for order in result.submitted:
        print(f"submitted id={order.order_id} broker_order_id={order.broker_order_id} symbol={order.request.symbol} state={order.broker_state.value if order.broker_state else 'unknown'}")
    for order in result.blocked:
        print(f"blocked id={order.order_id} symbol={order.request.symbol} reason={order.rejection_reason}")

def _context_from_database(database: Database, account: BrokerAccountState, settings) -> BrokerValidationContext:
    orders = database.load_broker_orders()
    anomalies = database.load_reconciliation_anomalies()
    incidents = database.load_broker_incidents()
    snapshots = database.load_broker_health_snapshots()
    latest_snapshot = snapshots[-1] if snapshots else None
    controls = database.load_operator_controls()
    latest_checklist = database.load_latest_pre_session_checklist()
    latest_authorization = database.load_latest_live_authorization()
    sessions = database.load_trading_sessions()
    handovers = database.load_handovers()
    workflow_context = OperatorWorkflowContext(
        snapshot=latest_snapshot,
        account_state=account,
        incidents=incidents,
        alerts=database.load_operational_alerts(),
        anomalies=anomalies,
        controls=controls,
        broker_orders=orders,
        latest_campaign=latest_campaign_with_readiness(database.load_soak_campaigns()),
        latest_audit_verification=database.load_latest_audit_verification(),
        latest_recovery_validation=load_recovery_state(settings),
    )
    readiness_reasons: list[str] = []
    if latest_snapshot is not None:
        readiness = assess_resume_readiness(latest_snapshot, incidents, workflow_context.alerts, anomalies, controls, settings)
        if readiness.status.value == "blocked_pending_manual_review":
            readiness_reasons.extend(f"resume blocked: {reason}" for reason in readiness.reasons)
    readiness_reasons.extend(
        live_authorization_block_reasons(
            settings,
            latest_checklist,
            latest_authorization,
            workflow_context,
            sessions=sessions,
            handovers=handovers,
        )
    )
    today = datetime.now(timezone.utc).date()
    daily_submitted = sum(
        1
        for order in orders
        for transition in order.broker_transitions
        if transition.state == BrokerOrderState.SUBMITTED and transition.occurred_at.date() == today
    )
    repeated_rejects = 0
    for order in sorted(orders, key=lambda item: item.created_at, reverse=True):
        if any(transition.state == BrokerOrderState.REJECTED for transition in order.broker_transitions):
            repeated_rejects += 1
            continue
        break
    return BrokerValidationContext(
        account_state=account,
        daily_submitted_trades=daily_submitted,
        repeated_rejects=repeated_rejects,
        reconciliation_anomalies=sum(1 for anomaly in anomalies if anomaly.severity in {"high", "critical"}),
        connectivity_failures=account.consecutive_failures,
        open_incidents=sum(1 for incident in incidents if incident.status.value == "open"),
        severe_incidents=sum(1 for incident in incidents if incident.blocks_execution),
        degraded_state_flags=latest_snapshot.degraded_flags if latest_snapshot else [],
        operator_control_reasons=[*operator_control_block_reasons(account.mode, controls), *readiness_reasons],
    )


if __name__ == "__main__":
    main()
