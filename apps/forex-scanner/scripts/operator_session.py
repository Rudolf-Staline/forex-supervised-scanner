"""Operator checklist, authorization, and supervised session procedures."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.audit.integrity import AuditSealTrigger
from app.backup.recovery import load_recovery_state
from app.execution.broker import build_execution_adapter
from app.execution.operator_identity import (
    ApprovalAction,
    ApprovalSignature,
    AuthenticatedOperatorContext,
    PermissionAction,
    approval_role_allowed,
    auth_reauth_required,
    authenticate_operator,
    build_approval_signature,
    require_authenticated_context,
    reauthenticate_operator_session,
    resolve_auth_session,
    resolve_identity,
    sign_out_operator_session,
)
from app.execution.operator_workflows import (
    OperatorActionResult,
    OperatorActionType,
    OperatorWorkflowContext,
    accept_handover,
    acknowledge_checklist,
    authorize_live,
    close_trading_session,
    create_handover,
    evaluate_inter_session_continuity,
    evaluate_pre_session_checklist,
    latest_campaign_with_readiness,
    live_authorization_block_reasons,
    open_trading_session,
    record_operator_action,
    refuse_handover,
)
from app.execution.operations import (
    build_operational_metrics,
    generate_operational_alerts,
    merge_operational_incidents,
    resolve_operational_alerts,
    resolve_recovered_incidents,
    run_startup_recovery,
)
from app.reporting.operator import generate_operator_workflow_report
from app.storage.database import Database
from app.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)


def main() -> None:
    """Run operator-grade checklist, authorization, handover, and session procedures."""

    parser = argparse.ArgumentParser(description="Operator checklist, pre-live authorization, handover, and supervised session workflow.")
    parser.add_argument("--db", default=None, help="SQLite database path.")
    parser.add_argument("--mode", choices=["paper", "broker_sandbox", "broker_live"], default=None)
    parser.add_argument("--provider", choices=["mt5", "mock"], default=None)
    parser.add_argument("--operator-id", "--operator", dest="operator_id", default=os.getenv("USERNAME", "operator"))
    parser.add_argument("--auth-session-id", default=None, help="Authenticated operator session id for sensitive actions.")
    parser.add_argument("--out", default="reports/operator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    signin_parser = subparsers.add_parser("sign-in", help="Authenticate a local operator identity and create an auth session.")
    signin_parser.add_argument("--secret", required=True, help="Operator passphrase for lightweight local authentication.")
    signin_parser.add_argument("--out", default=None)

    signout_parser = subparsers.add_parser("sign-out", help="Sign out the current local operator auth session.")
    signout_parser.add_argument("--out", default=None)

    checklist_parser = subparsers.add_parser("checklist", help="Run the structured pre-session checklist.")
    checklist_parser.add_argument("--acknowledge", action="store_true", help="Record checklist acknowledgement.")
    checklist_parser.add_argument("--out", default=None)

    authorize_parser = subparsers.add_parser("authorize-live", help="Attempt a manual broker-live authorization.")
    authorize_parser.add_argument("--acknowledge-checklist", action="store_true", help="Acknowledge the generated checklist before authorization.")
    authorize_parser.add_argument("--confirm", action="store_true", help="Required manual confirmation flag for granting authorization.")
    authorize_parser.add_argument("--secondary-operator", default=None, help="Secondary operator for optional dual confirmation.")
    authorize_parser.add_argument("--comment", default=None)
    authorize_parser.add_argument("--reauth-secret", default=None, help="Explicit re-auth secret for highly sensitive approval paths.")
    authorize_parser.add_argument("--out", default=None)

    open_parser = subparsers.add_parser("open", help="Open a supervised session after checklist review.")
    open_parser.add_argument("--acknowledge-checklist", action="store_true", help="Acknowledge the generated checklist before opening the session.")
    open_parser.add_argument("--confirm", action="store_true", help="Required manual confirmation flag for opening the session.")
    open_parser.add_argument("--comment", default=None)
    open_parser.add_argument("--out", default=None)

    close_parser = subparsers.add_parser("close", help="Close the current supervised session.")
    close_parser.add_argument("--handoff-required", action="store_true", help="Force handoff-required status on close.")
    close_parser.add_argument("--comment", default=None)
    close_parser.add_argument("--out", default=None)

    handover_create_parser = subparsers.add_parser("handover-create", help="Create a structured handover package.")
    handover_create_parser.add_argument("--session-id", default=None, help="Source session id; defaults to the open or latest session.")
    handover_create_parser.add_argument("--target-session-id", default=None)
    handover_create_parser.add_argument("--target-operator", default=None)
    handover_create_parser.add_argument("--summary", default=None)
    handover_create_parser.add_argument("--comment", default=None)
    handover_create_parser.add_argument("--out", default=None)

    handover_accept_parser = subparsers.add_parser("handover-accept", help="Accept a pending handover after review.")
    handover_accept_parser.add_argument("--handover-id", default=None, help="Handover id; defaults to the latest handover.")
    handover_accept_parser.add_argument("--acknowledge", action="store_true", help="Required acknowledgement flag before acceptance.")
    handover_accept_parser.add_argument("--target-session-id", default=None)
    handover_accept_parser.add_argument("--comment", default=None)
    handover_accept_parser.add_argument("--reauth-secret", default=None, help="Explicit re-auth secret when accepting severe carry-over risk.")
    handover_accept_parser.add_argument("--out", default=None)

    handover_refuse_parser = subparsers.add_parser("handover-refuse", help="Refuse a handover and record the refusal reason.")
    handover_refuse_parser.add_argument("--handover-id", default=None, help="Handover id; defaults to the latest handover.")
    handover_refuse_parser.add_argument("--reason", required=True)
    handover_refuse_parser.add_argument("--comment", default=None)
    handover_refuse_parser.add_argument("--out", default=None)

    status_parser = subparsers.add_parser("status", help="Show the latest checklist/session/authorization state.")
    status_parser.add_argument("--out", default=None)

    record_parser = subparsers.add_parser("record-action", help="Record a manual intervention completion or incident-resume approval.")
    record_parser.add_argument(
        "--action",
        required=True,
        choices=[
            OperatorActionType.MANUAL_INTERVENTION_COMPLETED.value,
            OperatorActionType.RESUME_AFTER_INCIDENT_APPROVED.value,
        ],
    )
    record_parser.add_argument("--reason", default=None)
    record_parser.add_argument("--reauth-secret", default=None, help="Explicit re-auth secret for sensitive resume approvals.")
    record_parser.add_argument("--out", default=None)

    args = parser.parse_args()
    configure_logging()
    settings = load_settings().model_copy(deep=True)
    if args.mode:
        settings.execution.mode = args.mode
    if args.provider:
        settings.broker.provider = args.provider
    database = Database(Path(args.db) if args.db else settings.database_absolute_path)
    identities = database.sync_operator_identities(settings)
    output_dir = Path(args.out or "reports/operator")

    if args.command == "sign-in":
        identity = resolve_identity(identities, args.operator_id)
        if identity is None:
            action = record_operator_action(
                operator=args.operator_id,
                action_type=OperatorActionType.AUTHENTICATION_FAILED,
                mode=settings.execution.mode,
                result=OperatorActionResult.DENIED,
                reason=f"unknown operator identity {args.operator_id}",
            )
            database.save_operator_actions([action])
            _emit_report(database, settings, output_dir)
            raise SystemExit(f"unknown operator identity {args.operator_id}")
        try:
            auth_session = authenticate_operator(
                identity,
                args.secret,
                session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
            )
        except ValueError as exc:
            action = record_operator_action(
                operator=args.operator_id,
                action_type=OperatorActionType.AUTHENTICATION_FAILED,
                mode=settings.execution.mode,
                result=OperatorActionResult.DENIED,
                reason=str(exc),
            )
            database.save_operator_actions([action])
            _emit_report(database, settings, output_dir)
            raise SystemExit(str(exc))
        database.save_operator_auth_session(auth_session)
        success = record_operator_action(
            operator=identity.display_name,
            action_type=OperatorActionType.AUTHENTICATION_SUCCEEDED,
            mode=settings.execution.mode,
            target_type="auth_session",
            target_id=auth_session.auth_session_id,
            reason="operator authenticated via local passphrase",
            auth_context=AuthenticatedOperatorContext(identity=identity, auth_session=auth_session),
        )
        database.save_operator_actions([success])
        _emit_report(database, settings, output_dir)
        print(
            "operator_session=sign_in "
            f"operator_id={identity.operator_id} role={identity.role.value} "
            f"auth_session_id={auth_session.auth_session_id} expires_at={auth_session.expires_at.isoformat()}"
        )
        return

    if args.command == "sign-out":
        auth_session = resolve_auth_session(
            database.load_operator_auth_sessions(),
            operator_id=args.operator_id,
            auth_session_id=args.auth_session_id,
        )
        if auth_session is None:
            action = record_operator_action(
                operator=args.operator_id,
                action_type=OperatorActionType.AUTHENTICATION_SIGNED_OUT,
                mode=settings.execution.mode,
                result=OperatorActionResult.DENIED,
                reason=f"no authenticated operator session exists for {args.operator_id}",
            )
            database.save_operator_actions([action])
            _emit_report(database, settings, output_dir)
            raise SystemExit(f"no authenticated operator session exists for {args.operator_id}")
        signed_out = sign_out_operator_session(auth_session)
        database.save_operator_auth_session(signed_out)
        identity = resolve_identity(identities, signed_out.operator_id)
        auth_context = AuthenticatedOperatorContext(identity=identity, auth_session=signed_out) if identity is not None else None
        action = record_operator_action(
            operator=signed_out.display_name,
            action_type=OperatorActionType.AUTHENTICATION_SIGNED_OUT,
            mode=settings.execution.mode,
            target_type="auth_session",
            target_id=signed_out.auth_session_id,
            reason="operator signed out of the local authenticated session",
            auth_context=auth_context,
        )
        database.save_operator_actions([action])
        _emit_report(database, settings, output_dir)
        print(
            "operator_session=sign_out "
            f"operator_id={signed_out.operator_id} auth_session_id={signed_out.auth_session_id}"
        )
        return

    if args.command == "status":
        _emit_report(database, settings, output_dir)
        latest_session = database.load_open_trading_session()
        latest_checklist = database.load_latest_pre_session_checklist()
        latest_auth = database.load_latest_live_authorization()
        latest_auth_session = database.load_latest_operator_auth_session(args.operator_id)
        sessions = database.load_trading_sessions()
        handovers = database.load_handovers()
        context = _load_context(database)
        continuity = evaluate_inter_session_continuity(
            settings,
            context,
            sessions,
            handovers,
            live_authorization=latest_auth,
        )
        blockers = live_authorization_block_reasons(
            settings,
            latest_checklist,
            latest_auth,
            context,
            sessions=sessions,
            handovers=handovers,
        )
        print(
            "operator_session=status "
            f"open_session={latest_session.session_id if latest_session else 'none'} "
            f"latest_handover={handovers[-1].handover_id if handovers else 'none'} "
            f"auth_session={latest_auth_session.auth_session_id if latest_auth_session else 'none'} "
            f"checklist={latest_checklist.status.value if latest_checklist else 'none'} "
            f"live_authorization={latest_auth.status.value if latest_auth else 'none'} "
            f"live_blockers={len(blockers)} continuity_blockers={len(continuity.blockers)}"
        )
        return

    if args.command == "record-action":
        permission = (
            PermissionAction.RESUME_AFTER_INCIDENT
            if args.action == OperatorActionType.RESUME_AFTER_INCIDENT_APPROVED.value
            else PermissionAction.RECORD_MANUAL_INTERVENTION
        )
        auth_context, denied_actions, blocked_reasons = _require_action_auth(
            database,
            settings,
            operator_id=args.operator_id,
            permission_action=permission,
            action_type=OperatorActionType(args.action),
            mode=settings.execution.mode,
            auth_session_id=args.auth_session_id,
            target_type="operator_action",
        )
        if auth_context is None:
            database.save_operator_actions(denied_actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        approval: ApprovalSignature | None = None
        actions = list(denied_actions)
        if args.action == OperatorActionType.RESUME_AFTER_INCIDENT_APPROVED.value:
            auth_context, actions, approval, blocked_reasons = _ensure_sensitive_approval(
                database,
                settings,
                auth_context,
                approval_action=ApprovalAction.RESUME_AFTER_MAJOR_INCIDENT,
                action_type=OperatorActionType.RESUME_AFTER_INCIDENT_APPROVED,
                mode=settings.execution.mode,
                comment=args.reason,
                reauth_secret=args.reauth_secret,
                target_type="incident_resume",
            )
            if auth_context is None:
                database.save_operator_actions(actions)
                _emit_report(database, settings, output_dir)
                raise SystemExit("; ".join(blocked_reasons))
        action = record_operator_action(
            operator=_operator_name(auth_context, args.operator_id),
            action_type=OperatorActionType(args.action),
            mode=settings.execution.mode,
            reason=args.reason,
            auth_context=auth_context,
            approval_signature_id=approval.approval_id if approval else None,
        )
        actions.append(action)
        if approval is not None:
            database.save_approval_signature(approval.model_copy(update={"target_id": action.action_id}))
        database.save_operator_actions(actions)
        _emit_report(database, settings, output_dir)
        print(f"operator_session=action action={action.action_type.value} result={action.result.value}")
        return

    context = _load_context(database, settings=settings, refresh=(args.command in {"checklist", "authorize-live", "open", "close", "handover-create"}))
    latest_authorization = database.load_latest_live_authorization()
    sessions = database.load_trading_sessions()
    handovers = database.load_handovers()

    if args.command == "checklist":
        checklist = evaluate_pre_session_checklist(settings, args.operator_id, context)
        actions = []
        if args.acknowledge:
            auth_context, denied_actions, blocked_reasons = _require_action_auth(
                database,
                settings,
                operator_id=args.operator_id,
                permission_action=PermissionAction.RUN_CHECKLIST,
                action_type=OperatorActionType.CHECKLIST_ACKNOWLEDGED,
                mode=settings.execution.mode,
                auth_session_id=args.auth_session_id,
                target_type="checklist",
                target_id=checklist.checklist_id,
            )
            actions.extend(denied_actions)
            if auth_context is not None:
                checklist, action = acknowledge_checklist(
                    checklist,
                    _operator_name(auth_context, args.operator_id),
                    auth_context=auth_context,
                )
                actions.append(action)
            else:
                database.save_pre_session_checklist(checklist)
                database.save_operator_actions(actions)
                outputs = _emit_report(database, settings, output_dir)
                print(
                    "operator_session=checklist_ack_blocked "
                    f"status={checklist.status.value} reasons={'; '.join(blocked_reasons)} summary={outputs['summary']}"
                )
                return
        database.save_pre_session_checklist(checklist)
        database.save_operator_actions(actions)
        outputs = _emit_report(database, settings, output_dir)
        print(
            "operator_session=checklist "
            f"status={checklist.status.value} blockers={len(checklist.blockers)} warnings={len(checklist.warnings)} "
            f"acknowledged={checklist.acknowledged} summary={outputs['summary']}"
        )
        return

    if args.command == "authorize-live":
        auth_context, actions, blocked_reasons = _require_action_auth(
            database,
            settings,
            operator_id=args.operator_id,
            permission_action=PermissionAction.AUTHORIZE_LIVE,
            action_type=OperatorActionType.LIVE_AUTHORIZATION_DENIED,
            mode=settings.execution.mode,
            auth_session_id=args.auth_session_id,
            target_type="live_authorization",
        )
        if auth_context is None:
            database.save_operator_actions(actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        checklist = evaluate_pre_session_checklist(settings, _operator_name(auth_context, args.operator_id), context)
        if args.acknowledge_checklist:
            checklist, ack_action = acknowledge_checklist(
                checklist,
                _operator_name(auth_context, args.operator_id),
                auth_context=auth_context,
            )
            actions.append(ack_action)
        auth_context, actions, approval, blocked_reasons = _ensure_sensitive_approval(
            database,
            settings,
            auth_context,
            approval_action=ApprovalAction.PRE_LIVE_AUTHORIZATION,
            action_type=OperatorActionType.LIVE_AUTHORIZATION_DENIED,
            mode=settings.execution.mode,
            comment=args.comment,
            reauth_secret=args.reauth_secret,
            target_type="live_authorization",
        )
        if auth_context is None:
            database.save_pre_session_checklist(checklist)
            database.save_operator_actions(actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        authorization, auth_actions = authorize_live(
            settings,
            _operator_name(auth_context, args.operator_id),
            checklist,
            context,
            acknowledged=args.confirm,
            comment=args.comment,
            secondary_operator=args.secondary_operator,
            sessions=sessions,
            handovers=handovers,
            auth_context=auth_context,
        )
        if authorization.status.value == "granted":
            approval = approval.model_copy(
                update={
                    "target_id": authorization.authorization_id,
                    "linked_checklist_id": checklist.checklist_id,
                    "linked_authorization_id": authorization.authorization_id,
                }
            )
            authorization = authorization.model_copy(update={"approval_signature_id": approval.approval_id})
            auth_actions = [action.model_copy(update={"approval_signature_id": approval.approval_id}) for action in auth_actions]
            database.save_approval_signature(approval)
        actions.extend(auth_actions)
        database.save_pre_session_checklist(checklist)
        database.save_live_authorization(authorization)
        database.save_operator_actions(actions)
        outputs = _emit_report(database, settings, output_dir)
        print(
            "operator_session=authorize_live "
            f"status={authorization.status.value} reasons={len(authorization.reasons)} "
            f"expires_at={authorization.expires_at.isoformat() if authorization.expires_at else 'none'} "
            f"summary={outputs['summary']}"
        )
        return

    if args.command == "open":
        auth_context, actions, blocked_reasons = _require_action_auth(
            database,
            settings,
            operator_id=args.operator_id,
            permission_action=PermissionAction.OPEN_SESSION,
            action_type=OperatorActionType.SESSION_OPEN_BLOCKED,
            mode=settings.execution.mode,
            auth_session_id=args.auth_session_id,
            target_type="session",
        )
        if auth_context is None:
            database.save_operator_actions(actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        checklist = evaluate_pre_session_checklist(settings, _operator_name(auth_context, args.operator_id), context)
        if args.acknowledge_checklist:
            checklist, ack_action = acknowledge_checklist(
                checklist,
                _operator_name(auth_context, args.operator_id),
                auth_context=auth_context,
            )
            actions.append(ack_action)
        result = open_trading_session(
            settings,
            _operator_name(auth_context, args.operator_id),
            checklist,
            context,
            confirmed=args.confirm,
            existing_session=database.load_open_trading_session(),
            live_authorization=latest_authorization,
            sessions=sessions,
            handovers=handovers,
            comment=args.comment,
            auth_context=auth_context,
        )
        actions.extend(result.actions)
        database.save_pre_session_checklist(result.checklist)
        if result.session is not None:
            database.save_trading_session(result.session)
        database.save_operator_actions(actions)
        outputs = _emit_report(database, settings, output_dir)
        if result.session is None:
            print(
                "operator_session=open_blocked "
                f"reasons={'; '.join(result.blocked_reasons)} summary={outputs['summary']}"
            )
            return
        print(
            "operator_session=open "
            f"session_id={result.session.session_id} status={result.session.status.value} "
            f"summary={outputs['summary']}"
        )
        return

    if args.command == "close":
        auth_context, actions, blocked_reasons = _require_action_auth(
            database,
            settings,
            operator_id=args.operator_id,
            permission_action=PermissionAction.CLOSE_SESSION,
            action_type=OperatorActionType.SESSION_CLOSED,
            mode=settings.execution.mode,
            auth_session_id=args.auth_session_id,
            target_type="session",
        )
        if auth_context is None:
            database.save_operator_actions(actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        session = database.load_open_trading_session()
        if session is None:
            raise SystemExit("no open supervised session exists")
        result = close_trading_session(
            session,
            _operator_name(auth_context, args.operator_id),
            context,
            comment=args.comment,
            handoff_required=True if args.handoff_required else None,
            auth_context=auth_context,
        )
        database.save_trading_session(result.session)
        actions.extend(result.actions)
        handover_id = "none"
        if result.session.handoff_required:
            handover, handover_actions = create_handover(
                settings,
                result.session,
                context,
                checklist=database.load_latest_pre_session_checklist(),
                live_authorization=latest_authorization,
                notes=args.comment,
                auth_context=auth_context,
            )
            database.save_handover(handover)
            actions.extend(handover_actions)
            handover_id = handover.handover_id
        database.save_operator_actions(actions)
        if "session_close" in settings.audit_integrity.auto_seal_triggers:
            database.create_audit_seal(
                trigger_type=AuditSealTrigger.SESSION_CLOSE,
                trigger_id=result.session.session_id,
                notes=args.comment,
            )
        outputs = _emit_report(database, settings, output_dir)
        print(
            "operator_session=close "
            f"session_id={result.session.session_id} status={result.session.status.value} "
            f"handoff_required={result.session.handoff_required} handover_id={handover_id} summary={outputs['summary']}"
        )
        return

    if args.command == "handover-create":
        auth_context, actions, blocked_reasons = _require_action_auth(
            database,
            settings,
            operator_id=args.operator_id,
            permission_action=PermissionAction.CREATE_HANDOVER,
            action_type=OperatorActionType.HANDOVER_CREATED,
            mode=settings.execution.mode,
            auth_session_id=args.auth_session_id,
            target_type="handover",
        )
        if auth_context is None:
            database.save_operator_actions(actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        session = _resolve_session(database, args.session_id)
        handover, handover_actions = create_handover(
            settings,
            session,
            context,
            checklist=database.load_latest_pre_session_checklist(),
            live_authorization=latest_authorization,
            target_session_id=args.target_session_id,
            target_operator=args.target_operator,
            summary=args.summary,
            notes=args.comment,
            auth_context=auth_context,
        )
        database.save_handover(handover)
        actions.extend(handover_actions)
        database.save_operator_actions(actions)
        outputs = _emit_report(database, settings, output_dir)
        print(
            "operator_session=handover_created "
            f"handover_id={handover.handover_id} status={handover.status.value} summary={outputs['summary']}"
        )
        return

    if args.command == "handover-accept":
        auth_context, actions, blocked_reasons = _require_action_auth(
            database,
            settings,
            operator_id=args.operator_id,
            permission_action=PermissionAction.ACCEPT_HANDOVER,
            action_type=OperatorActionType.HANDOVER_ACCEPTED,
            mode="operator_review",
            auth_session_id=args.auth_session_id,
            target_type="handover",
            target_id=args.handover_id,
        )
        if auth_context is None:
            database.save_operator_actions(actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        handover = _resolve_handover(database, args.handover_id)
        approval: ApprovalSignature | None = None
        if _handover_requires_sensitive_signature(handover):
            auth_context, actions, approval, blocked_reasons = _ensure_sensitive_approval(
                database,
                settings,
                auth_context,
                approval_action=ApprovalAction.ACCEPT_SEVERE_HANDOVER,
                action_type=OperatorActionType.HANDOVER_ACCEPTED,
                mode="operator_review",
                comment=args.comment,
                reauth_secret=args.reauth_secret,
                target_type="handover",
                target_id=handover.handover_id,
                linked_handover_id=handover.handover_id,
            )
            if auth_context is None:
                database.save_operator_actions(actions)
                _emit_report(database, settings, output_dir)
                raise SystemExit("; ".join(blocked_reasons))
        result = accept_handover(
            settings,
            handover,
            _operator_name(auth_context, args.operator_id),
            acknowledged=args.acknowledge,
            target_session_id=args.target_session_id,
            comment=args.comment,
            auth_context=auth_context,
            approval_signature=approval,
        )
        actions.extend(result.actions)
        if result.handover is not None:
            database.save_handover(result.handover)
        if approval is not None and result.handover is not None and not result.blocked_reasons:
            database.save_approval_signature(
                approval.model_copy(update={"linked_handover_id": result.handover.handover_id})
            )
        database.save_operator_actions(actions)
        if result.handover is not None and "handover" in settings.audit_integrity.auto_seal_triggers and not result.blocked_reasons:
            database.create_audit_seal(
                trigger_type=AuditSealTrigger.HANDOVER,
                trigger_id=result.handover.handover_id,
                notes=args.comment,
            )
        outputs = _emit_report(database, settings, output_dir)
        if result.blocked_reasons:
            print(
                "operator_session=handover_accept_blocked "
                f"reasons={'; '.join(result.blocked_reasons)} summary={outputs['summary']}"
            )
            return
        print(
            "operator_session=handover_accepted "
            f"handover_id={result.handover.handover_id if result.handover else 'none'} summary={outputs['summary']}"
        )
        return

    if args.command == "handover-refuse":
        auth_context, actions, blocked_reasons = _require_action_auth(
            database,
            settings,
            operator_id=args.operator_id,
            permission_action=PermissionAction.ACCEPT_HANDOVER,
            action_type=OperatorActionType.HANDOVER_REFUSED,
            mode="operator_review",
            auth_session_id=args.auth_session_id,
            target_type="handover",
            target_id=args.handover_id,
        )
        if auth_context is None:
            database.save_operator_actions(actions)
            _emit_report(database, settings, output_dir)
            raise SystemExit("; ".join(blocked_reasons))
        handover = _resolve_handover(database, args.handover_id)
        result = refuse_handover(
            handover,
            _operator_name(auth_context, args.operator_id),
            refusal_reason=args.reason,
            comment=args.comment,
            auth_context=auth_context,
        )
        actions.extend(result.actions)
        if result.handover is not None:
            database.save_handover(result.handover)
        database.save_operator_actions(actions)
        if result.handover is not None and "handover" in settings.audit_integrity.auto_seal_triggers and not result.blocked_reasons:
            database.create_audit_seal(
                trigger_type=AuditSealTrigger.HANDOVER,
                trigger_id=result.handover.handover_id,
                notes=args.comment or args.reason,
            )
        outputs = _emit_report(database, settings, output_dir)
        if result.blocked_reasons:
            print(
                "operator_session=handover_refuse_blocked "
                f"reasons={'; '.join(result.blocked_reasons)} summary={outputs['summary']}"
            )
            return
        print(
            "operator_session=handover_refused "
            f"handover_id={result.handover.handover_id if result.handover else 'none'} summary={outputs['summary']}"
        )
        return


def _operator_name(auth_context: AuthenticatedOperatorContext | None, fallback_operator_id: str) -> str:
    if auth_context is None:
        return fallback_operator_id
    return auth_context.identity.display_name


def _require_action_auth(
    database: Database,
    settings,
    *,
    operator_id: str,
    permission_action: PermissionAction,
    action_type: OperatorActionType,
    mode: str,
    auth_session_id: str | None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> tuple[AuthenticatedOperatorContext | None, list, list[str]]:
    identities = database.load_operator_identities()
    auth_sessions = database.load_operator_auth_sessions()
    auth_context, decision = require_authenticated_context(
        identities,
        auth_sessions,
        operator_id=operator_id,
        action=permission_action,
        auth_session_id=auth_session_id,
    )
    if auth_context is not None:
        return auth_context, [], []
    denial = record_operator_action(
        operator=operator_id,
        action_type=action_type,
        mode=mode,
        result=OperatorActionResult.DENIED,
        target_type=target_type,
        target_id=target_id,
        reason="; ".join(decision.reasons),
    )
    return None, [denial], decision.reasons


def _ensure_sensitive_approval(
    database: Database,
    settings,
    auth_context: AuthenticatedOperatorContext,
    *,
    approval_action: ApprovalAction,
    action_type: OperatorActionType,
    mode: str,
    comment: str | None,
    reauth_secret: str | None,
    target_type: str,
    target_id: str | None = None,
    linked_checklist_id: str | None = None,
    linked_authorization_id: str | None = None,
    linked_session_id: str | None = None,
    linked_handover_id: str | None = None,
    linked_incident_id: str | None = None,
) -> tuple[AuthenticatedOperatorContext | None, list, ApprovalSignature | None, list[str]]:
    actions = []
    blockers: list[str] = []
    if not approval_role_allowed(auth_context.identity.role, approval_action):
        blockers.append(
            f"role {auth_context.identity.role.value} cannot sign {approval_action.value}"
        )
    if approval_action.value in settings.operator_auth.approval_comment_required_actions and not (comment or "").strip():
        blockers.append(f"a comment is required for {approval_action.value}")
    if blockers:
        denial = record_operator_action(
            operator=auth_context.identity.display_name,
            action_type=action_type,
            mode=mode,
            result=OperatorActionResult.DENIED,
            target_type=target_type,
            target_id=target_id,
            reason="; ".join(blockers),
            auth_context=auth_context,
        )
        return None, [denial], None, blockers

    reauth_actions = {ApprovalAction(value) for value in settings.operator_auth.reauth_required_actions}
    requires_reauth = auth_reauth_required(
        auth_context.auth_session,
        action=approval_action,
        reauth_actions=reauth_actions,
        reauth_window_minutes=settings.operator_auth.reauth_window_minutes,
    )
    if requires_reauth:
        if not reauth_secret:
            denial = record_operator_action(
                operator=auth_context.identity.display_name,
                action_type=OperatorActionType.REAUTH_REQUIRED,
                mode=mode,
                result=OperatorActionResult.DENIED,
                target_type=target_type,
                target_id=target_id,
                reason=f"explicit re-authentication is required for {approval_action.value}",
                auth_context=auth_context,
            )
            return None, [denial], None, [f"explicit re-authentication is required for {approval_action.value}"]
        try:
            refreshed = reauthenticate_operator_session(
                auth_context.identity,
                auth_context.auth_session,
                reauth_secret,
                session_expiry_minutes=settings.operator_auth.session_expiry_minutes,
            )
        except ValueError as exc:
            denial = record_operator_action(
                operator=auth_context.identity.display_name,
                action_type=OperatorActionType.REAUTH_REQUIRED,
                mode=mode,
                result=OperatorActionResult.DENIED,
                target_type=target_type,
                target_id=target_id,
                reason=str(exc),
                auth_context=auth_context,
            )
            return None, [denial], None, [str(exc)]
        database.save_operator_auth_session(refreshed)
        auth_context = AuthenticatedOperatorContext(identity=auth_context.identity, auth_session=refreshed)
        actions.append(
            record_operator_action(
                operator=auth_context.identity.display_name,
                action_type=OperatorActionType.REAUTH_COMPLETED,
                mode=mode,
                target_type=target_type,
                target_id=target_id,
                reason=f"operator completed explicit re-authentication for {approval_action.value}",
                auth_context=auth_context,
            )
        )

    approval = build_approval_signature(
        auth_context,
        approval_action,
        target_type=target_type,
        target_id=target_id,
        expires_at=auth_context.auth_session.last_verified_at
        + timedelta(minutes=settings.operator_auth.approval_expiry_minutes),
        reason=comment,
        comment=comment,
        requires_reauth=requires_reauth,
        linked_checklist_id=linked_checklist_id,
        linked_authorization_id=linked_authorization_id,
        linked_session_id=linked_session_id,
        linked_handover_id=linked_handover_id,
        linked_incident_id=linked_incident_id,
    )
    return auth_context, actions, approval, []


def _handover_requires_sensitive_signature(handover) -> bool:
    return bool(
        handover.blocked_items
        or handover.pending_manual_actions
        or handover.unresolved_incidents
        or handover.unresolved_alerts
        or handover.unresolved_reconciliation_anomalies
    )


def _load_context(
    database: Database,
    *,
    settings=None,
    refresh: bool = False,
) -> OperatorWorkflowContext:
    if refresh and settings is not None and settings.execution.mode != "paper":
        try:
            adapter = build_execution_adapter(settings)
            result = run_startup_recovery(settings, adapter, database.load_broker_orders())
            previous_incidents = database.load_broker_incidents()
            previous_alerts = database.load_operational_alerts()
            incidents = merge_operational_incidents(previous_incidents, result.incidents)
            resolution = resolve_recovered_incidents(previous_incidents, incidents)
            metrics = build_operational_metrics(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders)
            alerts = generate_operational_alerts(result.snapshot, incidents, result.reconciliation_report.anomalies, result.updated_orders, settings, previous_alerts)
            resolved_alerts = resolve_operational_alerts(previous_alerts, alerts)
            database.save_broker_orders(result.updated_orders)
            database.save_reconciliation_report(result.reconciliation_report)
            database.save_broker_health_snapshot(result.snapshot)
            database.save_broker_incidents([*incidents, *resolution.closed_incidents])
            database.save_operational_metrics(metrics)
            database.save_operational_alerts([*alerts, *resolved_alerts])
            database.save_trade_events([*result.events, *resolution.events])
            return OperatorWorkflowContext(
                snapshot=result.snapshot,
                account_state=result.account_state,
                incidents=database.load_broker_incidents(),
                alerts=database.load_operational_alerts(),
                anomalies=database.load_reconciliation_anomalies(),
                controls=database.load_operator_controls(),
                broker_orders=database.load_broker_orders(),
                latest_campaign=latest_campaign_with_readiness(database.load_soak_campaigns()),
                latest_audit_verification=database.load_latest_audit_verification(),
                latest_recovery_validation=load_recovery_state(settings),
            )
        except Exception as exc:
            LOGGER.warning("operator session refresh fell back to persisted state: %s", exc)

    snapshots = database.load_broker_health_snapshots()
    return OperatorWorkflowContext(
        snapshot=snapshots[-1] if snapshots else None,
        account_state=None,
        incidents=database.load_broker_incidents(),
        alerts=database.load_operational_alerts(),
        anomalies=database.load_reconciliation_anomalies(),
        controls=database.load_operator_controls(),
        broker_orders=database.load_broker_orders(),
        latest_campaign=latest_campaign_with_readiness(database.load_soak_campaigns()),
        latest_audit_verification=database.load_latest_audit_verification(),
        latest_recovery_validation=load_recovery_state(settings) if settings is not None else None,
    )


def _emit_report(database: Database, settings, output_dir: Path) -> dict[str, Path]:
    context = _load_context(database)
    checklists = database.load_pre_session_checklists()
    authorizations = database.load_live_authorizations()
    sessions = database.load_trading_sessions()
    handovers = database.load_handovers()
    latest_checklist = checklists[-1] if checklists else None
    latest_authorization = authorizations[-1] if authorizations else None
    continuity = evaluate_inter_session_continuity(
        settings,
        context,
        sessions,
        handovers,
        live_authorization=latest_authorization,
    )
    outputs = generate_operator_workflow_report(
        checklists,
        authorizations,
        sessions,
        database.load_operator_actions(),
        live_authorization_block_reasons(
            settings,
            latest_checklist,
            latest_authorization,
            context,
            sessions=sessions,
            handovers=handovers,
        ),
        output_dir,
        handovers=handovers,
        continuity=continuity,
        identities=database.load_operator_identities(),
        auth_sessions=database.load_operator_auth_sessions(),
        approval_signatures=database.load_approval_signatures(),
    )
    return outputs


def _resolve_session(database: Database, session_id: str | None):
    if session_id:
        for session in database.load_trading_sessions():
            if session.session_id == session_id:
                return session
        raise SystemExit(f"session {session_id} was not found")
    session = database.load_open_trading_session()
    if session is not None:
        return session
    sessions = database.load_trading_sessions()
    if not sessions:
        raise SystemExit("no supervised session exists")
    return sessions[-1]


def _resolve_handover(database: Database, handover_id: str | None):
    if handover_id:
        handover = database.load_handover(handover_id)
        if handover is None:
            raise SystemExit(f"handover {handover_id} was not found")
        return handover
    handover = database.load_latest_handover()
    if handover is None:
        raise SystemExit("no handover exists")
    return handover


if __name__ == "__main__":
    main()
