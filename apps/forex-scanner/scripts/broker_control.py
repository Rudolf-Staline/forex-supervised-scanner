"""Operator control surface for supervised broker operations."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.execution.operator_identity import ApprovalAction, AuthenticatedOperatorContext, PermissionAction, approval_role_allowed, auth_reauth_required, build_approval_signature, require_authenticated_context, reauthenticate_operator_session
from app.execution.operations import OperatorControlState, assess_resume_readiness
from app.execution.operator_workflows import OperatorActionResult, OperatorActionType, record_operator_action
from app.storage.database import Database


def main() -> None:
    """Read or update persisted operator controls."""

    settings = load_settings()
    parser = argparse.ArgumentParser(description="Inspect or update supervised broker operator controls.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    parser.add_argument("--operator-id", "--operator", dest="operator_id", default=os.getenv("USERNAME", "operator"))
    parser.add_argument("--auth-session-id", default=None)
    parser.add_argument("--reauth-secret", default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--maintenance", choices=["on", "off"], default=None)
    parser.add_argument("--degraded", choices=["on", "off"], default=None)
    parser.add_argument("--broker-submissions", choices=["on", "off"], default=None)
    parser.add_argument("--live-submissions", choices=["on", "off"], default=None)
    parser.add_argument("--ack-incident", action="append", default=[])
    parser.add_argument("--clear-acks", action="store_true")
    args = parser.parse_args()

    database = Database(Path(args.db))
    database.sync_operator_identities(settings)
    controls = database.load_operator_controls()
    changing_controls = any(
        value is not None
        for value in (args.maintenance, args.degraded, args.broker_submissions, args.live_submissions)
    ) or bool(args.ack_incident) or args.clear_acks
    audit_actions = []
    if changing_controls:
        auth_context, decision = require_authenticated_context(
            database.load_operator_identities(),
            database.load_operator_auth_sessions(),
            operator_id=args.operator_id,
            action=PermissionAction.TOGGLE_OPERATOR_CONTROLS,
            auth_session_id=args.auth_session_id,
        )
        if auth_context is None:
            denial = record_operator_action(
                operator=args.operator_id,
                action_type=OperatorActionType.OPERATOR_CONTROL_UPDATED,
                mode=settings.execution.mode,
                result=OperatorActionResult.DENIED,
                target_type="operator_controls",
                target_id="default",
                reason="; ".join(decision.reasons),
            )
            database.save_operator_actions([denial])
            raise SystemExit("; ".join(decision.reasons))
        approval = None
        if args.live_submissions is not None or args.broker_submissions is not None:
            blockers: list[str] = []
            if not approval_role_allowed(auth_context.identity.role, ApprovalAction.ENABLE_SENSITIVE_EXECUTION):
                blockers.append(f"role {auth_context.identity.role.value} cannot sign {ApprovalAction.ENABLE_SENSITIVE_EXECUTION.value}")
            if not (args.reason or "").strip():
                blockers.append("a reason is required when changing submission capability controls")
            if blockers:
                denial = record_operator_action(
                    operator=auth_context.identity.display_name,
                    action_type=OperatorActionType.OPERATOR_CONTROL_UPDATED,
                    mode=settings.execution.mode,
                    result=OperatorActionResult.DENIED,
                    target_type="operator_controls",
                    target_id="default",
                    reason="; ".join(blockers),
                    auth_context=auth_context,
                )
                database.save_operator_actions([denial])
                raise SystemExit("; ".join(blockers))
            reauth_actions = {ApprovalAction(value) for value in settings.operator_auth.reauth_required_actions}
            if auth_reauth_required(
                auth_context.auth_session,
                action=ApprovalAction.ENABLE_SENSITIVE_EXECUTION,
                reauth_actions=reauth_actions,
                reauth_window_minutes=settings.operator_auth.reauth_window_minutes,
            ):
                if not args.reauth_secret:
                    denial = record_operator_action(
                        operator=auth_context.identity.display_name,
                        action_type=OperatorActionType.REAUTH_REQUIRED,
                        mode=settings.execution.mode,
                        result=OperatorActionResult.DENIED,
                        target_type="operator_controls",
                        target_id="default",
                        reason="explicit re-authentication is required for sensitive execution control changes",
                        auth_context=auth_context,
                    )
                    database.save_operator_actions([denial])
                    raise SystemExit("explicit re-authentication is required for sensitive execution control changes")
                refreshed = reauthenticate_operator_session(
                    auth_context.identity,
                    auth_context.auth_session,
                    args.reauth_secret,
                    session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
                )
                database.save_operator_auth_session(refreshed)
                auth_context = AuthenticatedOperatorContext(identity=auth_context.identity, auth_session=refreshed)
                audit_actions.append(
                    record_operator_action(
                        operator=auth_context.identity.display_name,
                        action_type=OperatorActionType.REAUTH_COMPLETED,
                        mode=settings.execution.mode,
                        target_type="operator_controls",
                        target_id="default",
                        reason="operator re-authenticated before changing submission capability controls",
                        auth_context=auth_context,
                    )
                )
            approval = build_approval_signature(
                auth_context,
                ApprovalAction.ENABLE_SENSITIVE_EXECUTION,
                target_type="operator_controls",
                target_id="default",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.operator_auth.approval_expiry_minutes),
                reason=args.reason,
                comment=args.reason,
            )
            database.save_approval_signature(approval)
        audit_actions.append(
            record_operator_action(
                operator=auth_context.identity.display_name,
                action_type=OperatorActionType.OPERATOR_CONTROL_UPDATED,
                mode=settings.execution.mode,
                target_type="operator_controls",
                target_id="default",
                reason=args.reason,
                auth_context=auth_context,
                approval_signature_id=approval.approval_id if approval is not None else None,
                payload={
                    "maintenance": args.maintenance,
                    "degraded": args.degraded,
                    "broker_submissions": args.broker_submissions,
                    "live_submissions": args.live_submissions,
                    "ack_incidents": len(args.ack_incident),
                    "clear_acks": args.clear_acks,
                },
            )
        )
    updates: dict[str, object] = {
        "updated_at": datetime.now(timezone.utc),
        "updated_by": args.operator_id,
        "reason": args.reason or controls.reason,
    }
    if args.maintenance is not None:
        updates["maintenance_mode"] = args.maintenance == "on"
    if args.degraded is not None:
        updates["degraded_mode"] = args.degraded == "on"
    if args.broker_submissions is not None:
        updates["broker_submissions_enabled"] = args.broker_submissions == "on"
    if args.live_submissions is not None:
        updates["live_submissions_enabled"] = args.live_submissions == "on"
    acknowledgements = [] if args.clear_acks else list(controls.acknowledged_incident_ids)
    acknowledgements.extend(args.ack_incident)
    updates["acknowledged_incident_ids"] = sorted(set(acknowledgements))
    updated = controls.model_copy(update=updates)
    database.save_operator_controls(updated)
    if audit_actions:
        database.save_operator_actions(audit_actions)

    snapshots = database.load_broker_health_snapshots()
    latest = snapshots[-1] if snapshots else None
    if latest is None:
        readiness = None
    else:
        readiness = assess_resume_readiness(
            latest,
            database.load_broker_incidents(),
            database.load_operational_alerts(),
            database.load_reconciliation_anomalies(),
            updated,
            settings,
        )
    print(
        "broker_control=ok "
        f"maintenance={updated.maintenance_mode} degraded={updated.degraded_mode} "
        f"broker_submissions={updated.broker_submissions_enabled} live_submissions={updated.live_submissions_enabled} "
        f"acknowledged_incidents={len(updated.acknowledged_incident_ids)}"
    )
    if readiness is not None:
        print(f"resume_readiness={readiness.status.value} reasons={'; '.join(readiness.reasons) or 'none'}")


if __name__ == "__main__":
    main()
