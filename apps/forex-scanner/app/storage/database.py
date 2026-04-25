"""SQLite persistence for scans, settings snapshots, selected symbols, and backtests."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.audit.integrity import (
    AuditExportPackage,
    AuditIntegrityRecord,
    AuditProtectedRecordType,
    AuditSeal,
    AuditSealTrigger,
    AuditSourceRecordSnapshot,
    AuditVerificationRun,
    build_audit_integrity_record,
    build_audit_seal,
    canonicalize_payload_json,
    compute_payload_hash,
    verify_integrity_records,
)
from app.config.settings import AppSettings
from app.core.types import BacktestResult, MarketRegime, ScanReport, SessionName, SetupFamily, SetupSubtype
from app.execution.models import ExecutionOrder, PaperBlockRecord, TradeEvent
from app.execution.operator_identity import ApprovalSignature, OperatorAuthSession, OperatorAuthSessionStatus, OperatorIdentity, OperatorIdentityStatus
from app.execution.operator_workflows import HandoverRecord, LiveAuthorizationRecord, OperatorActionRecord, PreSessionChecklist, TradingSessionRecord
from app.execution.operations import BrokerHealthSnapshot, BrokerIncident, OperationalAlert, OperationalMetric, OperatorControlState
from app.execution.reconciliation import ReconciliationAnomaly, ReconciliationReport
from app.execution.soak import SoakAnomaly, SoakCampaign, SoakRun, SoakSample
from app.paper.journal import TradeJournalEntry, all_trade_events, journal_entries_from_orders
from app.scoring.empirical import EmpiricalQuery, estimate_empirical_score


class Database:
    """Small SQLite repository used by the local app."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        """Create tables if they do not already exist."""

        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scan_results (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    style TEXT NOT NULL,
                    setup_family TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score REAL NOT NULL,
                    confidence TEXT NOT NULL,
                    entry REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    risk_reward REAL,
                    explanation TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings_snapshots (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    style TEXT NOT NULL,
                    symbols_json TEXT NOT NULL,
                    setup_filter TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    trades_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS selected_symbols (
                    symbol TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_orders (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    setup_family TEXT NOT NULL,
                    setup_subtype TEXT NOT NULL,
                    entry REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    signal_at TEXT,
                    activated_at TEXT,
                    closed_at TEXT,
                    exit_price REAL,
                    remaining_fraction REAL,
                    mae REAL,
                    mfe REAL,
                    realized_r REAL,
                    realized_pnl REAL,
                    partial_exits_json TEXT,
                    execution_assumptions_json TEXT,
                    portfolio_snapshot_json TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_blocks (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    setup_family TEXT NOT NULL,
                    setup_subtype TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    final_score REAL,
                    reasons_json TEXT NOT NULL,
                    portfolio_snapshot_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trade_events (
                    id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trading_journal (
                    id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    setup_family TEXT NOT NULL,
                    setup_subtype TEXT NOT NULL,
                    style TEXT,
                    session TEXT,
                    realized_r REAL,
                    realized_pnl REAL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS broker_orders (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    broker_name TEXT,
                    broker_mode TEXT,
                    broker_order_id TEXT,
                    broker_position_id TEXT,
                    broker_state TEXT,
                    status TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    setup_family TEXT NOT NULL,
                    setup_subtype TEXT NOT NULL,
                    entry REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    filled_quantity REAL,
                    average_fill_price REAL,
                    reconciliation_status TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reconciliation_anomalies (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    anomaly_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    symbol TEXT,
                    internal_order_id TEXT,
                    broker_order_id TEXT,
                    broker_position_id TEXT,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS broker_health_snapshots (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    connected INTEGER NOT NULL,
                    can_trade INTEGER NOT NULL,
                    health_status TEXT NOT NULL,
                    degraded_flags_json TEXT NOT NULL,
                    last_successful_account_sync_at TEXT,
                    last_successful_position_sync_at TEXT,
                    last_successful_reconciliation_at TEXT,
                    kill_switch_active INTEGER,
                    live_capability_enabled INTEGER,
                    active_incidents INTEGER,
                    open_reconciliation_anomalies INTEGER,
                    last_successful_broker_action_at TEXT,
                    last_failed_broker_action_at TEXT,
                    consecutive_failures INTEGER NOT NULL,
                    blocking_incidents INTEGER NOT NULL,
                    manual_intervention_required INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS broker_incidents (
                    id TEXT PRIMARY KEY,
                    opened_at TEXT NOT NULL,
                    updated_at TEXT,
                    closed_at TEXT,
                    resolved_at TEXT,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    symbol TEXT,
                    order_id TEXT,
                    broker_order_id TEXT,
                    reason TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    linked_alert_ids_json TEXT,
                    linked_anomaly_ids_json TEXT,
                    linked_journal_event_ids_json TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operational_metrics (
                    id TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    status TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    dimensions_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operational_alerts (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT,
                    dedupe_key TEXT NOT NULL,
                    suppression_until TEXT,
                    reason TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_controls (
                    id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    broker_submissions_enabled INTEGER NOT NULL,
                    live_submissions_enabled INTEGER NOT NULL,
                    maintenance_mode INTEGER NOT NULL,
                    degraded_mode INTEGER NOT NULL,
                    acknowledged_incident_ids_json TEXT NOT NULL,
                    reason TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pre_session_checklists (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL,
                    acknowledged_at TEXT,
                    linked_campaign_id TEXT,
                    linked_campaign_readiness TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS live_authorizations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    secondary_operator TEXT,
                    mode TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    linked_checklist_id TEXT,
                    linked_campaign_id TEXT,
                    checklist_status TEXT,
                    campaign_readiness TEXT,
                    acknowledged INTEGER NOT NULL,
                    expires_at TEXT,
                    comment TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trading_sessions (
                    id TEXT PRIMARY KEY,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    operator TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    linked_checklist_id TEXT,
                    linked_authorization_id TEXT,
                    handoff_required INTEGER NOT NULL,
                    unresolved_items_json TEXT NOT NULL,
                    notes TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_actions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    operator_id TEXT,
                    operator_display_name TEXT,
                    operator_role TEXT,
                    auth_session_id TEXT,
                    approval_signature_id TEXT,
                    action_type TEXT NOT NULL,
                    result TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    linked_checklist_id TEXT,
                    linked_authorization_id TEXT,
                    linked_session_id TEXT,
                    reason TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS handovers (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    accepted_at TEXT,
                    expires_at TEXT,
                    source_session_id TEXT NOT NULL,
                    target_session_id TEXT,
                    source_operator TEXT NOT NULL,
                    target_operator TEXT,
                    status TEXT NOT NULL,
                    linked_checklist_id TEXT,
                    linked_checklist_status TEXT,
                    refusal_reason TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_identities (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    team TEXT,
                    shift TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_auth_sessions (
                    id TEXT PRIMARY KEY,
                    operator_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    auth_method TEXT NOT NULL,
                    authenticated_at TEXT NOT NULL,
                    last_verified_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    signed_out_at TEXT,
                    team TEXT,
                    shift TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approval_signatures (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    operator_id TEXT NOT NULL,
                    operator_display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    auth_session_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT,
                    requires_reauth INTEGER NOT NULL,
                    expires_at TEXT,
                    linked_checklist_id TEXT,
                    linked_authorization_id TEXT,
                    linked_session_id TEXT,
                    linked_handover_id TEXT,
                    linked_incident_id TEXT,
                    reason TEXT,
                    comment TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_integrity_records (
                    id TEXT PRIMARY KEY,
                    chain_name TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL UNIQUE,
                    captured_at TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    source_version INTEGER NOT NULL,
                    source_created_at TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_size INTEGER NOT NULL,
                    previous_integrity_id TEXT,
                    previous_record_hash TEXT,
                    record_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_seals (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_id TEXT,
                    notes TEXT,
                    start_sequence INTEGER NOT NULL,
                    end_sequence INTEGER NOT NULL,
                    record_count INTEGER NOT NULL,
                    start_integrity_id TEXT NOT NULL,
                    end_integrity_id TEXT NOT NULL,
                    start_record_hash TEXT NOT NULL,
                    end_record_hash TEXT NOT NULL,
                    covered_record_types_json TEXT NOT NULL,
                    seal_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_verification_runs (
                    id TEXT PRIMARY KEY,
                    verified_at TEXT NOT NULL,
                    strict INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    scope_from TEXT,
                    scope_to TEXT,
                    record_types_json TEXT NOT NULL,
                    checked_records INTEGER NOT NULL,
                    source_records_checked INTEGER NOT NULL,
                    missing_source_records INTEGER NOT NULL,
                    altered_source_records INTEGER NOT NULL,
                    missing_integrity_records INTEGER NOT NULL,
                    chain_breaks INTEGER NOT NULL,
                    record_hash_mismatches INTEGER NOT NULL,
                    seal_failures INTEGER NOT NULL,
                    issues_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_export_packages (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    package_hash TEXT NOT NULL,
                    scope_from TEXT,
                    scope_to TEXT,
                    record_types_json TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    seal_count INTEGER NOT NULL,
                    verification_id TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS soak_campaigns (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    target_duration_seconds REAL NOT NULL,
                    mode TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    readiness TEXT,
                    run_ids_json TEXT NOT NULL,
                    samples INTEGER NOT NULL,
                    operator_notes TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS soak_runs (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    mode TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    readiness TEXT,
                    duration_seconds REAL NOT NULL,
                    interval_seconds REAL NOT NULL,
                    samples INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS soak_samples (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sample_index INTEGER NOT NULL,
                    sampled_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    broker TEXT NOT NULL,
                    connected INTEGER NOT NULL,
                    health_status TEXT NOT NULL,
                    account_sync_fresh INTEGER NOT NULL,
                    position_sync_fresh INTEGER NOT NULL,
                    reconciliation_fresh INTEGER NOT NULL,
                    degraded_mode INTEGER NOT NULL,
                    kill_switch_active INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS soak_anomalies (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    sample_index INTEGER,
                    count INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            self._migrate_scan_results(connection)
            self._migrate_paper_orders(connection)
            self._migrate_broker_health_snapshots(connection)
            self._migrate_broker_incidents(connection)
            self._migrate_operator_audit_tables(connection)
            self._bootstrap_audit_integrity(connection)

    def save_scan_report(self, report: ScanReport) -> None:
        """Persist all rows in a scan report."""

        rows = []
        for opportunity in report.opportunities:
            rows.append(
                (
                    str(uuid.uuid4()),
                    report.timestamp.isoformat(),
                    opportunity.symbol,
                    opportunity.style.value,
                    opportunity.setup_family.value,
                    opportunity.regime.value,
                    opportunity.direction.value,
                    opportunity.score,
                    opportunity.confidence.value,
                    opportunity.entry,
                    opportunity.stop_loss,
                    opportunity.take_profit,
                    opportunity.risk_reward,
                    opportunity.explanation,
                    opportunity.setup_subtype.value,
                    opportunity.status.value,
                    opportunity.provider,
                    opportunity.session.value if opportunity.session else None,
                    opportunity.htf_regime.value if opportunity.htf_regime else None,
                    opportunity.entry_regime.value if opportunity.entry_regime else None,
                    opportunity.trigger_regime.value if opportunity.trigger_regime else None,
                    opportunity.technical_score,
                    opportunity.execution_score,
                    opportunity.context_score,
                    opportunity.empirical_score,
                    opportunity.final_score,
                    json.dumps(opportunity.score_components),
                    opportunity.spread,
                    opportunity.atr,
                    json.dumps(opportunity.key_level_distances),
                    opportunity.data_quality.model_dump_json() if opportunity.data_quality else None,
                    opportunity.outcome.value if opportunity.outcome else None,
                    _bool_to_int(opportunity.tp1_hit),
                    _bool_to_int(opportunity.tp2_hit),
                    _bool_to_int(opportunity.tp3_hit),
                    opportunity.mae,
                    opportunity.mfe,
                    opportunity.bars_to_activation,
                    opportunity.bars_to_invalidation,
                    opportunity.bars_to_tp1,
                    opportunity.bars_to_tp2,
                    opportunity.bars_to_tp3,
                    opportunity.model_dump_json(),
                )
            )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO scan_results (
                    id, created_at, symbol, style, setup_family, regime, direction,
                    score, confidence, entry, stop_loss, take_profit, risk_reward, explanation,
                    setup_subtype, status, provider, session, htf_regime, entry_regime, trigger_regime,
                    technical_score, execution_score, context_score, empirical_score, final_score,
                    component_subscores_json, spread, atr, key_level_distances_json, data_quality_json,
                    outcome, tp1_hit, tp2_hit, tp3_hit, mae, mfe, bars_to_activation,
                    bars_to_invalidation, bars_to_tp1, bars_to_tp2, bars_to_tp3, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_settings_snapshot(self, settings: AppSettings) -> str:
        """Store an immutable settings snapshot and return its ID."""

        snapshot_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settings_snapshots (id, created_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (snapshot_id, datetime.now(timezone.utc).isoformat(), settings.model_dump_json()),
            )
        return snapshot_id

    def save_selected_symbols(self, symbols: list[str]) -> None:
        """Persist the current selected symbol universe."""

        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM selected_symbols")
            connection.executemany(
                "INSERT INTO selected_symbols (symbol, updated_at) VALUES (?, ?)",
                [(symbol, timestamp) for symbol in symbols],
            )

    def load_selected_symbols(self) -> list[str]:
        """Return the most recently saved symbol universe."""

        with self._connect() as connection:
            rows = connection.execute("SELECT symbol FROM selected_symbols ORDER BY symbol").fetchall()
        return [str(row["symbol"]) for row in rows]

    def save_backtest_result(self, result: BacktestResult) -> str:
        """Persist a completed backtest run and return its ID."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO backtest_runs (
                    id, created_at, style, symbols_json, setup_filter, start_at, end_at,
                    metrics_json, trades_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.created_at.isoformat(),
                    result.style.value,
                    json.dumps(result.symbols),
                    result.setup_filter.value if hasattr(result.setup_filter, "value") else str(result.setup_filter),
                    result.start.isoformat(),
                    result.end.isoformat(),
                    result.metrics.model_dump_json(),
                    json.dumps([trade.model_dump(mode="json") for trade in result.trades]),
                    result.model_dump_json(),
                ),
            )
        return result.run_id

    def save_paper_orders(self, orders: list[ExecutionOrder]) -> None:
        """Upsert paper orders for local paper-trading inspection."""

        rows = [
            (
                order.order_id,
                order.created_at.isoformat(),
                order.request.symbol,
                order.status.value,
                order.request.direction.value,
                order.request.setup_family.value,
                order.request.setup_subtype.value,
                order.request.entry_price,
                order.request.stop_loss,
                order.request.take_profit,
                order.signal_timestamp.isoformat() if order.signal_timestamp else None,
                order.activated_at.isoformat() if order.activated_at else None,
                order.closed_at.isoformat() if order.closed_at else None,
                order.exit_price,
                order.remaining_fraction,
                order.mae,
                order.mfe,
                order.realized_r,
                order.realized_pnl,
                json.dumps([partial.model_dump(mode="json") for partial in order.partial_exits]),
                json.dumps(order.execution_assumptions),
                json.dumps(order.portfolio_snapshot),
                order.model_dump_json(),
            )
            for order in orders
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO paper_orders (
                    id, created_at, symbol, status, direction, setup_family, setup_subtype,
                    entry, stop_loss, take_profit, signal_at, activated_at, closed_at,
                    exit_price, remaining_fraction, mae, mfe, realized_r, realized_pnl,
                    partial_exits_json, execution_assumptions_json, portfolio_snapshot_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        self.save_trade_events(all_trade_events(orders))

    def load_paper_orders(self) -> list[ExecutionOrder]:
        """Load persisted paper orders."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM paper_orders ORDER BY created_at").fetchall()
        return [ExecutionOrder.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_paper_blocks(self, blocks: list[PaperBlockRecord]) -> None:
        """Persist blocked paper opportunities and their guardrail reasons."""

        rows = [
            (
                block.block_id,
                block.created_at.isoformat(),
                block.symbol,
                block.status,
                block.setup_family,
                block.setup_subtype,
                block.direction,
                block.final_score,
                json.dumps(block.reasons),
                json.dumps(block.portfolio_snapshot),
                block.model_dump_json(),
            )
            for block in blocks
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO paper_blocks (
                    id, created_at, symbol, status, setup_family, setup_subtype,
                    direction, final_score, reasons_json, portfolio_snapshot_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        self.save_trade_events(all_trade_events([], blocks))

    def load_paper_blocks(self) -> list[PaperBlockRecord]:
        """Load persisted paper block records."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM paper_blocks ORDER BY created_at").fetchall()
        return [PaperBlockRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_trade_events(self, events: list[TradeEvent]) -> None:
        """Persist lifecycle/audit events for later reconstruction."""

        rows = [
            (
                event.event_id,
                event.trade_id,
                event.event_type.value,
                event.occurred_at.isoformat(),
                event.symbol,
                event.status,
                event.reason,
                event.model_dump_json(),
            )
            for event in events
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO trade_events (
                    id, trade_id, event_type, occurred_at, symbol, status, reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.TRADE_EVENT,
                        source_record_id=event.event_id,
                        source_created_at=event.occurred_at,
                        payload_json=event.model_dump_json(),
                    )
                    for event in events
                ],
            )

    def load_trade_events(self, trade_id: str | None = None) -> list[TradeEvent]:
        """Load persisted audit events, optionally scoped to one trade id."""

        query = "SELECT payload_json FROM trade_events"
        parameters: tuple[str, ...] = ()
        if trade_id is not None:
            query += " WHERE trade_id = ?"
            parameters = (trade_id,)
        query += " ORDER BY occurred_at"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [TradeEvent.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_journal_entries(self, entries: list[TradeJournalEntry]) -> None:
        """Persist queryable trading journal entries."""

        rows = [
            (
                entry.trade_id,
                entry.signal_id,
                entry.trade_id,
                (entry.signal_timestamp or entry.entry_timestamp or entry.exit_timestamp or datetime.now(timezone.utc)).isoformat(),
                entry.symbol,
                entry.status,
                entry.setup_family,
                entry.setup_subtype,
                entry.style,
                entry.session,
                entry.realized_r_multiple,
                entry.realized_pnl,
                entry.model_dump_json(),
            )
            for entry in entries
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO trading_journal (
                    id, signal_id, trade_id, created_at, symbol, status, setup_family, setup_subtype,
                    style, session, realized_r, realized_pnl, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def rebuild_trading_journal(self) -> list[TradeJournalEntry]:
        """Rebuild and persist journal entries from paper, broker, and block records."""

        entries = journal_entries_from_orders([*self.load_paper_orders(), *self.load_broker_orders()], self.load_paper_blocks())
        self.save_journal_entries(entries)
        return entries

    def load_journal_entries(self) -> list[TradeJournalEntry]:
        """Load persisted trading journal entries."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM trading_journal ORDER BY created_at").fetchall()
        return [TradeJournalEntry.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_broker_orders(self, orders: list[ExecutionOrder]) -> None:
        """Upsert broker-mode orders and their audit events."""

        rows = [
            (
                order.order_id,
                order.created_at.isoformat(),
                order.request.symbol,
                order.broker_name,
                order.broker_mode,
                order.broker_order_id,
                order.broker_position_id,
                order.broker_state.value if order.broker_state else None,
                order.status.value,
                order.request.direction.value,
                order.request.setup_family.value,
                order.request.setup_subtype.value,
                order.request.entry_price,
                order.request.stop_loss,
                order.request.take_profit,
                order.filled_quantity,
                order.average_fill_price,
                order.reconciliation_status,
                order.model_dump_json(),
            )
            for order in orders
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO broker_orders (
                    id, created_at, symbol, broker_name, broker_mode, broker_order_id, broker_position_id,
                    broker_state, status, direction, setup_family, setup_subtype, entry, stop_loss,
                    take_profit, filled_quantity, average_fill_price, reconciliation_status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        self.save_trade_events(all_trade_events(orders))

    def load_broker_orders(self) -> list[ExecutionOrder]:
        """Load persisted broker-mode orders."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM broker_orders ORDER BY created_at").fetchall()
        return [ExecutionOrder.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_reconciliation_report(self, report: ReconciliationReport) -> None:
        """Persist reconciliation anomalies from one reconciliation run."""

        rows = [
            (
                anomaly.anomaly_id,
                report.run_id,
                anomaly.detected_at.isoformat(),
                anomaly.anomaly_type.value,
                anomaly.severity,
                anomaly.symbol,
                anomaly.internal_order_id,
                anomaly.broker_order_id,
                anomaly.broker_position_id,
                anomaly.reason,
                anomaly.model_dump_json(),
            )
            for anomaly in report.anomalies
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO reconciliation_anomalies (
                    id, run_id, detected_at, anomaly_type, severity, symbol, internal_order_id,
                    broker_order_id, broker_position_id, reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_reconciliation_anomalies(self) -> list[ReconciliationAnomaly]:
        """Load persisted reconciliation anomalies."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM reconciliation_anomalies ORDER BY detected_at").fetchall()
        return [ReconciliationAnomaly.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_broker_health_snapshot(self, snapshot: BrokerHealthSnapshot) -> None:
        """Persist one broker operational health snapshot."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO broker_health_snapshots (
                    id, created_at, broker, mode, connected, can_trade, health_status,
                    degraded_flags_json, last_successful_account_sync_at,
                    last_successful_position_sync_at, last_successful_reconciliation_at,
                    kill_switch_active, live_capability_enabled, active_incidents,
                    open_reconciliation_anomalies, last_successful_broker_action_at,
                    last_failed_broker_action_at, consecutive_failures, blocking_incidents,
                    manual_intervention_required, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.created_at.isoformat(),
                    snapshot.broker,
                    snapshot.mode,
                    _bool_to_int(snapshot.connected),
                    _bool_to_int(snapshot.can_trade),
                    snapshot.health_status,
                    json.dumps(snapshot.degraded_flags),
                    snapshot.last_successful_account_sync_at.isoformat() if snapshot.last_successful_account_sync_at else None,
                    snapshot.last_successful_position_sync_at.isoformat() if snapshot.last_successful_position_sync_at else None,
                    snapshot.last_successful_reconciliation_at.isoformat() if snapshot.last_successful_reconciliation_at else None,
                    _bool_to_int(snapshot.kill_switch_active),
                    _bool_to_int(snapshot.live_capability_enabled),
                    snapshot.active_incidents,
                    snapshot.open_reconciliation_anomalies,
                    snapshot.last_successful_broker_action_at.isoformat() if snapshot.last_successful_broker_action_at else None,
                    snapshot.last_failed_broker_action_at.isoformat() if snapshot.last_failed_broker_action_at else None,
                    snapshot.consecutive_failures,
                    snapshot.blocking_incidents,
                    _bool_to_int(snapshot.manual_intervention_required),
                    snapshot.model_dump_json(),
                ),
            )

    def load_broker_health_snapshots(self) -> list[BrokerHealthSnapshot]:
        """Load broker operational health snapshots in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM broker_health_snapshots ORDER BY created_at").fetchall()
        return [BrokerHealthSnapshot.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_broker_incidents(self, incidents: list[BrokerIncident]) -> None:
        """Persist broker operational incidents."""

        rows = [
            (
                incident.incident_id,
                incident.opened_at.isoformat(),
                (incident.updated_at or incident.opened_at).isoformat(),
                incident.closed_at.isoformat() if incident.closed_at else None,
                incident.resolved_at.isoformat() if incident.resolved_at else None,
                incident.category.value,
                incident.severity.value,
                incident.status.value,
                incident.symbol,
                incident.order_id,
                incident.broker_order_id,
                incident.reason,
                incident.recommendation,
                json.dumps(incident.linked_alert_ids),
                json.dumps(incident.linked_anomaly_ids),
                json.dumps(incident.linked_journal_event_ids),
                incident.model_dump_json(),
            )
            for incident in incidents
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO broker_incidents (
                    id, opened_at, updated_at, closed_at, resolved_at, category, severity, status, symbol,
                    order_id, broker_order_id, reason, recommendation, linked_alert_ids_json,
                    linked_anomaly_ids_json, linked_journal_event_ids_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.BROKER_INCIDENT,
                        source_record_id=incident.incident_id,
                        source_created_at=incident.opened_at,
                        payload_json=incident.model_dump_json(),
                    )
                    for incident in incidents
                ],
            )

    def load_broker_incidents(self) -> list[BrokerIncident]:
        """Load persisted broker operational incidents."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM broker_incidents ORDER BY opened_at").fetchall()
        return [BrokerIncident.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_operational_metrics(self, metrics: list[OperationalMetric]) -> None:
        """Persist operational metric samples."""

        rows = [
            (
                metric.metric_id,
                metric.recorded_at.isoformat(),
                metric.name,
                metric.value,
                metric.status,
                metric.broker,
                metric.mode,
                json.dumps(metric.dimensions, sort_keys=True),
                metric.model_dump_json(),
            )
            for metric in metrics
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO operational_metrics (
                    id, recorded_at, name, value, status, broker, mode, dimensions_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_operational_metrics(self) -> list[OperationalMetric]:
        """Load persisted operational metric samples."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM operational_metrics ORDER BY recorded_at").fetchall()
        return [OperationalMetric.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_operational_alerts(self, alerts: list[OperationalAlert]) -> None:
        """Persist local operational alerts."""

        rows = [
            (
                alert.alert_id,
                alert.category.value,
                alert.severity.value,
                alert.status.value,
                alert.opened_at.isoformat(),
                alert.updated_at.isoformat(),
                alert.resolved_at.isoformat() if alert.resolved_at else None,
                alert.dedupe_key,
                alert.suppression_until.isoformat() if alert.suppression_until else None,
                alert.reason,
                alert.recommendation,
                alert.model_dump_json(),
            )
            for alert in alerts
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO operational_alerts (
                    id, category, severity, status, opened_at, updated_at, resolved_at,
                    dedupe_key, suppression_until, reason, recommendation, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_operational_alerts(self) -> list[OperationalAlert]:
        """Load persisted local operational alerts."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM operational_alerts ORDER BY opened_at").fetchall()
        return [OperationalAlert.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_operator_controls(self, controls: OperatorControlState) -> None:
        """Persist the current operator-control state."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO operator_controls (
                    id, updated_at, updated_by, broker_submissions_enabled, live_submissions_enabled,
                    maintenance_mode, degraded_mode, acknowledged_incident_ids_json, reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    controls.control_id,
                    controls.updated_at.isoformat(),
                    controls.updated_by,
                    _bool_to_int(controls.broker_submissions_enabled),
                    _bool_to_int(controls.live_submissions_enabled),
                    _bool_to_int(controls.maintenance_mode),
                    _bool_to_int(controls.degraded_mode),
                    json.dumps(controls.acknowledged_incident_ids),
                    controls.reason,
                    controls.model_dump_json(),
                ),
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.OPERATOR_CONTROL,
                        source_record_id=controls.control_id,
                        source_created_at=controls.updated_at,
                        payload_json=controls.model_dump_json(),
                    )
                ],
            )

    def load_operator_controls(self) -> OperatorControlState:
        """Load operator controls, returning safe defaults if never configured."""

        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM operator_controls WHERE id = 'default'").fetchone()
        if row is None:
            return OperatorControlState(updated_at=datetime.now(timezone.utc))
        return OperatorControlState.model_validate_json(str(row["payload_json"]))

    def sync_operator_identities(self, settings: AppSettings) -> list[OperatorIdentity]:
        """Sync configured local operator identities into persistent storage."""

        existing = {identity.operator_id: identity for identity in self.load_operator_identities()}
        timestamp = datetime.now(timezone.utc)
        identities = [
            OperatorIdentity(
                operator_id=definition.operator_id,
                display_name=definition.display_name,
                role=definition.role,
                status=OperatorIdentityStatus.ACTIVE if definition.active else OperatorIdentityStatus.INACTIVE,
                team=definition.team,
                shift=definition.shift,
                secret_sha256=definition.secret_sha256,
                created_at=existing[definition.operator_id].created_at if definition.operator_id in existing else timestamp,
                updated_at=timestamp,
            )
            for definition in settings.operator_auth.identities
        ]
        self.save_operator_identities(identities)
        return identities

    def save_operator_identities(self, identities: list[OperatorIdentity]) -> None:
        """Persist configured operator identities for local auth and audit."""

        rows = [
            (
                identity.operator_id,
                identity.display_name,
                identity.role.value,
                identity.status.value,
                identity.team,
                identity.shift,
                identity.created_at.isoformat(),
                identity.updated_at.isoformat(),
                identity.model_dump_json(),
            )
            for identity in identities
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO operator_identities (
                    id, display_name, role, status, team, shift, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_operator_identities(self) -> list[OperatorIdentity]:
        """Load configured operator identities in id order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM operator_identities ORDER BY id").fetchall()
        return [OperatorIdentity.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_operator_auth_session(self, auth_session: OperatorAuthSession) -> None:
        """Persist or update one authenticated operator session."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO operator_auth_sessions (
                    id, operator_id, display_name, role, status, auth_method,
                    authenticated_at, last_verified_at, expires_at, signed_out_at,
                    team, shift, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    auth_session.auth_session_id,
                    auth_session.operator_id,
                    auth_session.display_name,
                    auth_session.role.value,
                    auth_session.status.value,
                    auth_session.auth_method,
                    auth_session.authenticated_at.isoformat(),
                    auth_session.last_verified_at.isoformat(),
                    auth_session.expires_at.isoformat(),
                    auth_session.signed_out_at.isoformat() if auth_session.signed_out_at else None,
                    auth_session.team,
                    auth_session.shift,
                    auth_session.model_dump_json(),
                ),
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.OPERATOR_AUTH_SESSION,
                        source_record_id=auth_session.auth_session_id,
                        source_created_at=auth_session.authenticated_at,
                        payload_json=auth_session.model_dump_json(),
                    )
                ],
            )

    def load_operator_auth_sessions(self) -> list[OperatorAuthSession]:
        """Load authenticated operator sessions in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM operator_auth_sessions ORDER BY authenticated_at").fetchall()
        return [OperatorAuthSession.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_latest_operator_auth_session(self, operator_id: str | None = None) -> OperatorAuthSession | None:
        """Return the latest operator auth session, optionally for one operator."""

        with self._connect() as connection:
            if operator_id is None:
                row = connection.execute("SELECT payload_json FROM operator_auth_sessions ORDER BY authenticated_at DESC LIMIT 1").fetchone()
            else:
                row = connection.execute(
                    "SELECT payload_json FROM operator_auth_sessions WHERE operator_id = ? ORDER BY authenticated_at DESC LIMIT 1",
                    (operator_id,),
                ).fetchone()
        if row is None:
            return None
        return OperatorAuthSession.model_validate_json(str(row["payload_json"]))

    def load_active_operator_auth_sessions(self) -> list[OperatorAuthSession]:
        """Return sessions currently stored as active."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM operator_auth_sessions WHERE status = ? ORDER BY authenticated_at",
                (OperatorAuthSessionStatus.ACTIVE.value,),
            ).fetchall()
        return [OperatorAuthSession.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_approval_signature(self, approval: ApprovalSignature) -> None:
        """Persist one sensitive approval signature."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO approval_signatures (
                    id, created_at, operator_id, operator_display_name, role, auth_session_id,
                    action, status, target_type, target_id, requires_reauth, expires_at,
                    linked_checklist_id, linked_authorization_id, linked_session_id,
                    linked_handover_id, linked_incident_id, reason, comment, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.created_at.isoformat(),
                    approval.operator_id,
                    approval.operator_display_name,
                    approval.role.value,
                    approval.auth_session_id,
                    approval.action.value,
                    approval.status.value,
                    approval.target_type,
                    approval.target_id,
                    _bool_to_int(approval.requires_reauth),
                    approval.expires_at.isoformat() if approval.expires_at else None,
                    approval.linked_checklist_id,
                    approval.linked_authorization_id,
                    approval.linked_session_id,
                    approval.linked_handover_id,
                    approval.linked_incident_id,
                    approval.reason,
                    approval.comment,
                    approval.model_dump_json(),
                ),
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.APPROVAL_SIGNATURE,
                        source_record_id=approval.approval_id,
                        source_created_at=approval.created_at,
                        payload_json=approval.model_dump_json(),
                    )
                ],
            )

    def load_approval_signatures(self) -> list[ApprovalSignature]:
        """Load approval signatures in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM approval_signatures ORDER BY created_at").fetchall()
        return [ApprovalSignature.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_pre_session_checklist(self, checklist: PreSessionChecklist) -> None:
        """Persist one operator pre-session checklist."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO pre_session_checklists (
                    id, created_at, operator, mode, broker, status, acknowledged,
                    acknowledged_at, linked_campaign_id, linked_campaign_readiness, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checklist.checklist_id,
                    checklist.created_at.isoformat(),
                    checklist.operator,
                    checklist.mode,
                    checklist.broker,
                    checklist.status.value,
                    _bool_to_int(checklist.acknowledged),
                    checklist.acknowledged_at.isoformat() if checklist.acknowledged_at else None,
                    checklist.linked_campaign_id,
                    checklist.linked_campaign_readiness.value if checklist.linked_campaign_readiness else None,
                    checklist.model_dump_json(),
                ),
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.PRE_SESSION_CHECKLIST,
                        source_record_id=checklist.checklist_id,
                        source_created_at=checklist.created_at,
                        payload_json=checklist.model_dump_json(),
                    )
                ],
            )

    def load_pre_session_checklists(self) -> list[PreSessionChecklist]:
        """Load persisted operator pre-session checklists in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM pre_session_checklists ORDER BY created_at").fetchall()
        return [PreSessionChecklist.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_latest_pre_session_checklist(self) -> PreSessionChecklist | None:
        """Return the most recent operator pre-session checklist, if any."""

        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM pre_session_checklists ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return PreSessionChecklist.model_validate_json(str(row["payload_json"]))

    def save_live_authorization(self, authorization: LiveAuthorizationRecord) -> None:
        """Persist one pre-live authorization record."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO live_authorizations (
                    id, created_at, operator, operator_id, operator_role, auth_session_id,
                    approval_signature_id, secondary_operator, mode, broker, status,
                    linked_checklist_id, linked_campaign_id, checklist_status, campaign_readiness,
                    acknowledged, expires_at, comment, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization.authorization_id,
                    authorization.created_at.isoformat(),
                    authorization.operator,
                    authorization.operator_id,
                    authorization.operator_role.value if authorization.operator_role else None,
                    authorization.auth_session_id,
                    authorization.approval_signature_id,
                    authorization.secondary_operator,
                    authorization.mode,
                    authorization.broker,
                    authorization.status.value,
                    authorization.linked_checklist_id,
                    authorization.linked_campaign_id,
                    authorization.checklist_status.value if authorization.checklist_status else None,
                    authorization.campaign_readiness.value if authorization.campaign_readiness else None,
                    _bool_to_int(authorization.acknowledged),
                    authorization.expires_at.isoformat() if authorization.expires_at else None,
                    authorization.comment,
                    authorization.model_dump_json(),
                ),
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.LIVE_AUTHORIZATION,
                        source_record_id=authorization.authorization_id,
                        source_created_at=authorization.created_at,
                        payload_json=authorization.model_dump_json(),
                    )
                ],
            )

    def load_live_authorizations(self) -> list[LiveAuthorizationRecord]:
        """Load persisted pre-live authorization records in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM live_authorizations ORDER BY created_at").fetchall()
        return [LiveAuthorizationRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_latest_live_authorization(self) -> LiveAuthorizationRecord | None:
        """Return the most recent pre-live authorization record, if any."""

        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM live_authorizations ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return LiveAuthorizationRecord.model_validate_json(str(row["payload_json"]))

    def save_trading_session(self, session: TradingSessionRecord) -> None:
        """Persist or update one operator trading session record."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trading_sessions (
                    id, opened_at, closed_at, operator, mode, broker, status,
                    linked_checklist_id, linked_authorization_id, handoff_required,
                    unresolved_items_json, notes, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.opened_at.isoformat(),
                    session.closed_at.isoformat() if session.closed_at else None,
                    session.operator,
                    session.mode,
                    session.broker,
                    session.status.value,
                    session.linked_checklist_id,
                    session.linked_authorization_id,
                    _bool_to_int(session.handoff_required),
                    json.dumps(session.unresolved_items),
                    session.notes,
                    session.model_dump_json(),
                ),
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.TRADING_SESSION,
                        source_record_id=session.session_id,
                        source_created_at=session.opened_at,
                        payload_json=session.model_dump_json(),
                    )
                ],
            )

    def load_trading_sessions(self) -> list[TradingSessionRecord]:
        """Load persisted operator trading sessions in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM trading_sessions ORDER BY opened_at").fetchall()
        return [TradingSessionRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_open_trading_session(self) -> TradingSessionRecord | None:
        """Return the latest still-open trading session, if any."""

        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM trading_sessions WHERE closed_at IS NULL ORDER BY opened_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return TradingSessionRecord.model_validate_json(str(row["payload_json"]))

    def save_operator_actions(self, actions: list[OperatorActionRecord]) -> None:
        """Persist queryable operator action records."""

        rows = [
            (
                action.action_id,
                action.created_at.isoformat(),
                action.operator,
                action.operator_id,
                action.operator_display_name,
                action.operator_role.value if action.operator_role else None,
                action.auth_session_id,
                action.approval_signature_id,
                action.action_type.value,
                action.result.value,
                action.mode,
                action.target_type,
                action.target_id,
                action.linked_checklist_id,
                action.linked_authorization_id,
                action.linked_session_id,
                action.reason,
                action.model_dump_json(),
            )
            for action in actions
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO operator_actions (
                    id, created_at, operator, operator_id, operator_display_name, operator_role,
                    auth_session_id, approval_signature_id, action_type, result, mode, target_type,
                    target_id, linked_checklist_id, linked_authorization_id,
                    linked_session_id, reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.OPERATOR_ACTION,
                        source_record_id=action.action_id,
                        source_created_at=action.created_at,
                        payload_json=action.model_dump_json(),
                    )
                    for action in actions
                ],
            )

    def load_operator_actions(self) -> list[OperatorActionRecord]:
        """Load persisted operator action records in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM operator_actions ORDER BY created_at").fetchall()
        return [OperatorActionRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_handover(self, handover: HandoverRecord) -> None:
        """Persist or update one inter-session handover record."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO handovers (
                    id, created_at, reviewed_at, accepted_at, expires_at,
                    source_session_id, target_session_id, source_operator,
                    target_operator, status, linked_checklist_id,
                    linked_checklist_status, acceptance_signature_id, refusal_reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handover.handover_id,
                    handover.created_at.isoformat(),
                    handover.reviewed_at.isoformat() if handover.reviewed_at else None,
                    handover.accepted_at.isoformat() if handover.accepted_at else None,
                    handover.expires_at.isoformat() if handover.expires_at else None,
                    handover.source_session_id,
                    handover.target_session_id,
                    handover.source_operator,
                    handover.target_operator,
                    handover.status.value,
                    handover.linked_checklist_id,
                    handover.linked_checklist_status.value if handover.linked_checklist_status else None,
                    handover.acceptance_signature_id,
                    handover.refusal_reason,
                    handover.model_dump_json(),
                ),
            )
            self._append_audit_integrity_records(
                connection,
                [
                    AuditSourceRecordSnapshot(
                        record_type=AuditProtectedRecordType.HANDOVER,
                        source_record_id=handover.handover_id,
                        source_created_at=handover.created_at,
                        payload_json=handover.model_dump_json(),
                    )
                ],
            )

    def load_handovers(self) -> list[HandoverRecord]:
        """Load handovers in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM handovers ORDER BY created_at").fetchall()
        return [HandoverRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_latest_handover(self) -> HandoverRecord | None:
        """Return the most recent handover, if any."""

        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM handovers ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return HandoverRecord.model_validate_json(str(row["payload_json"]))

    def load_handover(self, handover_id: str) -> HandoverRecord | None:
        """Load one handover by id."""

        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if row is None:
            return None
        return HandoverRecord.model_validate_json(str(row["payload_json"]))

    def load_audit_integrity_records(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        record_types: list[AuditProtectedRecordType] | None = None,
        include_boundary: bool = False,
    ) -> list[AuditIntegrityRecord]:
        """Load append-only audit integrity records, optionally scoped by time and type."""

        with self._connect() as connection:
            rows = self._load_audit_integrity_rows(
                connection,
                start=start,
                end=end,
                record_types=record_types,
                include_boundary=include_boundary,
            )
        return [AuditIntegrityRecord.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_latest_audit_integrity_record(self) -> AuditIntegrityRecord | None:
        """Return the latest integrity-chain record, if any."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM audit_integrity_records ORDER BY sequence_number DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return AuditIntegrityRecord.model_validate_json(str(row["payload_json"]))

    def save_audit_seal(self, seal: AuditSeal) -> None:
        """Persist one integrity checkpoint/seal."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO audit_seals (
                    id, created_at, trigger_type, trigger_id, notes, start_sequence, end_sequence,
                    record_count, start_integrity_id, end_integrity_id, start_record_hash,
                    end_record_hash, covered_record_types_json, seal_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seal.seal_id,
                    seal.created_at.isoformat(),
                    seal.trigger_type.value,
                    seal.trigger_id,
                    seal.notes,
                    seal.start_sequence,
                    seal.end_sequence,
                    seal.record_count,
                    seal.start_integrity_id,
                    seal.end_integrity_id,
                    seal.start_record_hash,
                    seal.end_record_hash,
                    json.dumps([record_type.value for record_type in seal.covered_record_types]),
                    seal.seal_hash,
                    seal.model_dump_json(),
                ),
            )

    def load_audit_seals(
        self,
        *,
        trigger_type: AuditSealTrigger | None = None,
        trigger_id: str | None = None,
    ) -> list[AuditSeal]:
        """Load stored audit integrity seals."""

        query = "SELECT payload_json FROM audit_seals"
        clauses: list[str] = []
        parameters: list[object] = []
        if trigger_type is not None:
            clauses.append("trigger_type = ?")
            parameters.append(trigger_type.value)
        if trigger_id is not None:
            clauses.append("trigger_id = ?")
            parameters.append(trigger_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return [AuditSeal.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_audit_verification_run(self, verification: AuditVerificationRun) -> None:
        """Persist one audit integrity verification result."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO audit_verification_runs (
                    id, verified_at, strict, status, scope_from, scope_to, record_types_json,
                    checked_records, source_records_checked, missing_source_records,
                    altered_source_records, missing_integrity_records, chain_breaks,
                    record_hash_mismatches, seal_failures, issues_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verification.verification_id,
                    verification.verified_at.isoformat(),
                    _bool_to_int(verification.strict),
                    verification.status.value,
                    verification.scope_from.isoformat() if verification.scope_from else None,
                    verification.scope_to.isoformat() if verification.scope_to else None,
                    json.dumps([record_type.value for record_type in verification.record_types]),
                    verification.checked_records,
                    verification.source_records_checked,
                    verification.missing_source_records,
                    verification.altered_source_records,
                    verification.missing_integrity_records,
                    verification.chain_breaks,
                    verification.record_hash_mismatches,
                    verification.seal_failures,
                    json.dumps([issue.model_dump(mode="json") for issue in verification.issues]),
                    verification.model_dump_json(),
                ),
            )

    def load_audit_verification_runs(self) -> list[AuditVerificationRun]:
        """Load stored audit verification history."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM audit_verification_runs ORDER BY verified_at").fetchall()
        return [AuditVerificationRun.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_latest_audit_verification(self) -> AuditVerificationRun | None:
        """Return the latest audit verification run, if any."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM audit_verification_runs ORDER BY verified_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return AuditVerificationRun.model_validate_json(str(row["payload_json"]))

    def save_audit_export_package(self, export_package: AuditExportPackage) -> None:
        """Persist one audit evidence export-package summary."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO audit_export_packages (
                    id, created_at, output_dir, manifest_path, package_hash, scope_from, scope_to,
                    record_types_json, record_count, seal_count, verification_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_package.export_id,
                    export_package.created_at.isoformat(),
                    export_package.output_dir,
                    export_package.manifest_path,
                    export_package.package_hash,
                    export_package.scope_from.isoformat() if export_package.scope_from else None,
                    export_package.scope_to.isoformat() if export_package.scope_to else None,
                    json.dumps([record_type.value for record_type in export_package.record_types]),
                    export_package.record_count,
                    export_package.seal_count,
                    export_package.verification_id,
                    export_package.model_dump_json(),
                ),
            )

    def load_audit_export_packages(self) -> list[AuditExportPackage]:
        """Load stored audit evidence export-package history."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM audit_export_packages ORDER BY created_at").fetchall()
        return [AuditExportPackage.model_validate_json(str(row["payload_json"])) for row in rows]

    def create_audit_seal(
        self,
        *,
        trigger_type: AuditSealTrigger,
        trigger_id: str | None = None,
        notes: str | None = None,
        start_sequence: int | None = None,
        end_sequence: int | None = None,
        now: datetime | None = None,
    ) -> AuditSeal | None:
        """Create and persist a seal for the latest unsealed or explicit chain range."""

        with self._connect() as connection:
            if trigger_id is not None:
                existing = connection.execute(
                    "SELECT payload_json FROM audit_seals WHERE trigger_type = ? AND trigger_id = ? ORDER BY created_at DESC LIMIT 1",
                    (trigger_type.value, trigger_id),
                ).fetchone()
                if existing is not None:
                    return AuditSeal.model_validate_json(str(existing["payload_json"]))
            latest_row = connection.execute(
                "SELECT sequence_number FROM audit_integrity_records ORDER BY sequence_number DESC LIMIT 1"
            ).fetchone()
            if latest_row is None:
                return None
            effective_end = end_sequence or int(latest_row["sequence_number"])
            if start_sequence is None:
                last_seal = connection.execute(
                    "SELECT end_sequence FROM audit_seals ORDER BY end_sequence DESC LIMIT 1"
                ).fetchone()
                effective_start = int(last_seal["end_sequence"]) + 1 if last_seal is not None else 1
            else:
                effective_start = start_sequence
            if effective_start > effective_end:
                return None
            rows = connection.execute(
                """
                SELECT payload_json FROM audit_integrity_records
                WHERE sequence_number >= ? AND sequence_number <= ?
                ORDER BY sequence_number
                """,
                (effective_start, effective_end),
            ).fetchall()
            records = [AuditIntegrityRecord.model_validate_json(str(row["payload_json"])) for row in rows]
            seal = build_audit_seal(records, trigger_type=trigger_type, trigger_id=trigger_id, notes=notes, created_at=now)
            if seal is None:
                return None
            connection.execute(
                """
                INSERT OR REPLACE INTO audit_seals (
                    id, created_at, trigger_type, trigger_id, notes, start_sequence, end_sequence,
                    record_count, start_integrity_id, end_integrity_id, start_record_hash,
                    end_record_hash, covered_record_types_json, seal_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seal.seal_id,
                    seal.created_at.isoformat(),
                    seal.trigger_type.value,
                    seal.trigger_id,
                    seal.notes,
                    seal.start_sequence,
                    seal.end_sequence,
                    seal.record_count,
                    seal.start_integrity_id,
                    seal.end_integrity_id,
                    seal.start_record_hash,
                    seal.end_record_hash,
                    json.dumps([record_type.value for record_type in seal.covered_record_types]),
                    seal.seal_hash,
                    seal.model_dump_json(),
                ),
            )
            return seal

    def verify_audit_integrity(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        record_types: list[AuditProtectedRecordType] | None = None,
        strict: bool = True,
        save_result: bool = True,
    ) -> AuditVerificationRun:
        """Run integrity verification against the append-only audit chain and current source records."""

        with self._connect() as connection:
            rows = self._load_audit_integrity_rows(connection, start=start, end=end, record_types=None, include_boundary=True)
            records = [AuditIntegrityRecord.model_validate_json(str(row["payload_json"])) for row in rows]
            visible_ids = {
                record.integrity_id
                for record in records
                if (start is None or record.captured_at >= start)
                and (end is None or record.captured_at <= end)
                and (record_types is None or record.record_type in record_types)
            }
            current_sources = self._load_current_audit_source_snapshots(
                connection,
                record_types=record_types,
                start=start,
                end=end,
            )
            seals = self._load_audit_seal_rows(connection, start=start, end=end)
            verification = verify_integrity_records(
                records,
                current_source_records=current_sources,
                seals=seals,
                strict=strict,
                verified_at=datetime.now(timezone.utc),
                scope_from=start,
                scope_to=end,
                record_types=record_types,
                visible_integrity_ids=visible_ids,
            )
            if save_result:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO audit_verification_runs (
                        id, verified_at, strict, status, scope_from, scope_to, record_types_json,
                        checked_records, source_records_checked, missing_source_records,
                        altered_source_records, missing_integrity_records, chain_breaks,
                        record_hash_mismatches, seal_failures, issues_json, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verification.verification_id,
                        verification.verified_at.isoformat(),
                        _bool_to_int(verification.strict),
                        verification.status.value,
                        verification.scope_from.isoformat() if verification.scope_from else None,
                        verification.scope_to.isoformat() if verification.scope_to else None,
                        json.dumps([record_type.value for record_type in verification.record_types]),
                        verification.checked_records,
                        verification.source_records_checked,
                        verification.missing_source_records,
                        verification.altered_source_records,
                        verification.missing_integrity_records,
                        verification.chain_breaks,
                        verification.record_hash_mismatches,
                        verification.seal_failures,
                        json.dumps([issue.model_dump(mode="json") for issue in verification.issues]),
                        verification.model_dump_json(),
                    ),
                )
        return verification

    def save_soak_campaign(self, campaign: SoakCampaign) -> None:
        """Persist or update a multi-session soak campaign."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO soak_campaigns (
                    id, name, started_at, ended_at, target_duration_seconds, mode, broker,
                    status, readiness, run_ids_json, samples, operator_notes, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign.campaign_id,
                    campaign.name,
                    campaign.started_at.isoformat(),
                    campaign.ended_at.isoformat() if campaign.ended_at else None,
                    campaign.target_duration_seconds,
                    campaign.mode,
                    campaign.broker,
                    campaign.status.value,
                    campaign.readiness.value if campaign.readiness else None,
                    json.dumps(campaign.run_ids),
                    campaign.samples,
                    campaign.operator_notes,
                    campaign.model_dump_json(),
                ),
            )

    def load_soak_campaigns(self) -> list[SoakCampaign]:
        """Load all soak campaigns in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM soak_campaigns ORDER BY started_at").fetchall()
        return [SoakCampaign.model_validate_json(str(row["payload_json"])) for row in rows]

    def load_soak_campaign(self, campaign_id: str) -> SoakCampaign | None:
        """Load one soak campaign by id."""

        with self._connect() as connection:
            row = connection.execute("SELECT payload_json FROM soak_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if row is None:
            return None
        return SoakCampaign.model_validate_json(str(row["payload_json"]))

    def load_running_soak_campaign(self, name: str | None = None) -> SoakCampaign | None:
        """Return the most recent running campaign, optionally by name."""

        with self._connect() as connection:
            if name is None:
                row = connection.execute("SELECT payload_json FROM soak_campaigns WHERE status = 'running' ORDER BY started_at DESC LIMIT 1").fetchone()
            else:
                row = connection.execute("SELECT payload_json FROM soak_campaigns WHERE status = 'running' AND name = ? ORDER BY started_at DESC LIMIT 1", (name,)).fetchone()
        if row is None:
            return None
        return SoakCampaign.model_validate_json(str(row["payload_json"]))

    def save_soak_run(self, run: SoakRun) -> None:
        """Persist or update a soak-validation run."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO soak_runs (
                    id, started_at, ended_at, mode, broker, status, readiness,
                    duration_seconds, interval_seconds, samples, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.started_at.isoformat(),
                    run.ended_at.isoformat() if run.ended_at else None,
                    run.mode,
                    run.broker,
                    run.status.value,
                    run.readiness.value if run.readiness else None,
                    run.duration_seconds,
                    run.interval_seconds,
                    run.samples,
                    run.model_dump_json(),
                ),
            )

    def load_soak_runs(self) -> list[SoakRun]:
        """Load soak-validation runs in chronological order."""

        with self._connect() as connection:
            rows = connection.execute("SELECT payload_json FROM soak_runs ORDER BY started_at").fetchall()
        return [SoakRun.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_soak_samples(self, samples: list[SoakSample]) -> None:
        """Persist soak-validation samples."""

        rows = [
            (
                sample.sample_id,
                sample.run_id,
                sample.sample_index,
                sample.sampled_at.isoformat(),
                sample.mode,
                sample.broker,
                _bool_to_int(sample.connected),
                sample.health_status,
                _bool_to_int(sample.account_sync_fresh),
                _bool_to_int(sample.position_sync_fresh),
                _bool_to_int(sample.reconciliation_fresh),
                _bool_to_int(sample.degraded_mode),
                _bool_to_int(sample.kill_switch_active),
                sample.model_dump_json(),
            )
            for sample in samples
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO soak_samples (
                    id, run_id, sample_index, sampled_at, mode, broker, connected,
                    health_status, account_sync_fresh, position_sync_fresh,
                    reconciliation_fresh, degraded_mode, kill_switch_active, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_soak_samples(self, run_id: str | None = None) -> list[SoakSample]:
        """Load soak-validation samples, optionally for one run."""

        with self._connect() as connection:
            if run_id is None:
                rows = connection.execute("SELECT payload_json FROM soak_samples ORDER BY sampled_at, sample_index").fetchall()
            else:
                rows = connection.execute("SELECT payload_json FROM soak_samples WHERE run_id = ? ORDER BY sample_index", (run_id,)).fetchall()
        return [SoakSample.model_validate_json(str(row["payload_json"])) for row in rows]

    def save_soak_anomalies(self, anomalies: list[SoakAnomaly]) -> None:
        """Persist soak anomalies."""

        rows = [
            (
                anomaly.anomaly_id,
                anomaly.run_id,
                anomaly.detected_at.isoformat(),
                anomaly.category.value,
                anomaly.severity,
                anomaly.sample_index,
                anomaly.count,
                anomaly.reason,
                anomaly.recommendation,
                anomaly.model_dump_json(),
            )
            for anomaly in anomalies
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO soak_anomalies (
                    id, run_id, detected_at, category, severity, sample_index,
                    count, reason, recommendation, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_soak_anomalies(self, run_id: str | None = None) -> list[SoakAnomaly]:
        """Load soak anomalies, optionally for one run."""

        with self._connect() as connection:
            if run_id is None:
                rows = connection.execute("SELECT payload_json FROM soak_anomalies ORDER BY detected_at").fetchall()
            else:
                rows = connection.execute("SELECT payload_json FROM soak_anomalies WHERE run_id = ? ORDER BY detected_at", (run_id,)).fetchall()
        return [SoakAnomaly.model_validate_json(str(row["payload_json"])) for row in rows]

    def lookup_empirical_score(
        self,
        symbol: str,
        style: str,
        family: SetupFamily,
        subtype: SetupSubtype,
        session: SessionName,
        regime: MarketRegime,
        minimum_samples: int,
        neutral_score: float,
        min_condition_samples: int = 4,
        shrinkage_samples: int = 36,
        max_adjustment: float = 18.0,
    ) -> float:
        """Estimate historical setup quality from persisted realized outcomes."""

        estimate = estimate_empirical_score(
            records=self._load_empirical_records(),
            query=EmpiricalQuery(
                symbol=symbol,
                style=style,
                family=family.value,
                subtype=subtype.value,
                session=session.value,
                regime=regime.value,
            ),
            neutral_score=neutral_score,
            minimum_samples=minimum_samples,
            min_condition_samples=min_condition_samples,
            shrinkage_samples=shrinkage_samples,
            max_adjustment=max_adjustment,
        )
        return estimate.score

    def _bootstrap_audit_integrity(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT COUNT(*) AS count FROM audit_integrity_records").fetchone()
        if row is not None and int(row["count"]) > 0:
            return
        self._append_audit_integrity_records(connection, self._load_current_audit_source_snapshots(connection))

    def _append_audit_integrity_records(
        self,
        connection: sqlite3.Connection,
        snapshots: list[AuditSourceRecordSnapshot],
    ) -> None:
        if not snapshots:
            return
        latest_chain_row = connection.execute(
            "SELECT payload_json FROM audit_integrity_records ORDER BY sequence_number DESC LIMIT 1"
        ).fetchone()
        previous_record = (
            AuditIntegrityRecord.model_validate_json(str(latest_chain_row["payload_json"]))
            if latest_chain_row is not None
            else None
        )
        next_sequence = previous_record.sequence_number if previous_record is not None else 0
        source_cache: dict[tuple[str, str], tuple[str, int]] = {}
        rows: list[tuple[object, ...]] = []
        for snapshot in sorted(
            snapshots,
            key=lambda item: (item.source_created_at, item.record_type.value, item.source_record_id),
        ):
            key = (snapshot.record_type.value, snapshot.source_record_id)
            if key not in source_cache:
                source_row = connection.execute(
                    """
                    SELECT payload_hash, source_version
                    FROM audit_integrity_records
                    WHERE record_type = ? AND source_record_id = ?
                    ORDER BY sequence_number DESC LIMIT 1
                    """,
                    key,
                ).fetchone()
                if source_row is None:
                    source_cache[key] = ("", 0)
                else:
                    source_cache[key] = (str(source_row["payload_hash"]), int(source_row["source_version"]))
            last_payload_hash, last_version = source_cache[key]
            canonical_payload = canonicalize_payload_json(snapshot.payload_json)
            payload_hash = compute_payload_hash(canonical_payload)
            if payload_hash == last_payload_hash:
                continue
            next_sequence += 1
            record = build_audit_integrity_record(
                record_type=snapshot.record_type,
                source_record_id=snapshot.source_record_id,
                source_created_at=snapshot.source_created_at,
                payload_json=canonical_payload,
                sequence_number=next_sequence,
                source_version=last_version + 1,
                previous_integrity_id=previous_record.integrity_id if previous_record else None,
                previous_record_hash=previous_record.record_hash if previous_record else None,
            )
            rows.append(
                (
                    record.integrity_id,
                    record.chain_name,
                    record.sequence_number,
                    record.captured_at.isoformat(),
                    record.record_type.value,
                    record.source_record_id,
                    record.source_version,
                    record.source_created_at.isoformat(),
                    record.payload_hash,
                    record.payload_size,
                    record.previous_integrity_id,
                    record.previous_record_hash,
                    record.record_hash,
                    record.model_dump_json(),
                )
            )
            source_cache[key] = (record.payload_hash, record.source_version)
            previous_record = record
        if not rows:
            return
        connection.executemany(
            """
            INSERT INTO audit_integrity_records (
                id, chain_name, sequence_number, captured_at, record_type, source_record_id,
                source_version, source_created_at, payload_hash, payload_size,
                previous_integrity_id, previous_record_hash, record_hash, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _load_audit_integrity_rows(
        self,
        connection: sqlite3.Connection,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        record_types: list[AuditProtectedRecordType] | None = None,
        include_boundary: bool = False,
    ) -> list[sqlite3.Row]:
        query = "SELECT payload_json FROM audit_integrity_records"
        clauses: list[str] = []
        parameters: list[object] = []
        if start is not None:
            clauses.append("captured_at >= ?")
            parameters.append(start.isoformat())
        if end is not None:
            clauses.append("captured_at <= ?")
            parameters.append(end.isoformat())
        if record_types:
            placeholders = ", ".join("?" for _ in record_types)
            clauses.append(f"record_type IN ({placeholders})")
            parameters.extend(record_type.value for record_type in record_types)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY sequence_number"
        rows = connection.execute(query, tuple(parameters)).fetchall()
        if include_boundary and start is not None:
            boundary = connection.execute(
                """
                SELECT payload_json FROM audit_integrity_records
                WHERE captured_at < ?
                ORDER BY sequence_number DESC LIMIT 1
                """,
                (start.isoformat(),),
            ).fetchone()
            if boundary is not None:
                return [boundary, *rows]
        return rows

    def _load_audit_seal_rows(
        self,
        connection: sqlite3.Connection,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[AuditSeal]:
        if start is None and end is None:
            rows = connection.execute("SELECT payload_json FROM audit_seals ORDER BY created_at").fetchall()
            return [AuditSeal.model_validate_json(str(row["payload_json"])) for row in rows]
        query = "SELECT MIN(sequence_number) AS min_sequence, MAX(sequence_number) AS max_sequence FROM audit_integrity_records"
        clauses: list[str] = []
        parameters: list[object] = []
        if start is not None:
            clauses.append("captured_at >= ?")
            parameters.append(start.isoformat())
        if end is not None:
            clauses.append("captured_at <= ?")
            parameters.append(end.isoformat())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        sequence_row = connection.execute(query, tuple(parameters)).fetchone()
        if sequence_row is None or sequence_row["min_sequence"] is None or sequence_row["max_sequence"] is None:
            return []
        rows = connection.execute(
            """
            SELECT payload_json FROM audit_seals
            WHERE end_sequence >= ? AND start_sequence <= ?
            ORDER BY created_at
            """,
            (int(sequence_row["min_sequence"]), int(sequence_row["max_sequence"])),
        ).fetchall()
        return [AuditSeal.model_validate_json(str(row["payload_json"])) for row in rows]

    def _load_current_audit_source_snapshots(
        self,
        connection: sqlite3.Connection,
        *,
        record_types: list[AuditProtectedRecordType] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[AuditSourceRecordSnapshot]:
        requested_types = set(record_types or [])
        snapshots: list[AuditSourceRecordSnapshot] = []
        for record_type, table_name, id_column, time_column in _AUDIT_SOURCE_TABLE_SPECS:
            if requested_types and record_type not in requested_types:
                continue
            query = f"SELECT {id_column} AS source_id, {time_column} AS source_created_at, payload_json FROM {table_name}"
            clauses: list[str] = []
            parameters: list[object] = []
            if start is not None:
                clauses.append(f"{time_column} >= ?")
                parameters.append(start.isoformat())
            if end is not None:
                clauses.append(f"{time_column} <= ?")
                parameters.append(end.isoformat())
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            rows = connection.execute(query, tuple(parameters)).fetchall()
            snapshots.extend(
                AuditSourceRecordSnapshot(
                    record_type=record_type,
                    source_record_id=str(row["source_id"]),
                    source_created_at=_parse_datetime(row["source_created_at"]),
                    payload_json=str(row["payload_json"]),
                )
                for row in rows
            )
        return snapshots

    def _load_empirical_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        with self._connect() as connection:
            for row in connection.execute("SELECT trades_json FROM backtest_runs").fetchall():
                try:
                    payload = json.loads(str(row["trades_json"]))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, list):
                    records.extend(item for item in payload if isinstance(item, dict))
            for row in connection.execute("SELECT payload_json FROM scan_results WHERE outcome IS NOT NULL OR payload_json LIKE '%\"outcome\"%'").fetchall():
                try:
                    payload = json.loads(str(row["payload_json"]))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
            for row in connection.execute("SELECT payload_json FROM paper_orders WHERE realized_r IS NOT NULL").fetchall():
                try:
                    payload = json.loads(str(row["payload_json"]))
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(_paper_payload_to_empirical_record(payload))
        return records

    def _migrate_scan_results(self, connection: sqlite3.Connection) -> None:
        columns = _table_columns(connection, "scan_results")
        migrations = {
            "setup_subtype": "TEXT",
            "status": "TEXT",
            "provider": "TEXT",
            "session": "TEXT",
            "htf_regime": "TEXT",
            "entry_regime": "TEXT",
            "trigger_regime": "TEXT",
            "technical_score": "REAL",
            "execution_score": "REAL",
            "context_score": "REAL",
            "empirical_score": "REAL",
            "final_score": "REAL",
            "component_subscores_json": "TEXT",
            "spread": "REAL",
            "atr": "REAL",
            "key_level_distances_json": "TEXT",
            "data_quality_json": "TEXT",
            "outcome": "TEXT",
            "tp1_hit": "INTEGER",
            "tp2_hit": "INTEGER",
            "tp3_hit": "INTEGER",
            "mae": "REAL",
            "mfe": "REAL",
            "bars_to_activation": "INTEGER",
            "bars_to_invalidation": "INTEGER",
            "bars_to_tp1": "INTEGER",
            "bars_to_tp2": "INTEGER",
            "bars_to_tp3": "INTEGER",
        }
        for column, column_type in migrations.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE scan_results ADD COLUMN {column} {column_type}")

    def _migrate_paper_orders(self, connection: sqlite3.Connection) -> None:
        columns = _table_columns(connection, "paper_orders")
        migrations = {
            "signal_at": "TEXT",
            "activated_at": "TEXT",
            "closed_at": "TEXT",
            "exit_price": "REAL",
            "remaining_fraction": "REAL",
            "mae": "REAL",
            "mfe": "REAL",
            "partial_exits_json": "TEXT",
            "execution_assumptions_json": "TEXT",
            "portfolio_snapshot_json": "TEXT",
        }
        for column, column_type in migrations.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE paper_orders ADD COLUMN {column} {column_type}")

    def _migrate_broker_incidents(self, connection: sqlite3.Connection) -> None:
        columns = _table_columns(connection, "broker_incidents")
        migrations = {
            "updated_at": "TEXT",
            "resolved_at": "TEXT",
            "linked_alert_ids_json": "TEXT",
            "linked_anomaly_ids_json": "TEXT",
            "linked_journal_event_ids_json": "TEXT",
        }
        for column, column_type in migrations.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE broker_incidents ADD COLUMN {column} {column_type}")

    def _migrate_broker_health_snapshots(self, connection: sqlite3.Connection) -> None:
        columns = _table_columns(connection, "broker_health_snapshots")
        migrations = {
            "kill_switch_active": "INTEGER",
            "live_capability_enabled": "INTEGER",
            "active_incidents": "INTEGER",
            "open_reconciliation_anomalies": "INTEGER",
            "last_successful_broker_action_at": "TEXT",
            "last_failed_broker_action_at": "TEXT",
        }
        for column, column_type in migrations.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE broker_health_snapshots ADD COLUMN {column} {column_type}")

    def _migrate_operator_audit_tables(self, connection: sqlite3.Connection) -> None:
        operator_action_columns = _table_columns(connection, "operator_actions")
        operator_action_migrations = {
            "operator_id": "TEXT",
            "operator_display_name": "TEXT",
            "operator_role": "TEXT",
            "auth_session_id": "TEXT",
            "approval_signature_id": "TEXT",
        }
        for column, column_type in operator_action_migrations.items():
            if column not in operator_action_columns:
                connection.execute(f"ALTER TABLE operator_actions ADD COLUMN {column} {column_type}")

        live_authorization_columns = _table_columns(connection, "live_authorizations")
        live_authorization_migrations = {
            "operator_id": "TEXT",
            "operator_role": "TEXT",
            "auth_session_id": "TEXT",
            "approval_signature_id": "TEXT",
        }
        for column, column_type in live_authorization_migrations.items():
            if column not in live_authorization_columns:
                connection.execute(f"ALTER TABLE live_authorizations ADD COLUMN {column} {column_type}")

        handover_columns = _table_columns(connection, "handovers")
        handover_migrations = {
            "acceptance_signature_id": "TEXT",
        }
        for column, column_type in handover_migrations.items():
            if column not in handover_columns:
                connection.execute(f"ALTER TABLE handovers ADD COLUMN {column} {column_type}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def initialize_database(path: Path) -> Database:
    """Initialize and return the SQLite database repository."""

    return Database(path)


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _parse_datetime(value: object) -> datetime:
    text = str(value)
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


_AUDIT_SOURCE_TABLE_SPECS: tuple[tuple[AuditProtectedRecordType, str, str, str], ...] = (
    (AuditProtectedRecordType.TRADE_EVENT, "trade_events", "id", "occurred_at"),
    (AuditProtectedRecordType.BROKER_INCIDENT, "broker_incidents", "id", "opened_at"),
    (AuditProtectedRecordType.OPERATOR_CONTROL, "operator_controls", "id", "updated_at"),
    (AuditProtectedRecordType.PRE_SESSION_CHECKLIST, "pre_session_checklists", "id", "created_at"),
    (AuditProtectedRecordType.LIVE_AUTHORIZATION, "live_authorizations", "id", "created_at"),
    (AuditProtectedRecordType.TRADING_SESSION, "trading_sessions", "id", "opened_at"),
    (AuditProtectedRecordType.OPERATOR_ACTION, "operator_actions", "id", "created_at"),
    (AuditProtectedRecordType.HANDOVER, "handovers", "id", "created_at"),
    (AuditProtectedRecordType.OPERATOR_AUTH_SESSION, "operator_auth_sessions", "id", "authenticated_at"),
    (AuditProtectedRecordType.APPROVAL_SIGNATURE, "approval_signatures", "id", "created_at"),
)


def _paper_payload_to_empirical_record(payload: dict[str, object]) -> dict[str, object]:
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    request_payload: dict[str, object] = request if isinstance(request, dict) else {}
    return {
        "symbol": request_payload.get("symbol", payload.get("symbol", "")),
        "style": request_payload.get("style", ""),
        "setup_family": request_payload.get("setup_family", ""),
        "setup_subtype": request_payload.get("setup_subtype", "none"),
        "direction": request_payload.get("direction", ""),
        "session": request_payload.get("session", ""),
        "status": payload.get("status", "paper"),
        "net_r": payload.get("realized_r"),
        "mae": payload.get("mae"),
        "mfe": payload.get("mfe"),
        "tp1_hit": payload.get("tp1_exit_price") is not None,
        "tp2_hit": payload.get("tp2_exit_price") is not None,
        "tp3_hit": payload.get("tp3_exit_price") is not None,
    }
