"""Operator workflow reports for checklist, session, authorization, and handover review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.execution.operator_identity import (
    ApprovalSignature,
    ApprovalSignatureStatus,
    OperatorAuthSession,
    OperatorIdentity,
    effective_auth_session_status,
)
from app.execution.operator_workflows import (
    ContinuityCheckResult,
    HandoverRecord,
    LiveAuthorizationRecord,
    OperatorActionRecord,
    PreSessionChecklist,
    TradingSessionRecord,
)


def generate_operator_workflow_report(
    checklists: list[PreSessionChecklist],
    authorizations: list[LiveAuthorizationRecord],
    sessions: list[TradingSessionRecord],
    actions: list[OperatorActionRecord],
    blockers: list[str],
    output_dir: Path,
    *,
    handovers: list[HandoverRecord] | None = None,
    continuity: ContinuityCheckResult | None = None,
    identities: list[OperatorIdentity] | None = None,
    auth_sessions: list[OperatorAuthSession] | None = None,
    approval_signatures: list[ApprovalSignature] | None = None,
) -> dict[str, Path]:
    """Write operator-facing workflow summaries in Markdown and CSV/JSON."""

    output_dir.mkdir(parents=True, exist_ok=True)
    handover_records = handovers or []
    operator_identities = identities or []
    operator_auth_sessions = auth_sessions or []
    approvals = approval_signatures or []
    latest_checklist = checklists[-1] if checklists else None
    latest_authorization = authorizations[-1] if authorizations else None
    latest_handover = handover_records[-1] if handover_records else None
    current_session = next((session for session in reversed(sessions) if session.closed_at is None), None)
    unresolved_handoffs = [session for session in sessions if session.handoff_required]
    pending_handovers = [handover for handover in handover_records if handover.status.value != "accepted"]
    active_auth_sessions = [
        session for session in operator_auth_sessions if effective_auth_session_status(session).value == "active"
    ]
    expired_approvals = [approval for approval in approvals if _effective_approval_status(approval) == ApprovalSignatureStatus.EXPIRED]

    checklist_frame = _checklists_frame(checklists)
    authorization_frame = _authorizations_frame(authorizations)
    session_frame = _sessions_frame(sessions)
    action_frame = _actions_frame(actions)
    blocker_frame = _blockers_frame(blockers)
    unresolved_handoff_frame = _unresolved_handoff_frame(unresolved_handoffs)
    handover_frame = _handovers_frame(handover_records)
    pending_handover_frame = _pending_handovers_frame(pending_handovers)
    carry_over_frame = _carry_over_frame(handover_records)
    open_risk_frame = _open_risk_frame(latest_handover, continuity)
    identity_frame = _identity_frame(operator_identities)
    auth_session_frame = _auth_sessions_frame(operator_auth_sessions)
    approval_frame = _approvals_frame(approvals)
    approval_by_action_frame = _approval_by_action_frame(approvals)
    expired_approval_frame = _approvals_frame(expired_approvals)
    denied_privileged_frame = _denied_privileged_frame(actions)
    reauth_frame = _reauth_frame(actions)

    outputs = {
        "summary": output_dir / "summary.md",
        "summary_json": output_dir / "summary.json",
        "latest_checklist": output_dir / "latest_checklist.md",
        "latest_checklist_json": output_dir / "latest_checklist.json",
        "current_session": output_dir / "current_session.md",
        "current_session_json": output_dir / "current_session.json",
        "latest_authorization": output_dir / "latest_authorization.md",
        "latest_authorization_json": output_dir / "latest_authorization.json",
        "outstanding_blockers": output_dir / "outstanding_live_blockers.md",
        "outstanding_blockers_csv": output_dir / "outstanding_live_blockers.csv",
        "session_history": output_dir / "session_history.csv",
        "operator_actions": output_dir / "operator_actions.csv",
        "authorization_history": output_dir / "authorization_history.csv",
        "checklist_history": output_dir / "checklist_history.csv",
        "unresolved_handoffs": output_dir / "unresolved_handoffs.csv",
        "latest_handover": output_dir / "latest_handover.md",
        "latest_handover_json": output_dir / "latest_handover.json",
        "continuity_summary": output_dir / "continuity_summary.md",
        "continuity_summary_json": output_dir / "continuity_summary.json",
        "handover_history": output_dir / "handover_history.csv",
        "pending_handovers": output_dir / "pending_handovers.csv",
        "carry_over_items": output_dir / "carry_over_items.csv",
        "open_risk_items": output_dir / "open_risk_items.csv",
        "operator_identities": output_dir / "operator_identities.csv",
        "active_auth_sessions": output_dir / "active_operator_sessions.csv",
        "approval_history": output_dir / "approval_history.csv",
        "approval_signatures_by_action": output_dir / "approval_signatures_by_action.csv",
        "expired_approvals": output_dir / "expired_approvals.csv",
        "denied_privileged_actions": output_dir / "denied_privileged_actions.csv",
        "reauth_events": output_dir / "reauth_events.csv",
        "identity_audit_summary": output_dir / "identity_audit_summary.md",
        "identity_audit_summary_json": output_dir / "identity_audit_summary.json",
    }

    checklist_frame.to_csv(outputs["checklist_history"], index=False)
    authorization_frame.to_csv(outputs["authorization_history"], index=False)
    session_frame.to_csv(outputs["session_history"], index=False)
    action_frame.to_csv(outputs["operator_actions"], index=False)
    blocker_frame.to_csv(outputs["outstanding_blockers_csv"], index=False)
    unresolved_handoff_frame.to_csv(outputs["unresolved_handoffs"], index=False)
    handover_frame.to_csv(outputs["handover_history"], index=False)
    pending_handover_frame.to_csv(outputs["pending_handovers"], index=False)
    carry_over_frame.to_csv(outputs["carry_over_items"], index=False)
    open_risk_frame.to_csv(outputs["open_risk_items"], index=False)
    identity_frame.to_csv(outputs["operator_identities"], index=False)
    auth_session_frame.to_csv(outputs["active_auth_sessions"], index=False)
    approval_frame.to_csv(outputs["approval_history"], index=False)
    approval_by_action_frame.to_csv(outputs["approval_signatures_by_action"], index=False)
    expired_approval_frame.to_csv(outputs["expired_approvals"], index=False)
    denied_privileged_frame.to_csv(outputs["denied_privileged_actions"], index=False)
    reauth_frame.to_csv(outputs["reauth_events"], index=False)

    summary_payload = {
        "checklists": len(checklists),
        "authorizations": len(authorizations),
        "sessions": len(sessions),
        "open_session": current_session.session_id if current_session else None,
        "unresolved_handoffs": len(unresolved_handoffs),
        "handovers_total": len(handover_records),
        "handovers_accepted": sum(1 for handover in handover_records if handover.status.value == "accepted"),
        "handovers_unaccepted": sum(1 for handover in handover_records if handover.status.value != "accepted"),
        "operator_identities": len(operator_identities),
        "active_auth_sessions": len(active_auth_sessions),
        "approval_signatures": len(approvals),
        "expired_approvals": len(expired_approvals),
        "outstanding_live_blockers": blockers,
        "continuity_blockers": continuity.blockers if continuity else [],
        "continuity_warnings": continuity.warnings if continuity else [],
    }
    outputs["summary_json"].write_text(json.dumps(summary_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["summary"].write_text(_summary_markdown(summary_payload), encoding="utf-8")
    identity_audit_payload = _identity_audit_payload(operator_identities, operator_auth_sessions, approvals, actions)
    outputs["identity_audit_summary_json"].write_text(json.dumps(identity_audit_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs["identity_audit_summary"].write_text(_identity_audit_markdown(identity_audit_payload), encoding="utf-8")
    outputs["latest_checklist_json"].write_text(_json_or_empty(latest_checklist), encoding="utf-8")
    outputs["latest_checklist"].write_text(_checklist_markdown(latest_checklist), encoding="utf-8")
    outputs["current_session_json"].write_text(_json_or_empty(current_session), encoding="utf-8")
    outputs["current_session"].write_text(_current_session_markdown(current_session), encoding="utf-8")
    outputs["latest_authorization_json"].write_text(_json_or_empty(latest_authorization), encoding="utf-8")
    outputs["latest_authorization"].write_text(_authorization_markdown(latest_authorization), encoding="utf-8")
    outputs["outstanding_blockers"].write_text(_blockers_markdown(blockers), encoding="utf-8")
    outputs["latest_handover_json"].write_text(_json_or_empty(latest_handover), encoding="utf-8")
    outputs["latest_handover"].write_text(_handover_markdown(latest_handover), encoding="utf-8")
    outputs["continuity_summary_json"].write_text(_json_or_empty(continuity), encoding="utf-8")
    outputs["continuity_summary"].write_text(_continuity_markdown(continuity), encoding="utf-8")
    return outputs


def _checklists_frame(checklists: list[PreSessionChecklist]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "checklist_id": checklist.checklist_id,
                "created_at": checklist.created_at.isoformat(),
                "operator": checklist.operator,
                "mode": checklist.mode,
                "broker": checklist.broker,
                "status": checklist.status.value,
                "acknowledged": checklist.acknowledged,
                "acknowledged_at": checklist.acknowledged_at.isoformat() if checklist.acknowledged_at else "",
                "blockers": len(checklist.blockers),
                "warnings": len(checklist.warnings),
                "campaign_id": checklist.linked_campaign_id or "",
                "campaign_readiness": checklist.linked_campaign_readiness.value if checklist.linked_campaign_readiness else "",
            }
            for checklist in checklists
        ]
    )


def _authorizations_frame(authorizations: list[LiveAuthorizationRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "authorization_id": authorization.authorization_id,
                "created_at": authorization.created_at.isoformat(),
                "operator": authorization.operator,
                "operator_id": authorization.operator_id or "",
                "operator_role": authorization.operator_role.value if authorization.operator_role else "",
                "auth_session_id": authorization.auth_session_id or "",
                "approval_signature_id": authorization.approval_signature_id or "",
                "secondary_operator": authorization.secondary_operator or "",
                "mode": authorization.mode,
                "broker": authorization.broker,
                "status": authorization.status.value,
                "linked_checklist_id": authorization.linked_checklist_id or "",
                "linked_campaign_id": authorization.linked_campaign_id or "",
                "checklist_status": authorization.checklist_status.value if authorization.checklist_status else "",
                "campaign_readiness": authorization.campaign_readiness.value if authorization.campaign_readiness else "",
                "acknowledged": authorization.acknowledged,
                "expires_at": authorization.expires_at.isoformat() if authorization.expires_at else "",
                "reasons": "; ".join(authorization.reasons),
                "warnings": "; ".join(authorization.warnings),
                "comment": authorization.comment or "",
            }
            for authorization in authorizations
        ]
    )


def _sessions_frame(sessions: list[TradingSessionRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "session_id": session.session_id,
                "opened_at": session.opened_at.isoformat(),
                "closed_at": session.closed_at.isoformat() if session.closed_at else "",
                "operator": session.operator,
                "mode": session.mode,
                "broker": session.broker,
                "status": session.status.value,
                "linked_checklist_id": session.linked_checklist_id or "",
                "linked_authorization_id": session.linked_authorization_id or "",
                "handoff_required": session.handoff_required,
                "unresolved_items": "; ".join(session.unresolved_items),
                "notes": session.notes or "",
            }
            for session in sessions
        ]
    )


def _actions_frame(actions: list[OperatorActionRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "action_id": action.action_id,
                "created_at": action.created_at.isoformat(),
                "operator": action.operator,
                "operator_id": action.operator_id or "",
                "operator_display_name": action.operator_display_name or "",
                "operator_role": action.operator_role.value if action.operator_role else "",
                "auth_session_id": action.auth_session_id or "",
                "approval_signature_id": action.approval_signature_id or "",
                "action_type": action.action_type.value,
                "result": action.result.value,
                "mode": action.mode,
                "target_type": action.target_type or "",
                "target_id": action.target_id or "",
                "linked_checklist_id": action.linked_checklist_id or "",
                "linked_authorization_id": action.linked_authorization_id or "",
                "linked_session_id": action.linked_session_id or "",
                "reason": action.reason or "",
            }
            for action in actions
        ]
    )


def _handovers_frame(handovers: list[HandoverRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "handover_id": handover.handover_id,
                "created_at": handover.created_at.isoformat(),
                "reviewed_at": handover.reviewed_at.isoformat() if handover.reviewed_at else "",
                "accepted_at": handover.accepted_at.isoformat() if handover.accepted_at else "",
                "expires_at": handover.expires_at.isoformat() if handover.expires_at else "",
                "source_session_id": handover.source_session_id,
                "target_session_id": handover.target_session_id or "",
                "source_operator": handover.source_operator,
                "target_operator": handover.target_operator or "",
                "status": handover.status.value,
                "linked_checklist_id": handover.linked_checklist_id or "",
                "linked_checklist_status": handover.linked_checklist_status.value if handover.linked_checklist_status else "",
                "live_authorization_state": handover.live_authorization_state.value if handover.live_authorization_state else "",
                "acceptance_signature_id": handover.acceptance_signature_id or "",
                "blocked_items": len(handover.blocked_items),
                "pending_manual_actions": len(handover.pending_manual_actions),
                "notes": handover.notes or "",
                "refusal_reason": handover.refusal_reason or "",
            }
            for handover in handovers
        ]
    )


def _pending_handovers_frame(handovers: list[HandoverRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "handover_id": handover.handover_id,
                "created_at": handover.created_at.isoformat(),
                "source_session_id": handover.source_session_id,
                "source_operator": handover.source_operator,
                "status": handover.status.value,
                "blocked_items": len(handover.blocked_items),
                "pending_manual_actions": len(handover.pending_manual_actions),
                "summary": handover.summary,
            }
            for handover in handovers
        ]
    )


def _blockers_frame(blockers: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{"blocker": blocker} for blocker in blockers])


def _unresolved_handoff_frame(sessions: list[TradingSessionRecord]) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for session in sessions:
        if not session.unresolved_items:
            rows.append(
                {
                    "session_id": session.session_id,
                    "closed_at": session.closed_at.isoformat() if session.closed_at else "",
                    "status": session.status.value,
                    "item": "",
                }
            )
            continue
        for item in session.unresolved_items:
            rows.append(
                {
                    "session_id": session.session_id,
                    "closed_at": session.closed_at.isoformat() if session.closed_at else "",
                    "status": session.status.value,
                    "item": item,
                }
            )
    return pd.DataFrame(rows)


def _carry_over_frame(handovers: list[HandoverRecord]) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for handover in handovers:
        for incident in handover.unresolved_incidents:
            rows.append(_carry_over_row(handover, "incident", incident.record_id, incident.severity, incident.label, incident.reason))
        for alert in handover.unresolved_alerts:
            rows.append(_carry_over_row(handover, "alert", alert.record_id, alert.severity, alert.label, alert.reason))
        for anomaly in handover.unresolved_reconciliation_anomalies:
            rows.append(_carry_over_row(handover, "anomaly", anomaly.record_id, anomaly.severity, anomaly.label, anomaly.reason))
        for exposure in handover.open_positions_orders:
            rows.append(_carry_over_row(handover, exposure.kind, exposure.identifier, exposure.status, exposure.symbol, exposure.reason))
    return pd.DataFrame(rows)


def _open_risk_frame(latest_handover: HandoverRecord | None, continuity: ContinuityCheckResult | None) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    if latest_handover is not None:
        for item in latest_handover.blocked_items:
            rows.append({"source": "handover", "severity": "blocker", "item": item})
        for item in latest_handover.pending_manual_actions:
            rows.append({"source": "handover", "severity": "manual_action", "item": item})
    if continuity is not None:
        for item in continuity.blockers:
            rows.append({"source": "continuity", "severity": "blocker", "item": item})
        for item in continuity.warnings:
            rows.append({"source": "continuity", "severity": "warning", "item": item})
        for item in continuity.carry_over_items:
            rows.append({"source": "continuity", "severity": "carry_over", "item": item})
    return pd.DataFrame(rows)


def _identity_frame(identities: list[OperatorIdentity]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "operator_id": identity.operator_id,
                "display_name": identity.display_name,
                "role": identity.role.value,
                "status": identity.status.value,
                "team": identity.team or "",
                "shift": identity.shift or "",
                "created_at": identity.created_at.isoformat(),
                "updated_at": identity.updated_at.isoformat(),
            }
            for identity in identities
        ]
    )


def _auth_sessions_frame(auth_sessions: list[OperatorAuthSession]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "auth_session_id": auth_session.auth_session_id,
                "operator_id": auth_session.operator_id,
                "display_name": auth_session.display_name,
                "role": auth_session.role.value,
                "status": effective_auth_session_status(auth_session).value,
                "authenticated_at": auth_session.authenticated_at.isoformat(),
                "last_verified_at": auth_session.last_verified_at.isoformat(),
                "expires_at": auth_session.expires_at.isoformat(),
                "signed_out_at": auth_session.signed_out_at.isoformat() if auth_session.signed_out_at else "",
                "team": auth_session.team or "",
                "shift": auth_session.shift or "",
            }
            for auth_session in auth_sessions
        ]
    )


def _approvals_frame(approvals: list[ApprovalSignature]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "approval_id": approval.approval_id,
                "created_at": approval.created_at.isoformat(),
                "operator_id": approval.operator_id,
                "operator_display_name": approval.operator_display_name,
                "role": approval.role.value,
                "auth_session_id": approval.auth_session_id,
                "action": approval.action.value,
                "status": _effective_approval_status(approval).value,
                "target_type": approval.target_type,
                "target_id": approval.target_id or "",
                "requires_reauth": approval.requires_reauth,
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else "",
                "reason": approval.reason or "",
                "comment": approval.comment or "",
                "linked_checklist_id": approval.linked_checklist_id or "",
                "linked_authorization_id": approval.linked_authorization_id or "",
                "linked_session_id": approval.linked_session_id or "",
                "linked_handover_id": approval.linked_handover_id or "",
                "linked_incident_id": approval.linked_incident_id or "",
            }
            for approval in approvals
        ]
    )


def _approval_by_action_frame(approvals: list[ApprovalSignature]) -> pd.DataFrame:
    if not approvals:
        return pd.DataFrame(columns=["action", "count", "expired", "requires_reauth"])
    rows: dict[str, dict[str, int | str]] = {}
    for approval in approvals:
        action = approval.action.value
        if action not in rows:
            rows[action] = {"action": action, "count": 0, "expired": 0, "requires_reauth": 0}
        rows[action]["count"] = int(rows[action]["count"]) + 1
        if _effective_approval_status(approval) == ApprovalSignatureStatus.EXPIRED:
            rows[action]["expired"] = int(rows[action]["expired"]) + 1
        if approval.requires_reauth:
            rows[action]["requires_reauth"] = int(rows[action]["requires_reauth"]) + 1
    return pd.DataFrame(rows.values())


def _denied_privileged_frame(actions: list[OperatorActionRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "action_id": action.action_id,
                "created_at": action.created_at.isoformat(),
                "operator": action.operator,
                "operator_id": action.operator_id or "",
                "role": action.operator_role.value if action.operator_role else "",
                "auth_session_id": action.auth_session_id or "",
                "action_type": action.action_type.value,
                "reason": action.reason or "",
                "target_type": action.target_type or "",
                "target_id": action.target_id or "",
            }
            for action in actions
            if action.result.value == "denied"
        ]
    )


def _reauth_frame(actions: list[OperatorActionRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "action_id": action.action_id,
                "created_at": action.created_at.isoformat(),
                "operator": action.operator,
                "operator_id": action.operator_id or "",
                "role": action.operator_role.value if action.operator_role else "",
                "auth_session_id": action.auth_session_id or "",
                "action_type": action.action_type.value,
                "reason": action.reason or "",
                "approval_signature_id": action.approval_signature_id or "",
            }
            for action in actions
            if action.action_type.value in {"reauth_required", "reauth_completed"}
        ]
    )


def _identity_audit_payload(
    identities: list[OperatorIdentity],
    auth_sessions: list[OperatorAuthSession],
    approvals: list[ApprovalSignature],
    actions: list[OperatorActionRecord],
) -> dict[str, object]:
    return {
        "identities": len(identities),
        "active_auth_sessions": sum(
            1 for auth_session in auth_sessions if effective_auth_session_status(auth_session).value == "active"
        ),
        "approval_signatures": len(approvals),
        "expired_approvals": sum(1 for approval in approvals if _effective_approval_status(approval) == ApprovalSignatureStatus.EXPIRED),
        "denied_privileged_actions": sum(1 for action in actions if action.result.value == "denied"),
        "reauth_events": sum(1 for action in actions if action.action_type.value in {"reauth_required", "reauth_completed"}),
        "approvals_by_action": sorted(
            (
                {
                    "action": action,
                    "count": count,
                }
                for action, count in _count_by_action(approvals).items()
            ),
            key=lambda item: str(item["action"]),
        ),
    }


def _effective_approval_status(approval: ApprovalSignature) -> ApprovalSignatureStatus:
    if approval.expires_at is not None and approval.expires_at <= datetime.now(timezone.utc):
        return ApprovalSignatureStatus.EXPIRED
    return approval.status


def _count_by_action(approvals: list[ApprovalSignature]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for approval in approvals:
        counts[approval.action.value] = counts.get(approval.action.value, 0) + 1
    return counts


def _identity_audit_markdown(payload: dict[str, object]) -> str:
    approvals_by_action = payload.get("approvals_by_action", [])
    lines = [
        "# Identity Audit Summary",
        "",
        f"- Configured identities: {payload.get('identities', 0)}",
        f"- Active auth sessions: {payload.get('active_auth_sessions', 0)}",
        f"- Approval signatures: {payload.get('approval_signatures', 0)}",
        f"- Expired approvals: {payload.get('expired_approvals', 0)}",
        f"- Denied privileged actions: {payload.get('denied_privileged_actions', 0)}",
        f"- Re-auth events: {payload.get('reauth_events', 0)}",
    ]
    if approvals_by_action:
        lines.extend(["", "## Approval Actions", ""])
        for item in approvals_by_action:
            lines.append(f"- {item['action']}: {item['count']}")
    lines.append("")
    return "\n".join(lines)


def _carry_over_row(
    handover: HandoverRecord,
    category: str,
    record_id: str,
    severity: str,
    label: str,
    reason: str,
) -> dict[str, str]:
    return {
        "handover_id": handover.handover_id,
        "source_session_id": handover.source_session_id,
        "status": handover.status.value,
        "category": category,
        "record_id": record_id,
        "severity": severity,
        "label": label,
        "reason": reason,
    }


def _summary_markdown(summary: dict[str, object]) -> str:
    blockers = summary["outstanding_live_blockers"]
    continuity_blockers = summary["continuity_blockers"]
    blocker_count = len(blockers) if isinstance(blockers, list) else 0
    continuity_count = len(continuity_blockers) if isinstance(continuity_blockers, list) else 0
    return "\n".join(
        [
            "# Operator Workflow Summary",
            "",
            f"Checklists recorded: {summary['checklists']}",
            f"Authorizations recorded: {summary['authorizations']}",
            f"Sessions recorded: {summary['sessions']}",
            f"Current open session: {summary['open_session'] or 'none'}",
            f"Unresolved handoffs: {summary['unresolved_handoffs']}",
            f"Handovers total: {summary['handovers_total']}",
            f"Handovers accepted: {summary['handovers_accepted']}",
            f"Handovers unaccepted: {summary['handovers_unaccepted']}",
            f"Configured operator identities: {summary['operator_identities']}",
            f"Active auth sessions: {summary['active_auth_sessions']}",
            f"Approval signatures: {summary['approval_signatures']}",
            f"Expired approvals: {summary['expired_approvals']}",
            f"Outstanding live blockers: {blocker_count}",
            f"Continuity blockers: {continuity_count}",
            "",
        ]
    )


def _checklist_markdown(checklist: PreSessionChecklist | None) -> str:
    if checklist is None:
        return "# Latest Checklist\n\nNo checklist has been recorded yet.\n"
    lines = [
        "# Latest Checklist",
        "",
        f"Checklist id: {checklist.checklist_id}",
        f"Created at: {checklist.created_at.isoformat()}",
        f"Operator: {checklist.operator}",
        f"Mode: {checklist.mode}",
        f"Broker: {checklist.broker}",
        f"Status: {checklist.status.value}",
        f"Acknowledged: {checklist.acknowledged}",
        "",
        "## Blockers",
    ]
    lines.extend(f"- {blocker}" for blocker in checklist.blockers or ["none"])
    lines.append("")
    lines.append("## Warnings")
    lines.extend(f"- {warning}" for warning in checklist.warnings or ["none"])
    lines.append("")
    return "\n".join(lines)


def _authorization_markdown(authorization: LiveAuthorizationRecord | None) -> str:
    if authorization is None:
        return "# Latest Live Authorization\n\nNo live authorization has been recorded yet.\n"
    lines = [
        "# Latest Live Authorization",
        "",
        f"Authorization id: {authorization.authorization_id}",
        f"Created at: {authorization.created_at.isoformat()}",
        f"Operator: {authorization.operator}",
        f"Secondary operator: {authorization.secondary_operator or 'none'}",
        f"Mode: {authorization.mode}",
        f"Broker: {authorization.broker}",
        f"Status: {authorization.status.value}",
        f"Expires at: {authorization.expires_at.isoformat() if authorization.expires_at else 'none'}",
        "",
        "## Reasons",
    ]
    lines.extend(f"- {reason}" for reason in authorization.reasons or ["none"])
    lines.append("")
    lines.append("## Warnings")
    lines.extend(f"- {warning}" for warning in authorization.warnings or ["none"])
    lines.append("")
    return "\n".join(lines)


def _current_session_markdown(session: TradingSessionRecord | None) -> str:
    if session is None:
        return "# Current Session\n\nNo supervised session is currently open.\n"
    lines = [
        "# Current Session",
        "",
        f"Session id: {session.session_id}",
        f"Opened at: {session.opened_at.isoformat()}",
        f"Operator: {session.operator}",
        f"Mode: {session.mode}",
        f"Broker: {session.broker}",
        f"Status: {session.status.value}",
        f"Checklist id: {session.linked_checklist_id or 'none'}",
        f"Authorization id: {session.linked_authorization_id or 'none'}",
        "",
        "## Open Summary",
    ]
    for key, value in sorted(session.open_summary.items()):
        lines.append(f"- {key}: {value}")
    lines.append("")
    if session.unresolved_items:
        lines.append("## Unresolved Items")
        lines.extend(f"- {item}" for item in session.unresolved_items)
        lines.append("")
    return "\n".join(lines)


def _handover_markdown(handover: HandoverRecord | None) -> str:
    if handover is None:
        return "# Latest Handover\n\nNo handover has been recorded yet.\n"
    lines = [
        "# Latest Handover",
        "",
        f"Handover id: {handover.handover_id}",
        f"Created at: {handover.created_at.isoformat()}",
        f"Reviewed at: {handover.reviewed_at.isoformat() if handover.reviewed_at else 'none'}",
        f"Accepted at: {handover.accepted_at.isoformat() if handover.accepted_at else 'none'}",
        f"Status: {handover.status.value}",
        f"Source session: {handover.source_session_id}",
        f"Source operator: {handover.source_operator}",
        f"Target operator: {handover.target_operator or 'unassigned'}",
        f"Target session: {handover.target_session_id or 'unassigned'}",
        f"Live authorization state: {handover.live_authorization_state.value if handover.live_authorization_state else 'none'}",
        "",
        handover.summary,
        "",
        "## Blocked Items",
    ]
    lines.extend(f"- {item}" for item in handover.blocked_items or ["none"])
    lines.append("")
    lines.append("## Recommended Next Steps")
    lines.extend(f"- {item}" for item in handover.recommended_next_steps or ["none"])
    lines.append("")
    if handover.refusal_reason:
        lines.append(f"Refusal reason: {handover.refusal_reason}")
        lines.append("")
    return "\n".join(lines)


def _continuity_markdown(continuity: ContinuityCheckResult | None) -> str:
    if continuity is None:
        return "# Session Continuity Summary\n\nNo continuity evaluation has been generated yet.\n"
    lines = [
        "# Session Continuity Summary",
        "",
        f"Checked at: {continuity.checked_at.isoformat()}",
        f"Latest session id: {continuity.latest_session_id or 'none'}",
        f"Latest handover id: {continuity.latest_handover_id or 'none'}",
        f"Latest handover status: {continuity.latest_handover_status.value if continuity.latest_handover_status else 'none'}",
        "",
        "## Blockers",
    ]
    lines.extend(f"- {item}" for item in continuity.blockers or ["none"])
    lines.append("")
    lines.append("## Warnings")
    lines.extend(f"- {item}" for item in continuity.warnings or ["none"])
    lines.append("")
    lines.append("## Carry-Over Items")
    lines.extend(f"- {item}" for item in continuity.carry_over_items or ["none"])
    lines.append("")
    return "\n".join(lines)


def _blockers_markdown(blockers: list[str]) -> str:
    lines = ["# Outstanding Live Blockers", ""]
    lines.extend(f"- {blocker}" for blocker in blockers or ["none"])
    lines.append("")
    return "\n".join(lines)


def _json_or_empty(model: object | None) -> str:
    if model is None:
        return "{}\n"
    dump = model.model_dump(mode="json") if hasattr(model, "model_dump") else {}
    return json.dumps(dump, indent=2, sort_keys=True) + "\n"
