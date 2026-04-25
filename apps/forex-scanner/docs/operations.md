# Supervised Operations

The broker path is designed for controlled paper/sandbox/live-disabled evaluation first. `paper` remains the default mode, and `broker_live` requires explicit config and environment gates.

## Operational Model

Each broker recovery or monitoring pass records:

- health snapshots,
- operational metrics,
- alerts,
- incidents,
- reconciliation anomalies,
- journal events.

Health snapshots track broker connectivity, account and position sync freshness, reconciliation freshness, degraded flags, kill switch status, live capability state, active incidents, and recent broker action timestamps.

Metrics are local SQLite samples such as `broker_connected`, `health_check_ok`, `account_sync_success`, `position_sync_success`, `order_submission_attempts`, `broker_rejects`, `retry_exhausted`, `live_guardrail_triggers`, and `manual_intervention_required`.

Alerts are local structured objects with severity, category, dedupe key, suppression window, active/resolved status, runbook reference, linked incident/anomaly ids, and recommendations. Local file routing is enabled by default. Webhook routing is optional, disabled by default, and fails closed when the endpoint environment variable is missing.

Incidents are operator-facing objects with category, severity, timestamps, status, recommendation, linked anomalies, linked alerts, and broker/order identifiers when available.

Operator controls are persisted in SQLite and make the current operating posture explicit:

- maintenance mode blocks broker submissions until an operator clears it,
- degraded mode records reduced-confidence operation and appears in resume-readiness reports,
- broker submissions can be paused independently of strategy scans,
- live submissions have a separate gate and remain disabled by default,
- incident acknowledgements are recorded for audit and review.

Operator identity is intentionally lightweight and local-first:

- local operator identities are defined in config with `operator_id`, display name, role, status, and optional team/shift metadata,
- sign-in creates a local authenticated operator session with expiry,
- higher-risk actions can require explicit re-auth inside that session,
- sensitive approvals are stored as signed approval records linked to the operator identity, role, auth session, and target object.

Resume readiness is computed from the latest health snapshot, unresolved incidents, active alerts, reconciliation anomalies, and operator controls. It returns one of:

- `safe_to_resume`: no blocking operational issues are currently known,
- `degraded_but_safe`: operation can continue with extra supervision,
- `blocked_pending_manual_review`: new broker submissions should not proceed.

When enabled, local Prometheus textfile-style metrics are exported to `reports/broker/forex_scanner.prom`. This is a local handoff format for supervised monitoring stacks; no third-party alerting service is required or configured by default.

The Prometheus textfile exporter uses stable, low-cardinality labels:

- `execution_mode`
- `broker_adapter`
- `severity`
- `category`
- `state`

It intentionally avoids per-order and per-trade-id labels. Symbol-level labels are also avoided in the operational exporter to prevent cardinality growth during long supervised runs.

Dashboard-ready Grafana JSON lives at `docs/dashboards/forex_scanner_ops_grafana.json`. It is designed around the Prometheus textfile metric names and includes panels for broker connectivity, account/position/reconciliation freshness, reconciliation anomalies, active alerts/incidents, retry/reject trends, guardrail triggers, degraded mode, kill-switch state, live submission failures, recovery activity, exporter health, and a soak-readiness proxy.

Alert-rule evaluation lives in `scripts/alert_rules.py`. It reads persisted health snapshots, metrics, incidents, reconciliation anomalies, orders, and operator controls, then writes operator summaries under `reports/alerts/` by default. The rule layer covers broker availability, stale account/position/reconciliation syncs, severe reconciliation anomalies, repeated rejects, retry exhaustion, manual-intervention state, kill switch activation, prolonged degraded mode, guardrail spikes, and live submission failures.

Soak validation is the long-run version of the same operational model. It repeatedly records health snapshots, account/position sync freshness, reconciliation status, alerts, incidents, operator-control state, and readiness. It produces a conservative recommendation:

- `pass`: the run stayed within configured operational thresholds,
- `pass_with_warnings`: the run was usable for continued supervised evaluation but needs review,
- `fail`: the run exceeded hard reliability thresholds or ended with serious unresolved issues.

A passing soak run is not a go-live switch. It is evidence for operator review only.

Multi-session soak campaigns aggregate several soak runs across hours or days. Campaign readiness uses a stricter rating:

- `not_ready`: blocking operational reliability issues remain,
- `limited_ready`: suitable only for continued supervised validation with reduced scope,
- `supervised_ready`: campaign stayed inside configured thresholds and can be reviewed for controlled broker-supervised next steps.

Campaign readiness is never an autonomous approval switch. It requires manual review, a fresh recovery/reconciliation pass, clean operator controls, and all broker-live gates before any broker-live supervised action.

## Safe Commands

```powershell
python scripts/operator_session.py --operator-id operator sign-in --secret operator-pass
python scripts/broker_check.py --mode broker_sandbox --provider mock
python scripts/broker_recovery.py --mode broker_sandbox --provider mock
python scripts/broker_monitor.py --mode broker_sandbox --provider mock --iterations 3 --interval-seconds 5
python scripts/broker_control.py
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> checklist --acknowledge
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> open --acknowledge-checklist --confirm
python scripts/metrics_export.py --check
python scripts/alert_rules.py --out reports/alerts --route
python scripts/soak_test.py --mode broker_sandbox --provider mock --duration-minutes 15 --interval-seconds 60
python scripts/soak_campaign.py start --name weekly-sandbox --mode broker_sandbox --provider mock --target-hours 168
python scripts/soak_campaign.py run-session --name weekly-sandbox --provider mock --duration-minutes 30 --interval-seconds 60
python scripts/soak_campaign.py finalize --campaign-id <campaign_id>
python scripts/backup_recovery.py --db data/forex_scanner.sqlite create --out backups/local --report-out reports/backup_recovery --label manual-checkpoint
python scripts/backup_recovery.py --db data/forex_scanner.sqlite verify --backup backups/local/<backup_id>.zip --out reports/backup_recovery
python scripts/broker_report.py --out reports/broker
python scripts/operator_session.py status --out reports/operator
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> sign-out
```

For MT5 sandbox checks:

```powershell
python scripts/broker_check.py --mode broker_sandbox
python scripts/broker_recovery.py --mode broker_sandbox
```

## Reports To Inspect

- `broker_health.md` and `broker_health_history.csv`
- `alert_summary.md`, `alert_summary.csv`, `alert_summary.json`
- `incident_report.md`, `incident_report.csv`, `incident_report.json`
- `operational_metrics.csv`
- `restart_recovery.md`
- `reconciliation_anomalies.csv`
- `broker_reliability.md`
- `long_term_reliability.md`
- `alert_aging.csv`
- `unresolved_incidents.csv`
- `operator_controls.csv`
- `resume_readiness.md`
- `reports/operator/latest_checklist.md`
- `reports/operator/current_session.md`
- `reports/operator/latest_authorization.md`
- `reports/operator/latest_handover.md`
- `reports/operator/continuity_summary.md`
- `reports/operator/outstanding_live_blockers.md`
- `reports/operator/session_history.csv`
- `reports/operator/handover_history.csv`
- `reports/operator/pending_handovers.csv`
- `reports/operator/carry_over_items.csv`
- `reports/operator/open_risk_items.csv`
- `reports/operator/operator_actions.csv`
- `reports/operator/unresolved_handoffs.csv`
- `reports/operator/operator_identities.csv`
- `reports/operator/active_operator_sessions.csv`
- `reports/operator/approval_history.csv`
- `reports/operator/approval_signatures_by_action.csv`
- `reports/operator/expired_approvals.csv`
- `reports/operator/denied_privileged_actions.csv`
- `reports/operator/reauth_events.csv`
- `reports/operator/identity_audit_summary.md`
- `forex_scanner.prom`
- `docs/dashboards/forex_scanner_ops_grafana.json`
- `reports/alerts/summary.md`
- `reports/alerts/rule_evaluations.csv`
- `reports/alerts/active_alerts.csv`
- `reports/alerts/routing_failures.csv`
- `reports/alerts/alert_events.jsonl`
- `daily_operational_summary.md`
- `manual_intervention.csv`
- `reports/soak/<run_id>/summary.md`
- `reports/soak/<run_id>/readiness.md`
- `reports/soak/<run_id>/reliability.md`
- `reports/soak/<run_id>/samples.csv`
- `reports/soak/<run_id>/anomalies.md`
- `reports/soak_campaigns/<campaign_id>/campaign_summary.md`
- `reports/soak_campaigns/<campaign_id>/weekly_reliability.md`
- `reports/soak_campaigns/<campaign_id>/readiness.md`
- `reports/soak_campaigns/<campaign_id>/recurring_anomalies.md`
- `reports/soak_campaigns/<campaign_id>/campaign_timeline.csv`

## Operator Controls

Examples:

```powershell
python scripts/broker_control.py --operator-id supervisor --auth-session-id <auth_session_id> --maintenance on --reason "investigating reconciliation mismatch"
python scripts/broker_control.py --operator-id supervisor --auth-session-id <auth_session_id> --broker-submissions off --reason "pause after repeated rejects"
python scripts/broker_control.py --operator-id supervisor --auth-session-id <auth_session_id> --degraded on --reason "MT5 connection was recently unstable"
python scripts/broker_control.py --operator-id supervisor --auth-session-id <auth_session_id> --broker-submissions on --live-submissions on --reason "recovery checks clean" --reauth-secret supervisor-pass
```

Do not enable live submissions just because the CLI allows it. Live mode still requires the separate config and environment gates, a clean recovery/reconciliation pass, and operator review of unresolved incidents and alerts.

## Pre-Session Workflow

Run the checklist and acknowledge it explicitly:

```powershell
python scripts/operator_session.py --operator-id operator sign-in --secret operator-pass
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> checklist --acknowledge
```

The checklist records pass/warning/fail outcomes for execution mode, broker connectivity, account/position/reconciliation freshness, unresolved incidents, severe alerts, degraded mode, kill switch state, data quality, spread sanity, guardrail config, monitoring/exporter state, and campaign readiness.

Warnings do not automatically block session open. Failures do.

If the previous supervised session ended with unresolved carry-over state, the next operator may also need an accepted handover before a new session can open.

## Pre-Live Authorization

Before any `broker_live` submission path can proceed, record a manual authorization:

```powershell
python scripts/operator_session.py --mode broker_live --operator-id supervisor sign-in --secret supervisor-pass
python scripts/operator_session.py --mode broker_live --operator-id supervisor --auth-session-id <auth_session_id> authorize-live --acknowledge-checklist --confirm --comment "manual review complete" --reauth-secret supervisor-pass
```

Authorization is denied when:

- the checklist still has required blockers,
- the checklist has not been acknowledged when config requires acknowledgement,
- resume readiness is blocked,
- campaign readiness is below the configured minimum,
- a pending, refused, or expired handover still blocks continuity into the next sensitive workflow,
- broker-live config gates are not enabled,
- dual confirmation is required but not supplied,
- no authenticated supervisor/admin session exists,
- the required comment or re-authentication step is missing.

Granted authorizations expire automatically after the configured window and are checked again during broker-live submission preflight.

## Session Procedures

Open a supervised session:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> open --acknowledge-checklist --confirm --comment "London supervised session"
```

Close a session:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> close --comment "clean close"
```

Force handoff-required close when unresolved state must be passed forward:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> close --handoff-required --comment "open broker state left for next shift"
```

Explicit handover workflows:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> handover-create --comment "carry-over items summarized for next shift"
python scripts/operator_session.py --operator-id supervisor --auth-session-id <auth_session_id> handover-accept --acknowledge --comment "carry-over reviewed and accepted" --reauth-secret supervisor-pass
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> handover-refuse --reason "fresh reconciliation required before takeover"
```

The operator workflow persists:

- operator sign-in/sign-out and re-auth records,
- checklist acknowledgements,
- session-open and blocked-open attempts,
- live authorization grants and denials,
- session-close records,
- handoff-required records,
- handover creation, acceptance, and refusal records,
- manual-intervention completed records,
- resume-after-incident approvals,
- approval signatures linked to authenticated operator sessions.

## Handover And Continuity

Handover packages summarize:

- the source session and operator,
- the latest checklist status,
- the latest health snapshot timing,
- unresolved incidents, alerts, and reconciliation anomalies,
- open positions/orders,
- live authorization state,
- degraded-mode and kill-switch state,
- pending manual actions and recommended next steps.

Session continuation is blocked conservatively when:

- the previous session is still open,
- the previous session required handoff but no handover exists,
- the latest handover is still pending, refused, or expired,
- open orders/positions have not been acknowledged through an accepted handover,
- broker health or reconciliation have not been refreshed after the latest accepted handover,
- a broker-live authorization predates the latest session transition and has not been re-reviewed.

## Soak Validation

Recommended progression:

```powershell
python scripts/soak_test.py --mode paper --duration-minutes 5 --interval-seconds 30
python scripts/soak_test.py --mode broker_sandbox --provider mock --duration-minutes 15 --interval-seconds 60
python scripts/soak_test.py --mode broker_sandbox --duration-minutes 60 --interval-seconds 60
```

Interpretation:

- `pass`: continue supervised validation, still review reports manually,
- `pass_with_warnings`: extend the run or resolve warnings before increasing scope,
- `fail`: do not resume broker submissions until issues are understood and recovery is clean.

The most important files are `readiness.md`, `reliability.md`, `anomalies.md`, `samples.csv`, `health_timeline.csv`, `reconciliation_timeline.csv`, and `unresolved_issues.csv`.

## Multi-Session Soak Campaigns

Use campaigns when the operator wants weekly-style evidence over repeated sessions instead of one isolated run.

Start a safe campaign:

```powershell
python scripts/soak_campaign.py start --name weekly-sandbox --mode broker_sandbox --provider mock --target-hours 168
```

Run or resume one session:

```powershell
python scripts/soak_campaign.py run-session --name weekly-sandbox --provider mock --duration-minutes 30 --interval-seconds 60
```

Attach an existing run manually:

```powershell
python scripts/soak_campaign.py attach-run --campaign-id <campaign_id> --run-id <run_id>
```

Stop without final readiness:

```powershell
python scripts/soak_campaign.py stop --campaign-id <campaign_id> --reason "operator paused validation"
```

Finalize and generate reports:

```powershell
python scripts/soak_campaign.py finalize --campaign-id <campaign_id> --out reports/soak_campaigns
```

Campaign reports include:

- `campaign_summary.md`: target vs observed duration, mode, broker, run count, and headline reliability
- `weekly_reliability.md`: uptime/health proxy, degraded percentage, sync reliability, retries/rejects, recovery stats, and alert/incident burden
- `readiness.md`: `not_ready`, `limited_ready`, or `supervised_ready` with blocking issues, warnings, and next actions
- `recurring_anomalies.md`: isolated vs recurring/worsening/clustered issues
- `campaign_timeline.csv`: sample-level timeline across sessions
- `restart_recovery_events.csv`: recovery/check samples during the campaign
- `unresolved_issues.csv`: active alerts, open incidents, soak anomalies, and recurring issues

Readiness interpretation:

- `not_ready`: do not proceed toward serious broker-live supervised checks; resolve blockers and rerun.
- `limited_ready`: continue sandbox/paper validation with reduced scope and longer observation.
- `supervised_ready`: evidence is strong enough for manual operator review, not for unattended automation.

## External Metrics Export

Manual export:

```powershell
python scripts/metrics_export.py --check
python scripts/metrics_export.py --stdout --check
```

Default output:

```text
reports/broker/forex_scanner.prom
```

Representative metric families:

- `forex_scanner_execution_mode`
- `forex_scanner_broker_connected`
- `forex_scanner_broker_connectivity_failures_total`
- `forex_scanner_account_sync_fresh`
- `forex_scanner_position_sync_fresh`
- `forex_scanner_reconciliation_fresh`
- `forex_scanner_reconciliation_anomalies_active`
- `forex_scanner_operational_alerts_active`
- `forex_scanner_operational_incidents_active`
- `forex_scanner_broker_rejects_total`
- `forex_scanner_broker_retry_attempts_total`
- `forex_scanner_broker_retries_exhausted_total`
- `forex_scanner_stale_state_detections_total`
- `forex_scanner_live_guardrail_triggers_total`
- `forex_scanner_manual_intervention_required`
- `forex_scanner_kill_switch_active`
- `forex_scanner_operator_degraded_mode`
- `forex_scanner_live_submission_attempts_total`
- `forex_scanner_live_submission_failures_total`
- `forex_scanner_recovery_actions_total`
- `forex_scanner_last_successful_broker_action_timestamp_seconds`
- `forex_scanner_last_failed_broker_action_timestamp_seconds`

Prometheus can consume this via a local textfile collector pattern. Grafana dashboards should start with connectivity, account/position sync freshness, reconciliation anomalies, active alerts/incidents, retry exhaustion, stale-state detections, kill-switch state, and readiness/soak report outputs.

## Dashboards And Alert Rules

Dashboard artifact:

```text
docs/dashboards/forex_scanner_ops_grafana.json
```

Alert evaluation:

```powershell
python scripts/alert_rules.py --db data/forex_scanner.sqlite --out reports/alerts
python scripts/alert_rules.py --db data/forex_scanner.sqlite --out reports/alerts --route
```

`--route` appends structured alert delivery records to `reports/alerts/alert_events.jsonl` when `monitoring.alert_local_sink_enabled=true`. Webhook routing remains off unless `monitoring.alert_webhook_enabled=true` and the environment variable named by `monitoring.alert_webhook_url_env` is present.

Default webhook variable:

```powershell
$env:FOREX_SCANNER_ALERT_WEBHOOK_URL="https://example.invalid/operator-alert-hook"
```

Leave webhook routing disabled until the receiving endpoint, retry expectations, and operator response process are reviewed. Missing webhook config produces a local failed-delivery record instead of blocking monitoring or broker safety checks.

Rule outputs to inspect:

- `summary.md`: counts of active, resolved, suppressed, and failed delivery records
- `rule_evaluations.csv`: every rule, threshold, observed value, trigger state, and linked incident/anomaly ids
- `active_alerts.csv`: active alerts with recommendations and runbook references
- `suppressed_alerts.csv`: deduplicated alerts inside the suppression window
- `resolved_alerts.csv`: rules that cleared since the previous pass
- `routing_failures.csv`: webhook/local delivery failures

## Live Gating

`broker_live` remains blocked unless all of these are true:

- `execution_capabilities.broker_live_enabled=true`
- `broker.live_enabled=true`
- `FOREX_SCANNER_BROKER_LIVE_CONFIRM=ENABLE_LIVE_TRADING`
- kill switch is not active
- broker validation passes
- recovery/reconciliation has no high or critical blockers
- no active high or critical operational alerts

## Audit Retention And Archives

Operational records are retained in SQLite by default and archived into local ZIP evidence packages when they exceed the configured retention windows. The archive workflow is intentionally local-first and non-destructive: protected database rows are copied into reviewable packages, but active audit/journal rows are not purged unless future supervised maintenance explicitly adds that capability.

Retention policy lives under `retention_archive` in settings:

- `audit_records_retention_days`
- `journal_events_retention_days`
- `alerts_incidents_retention_days`
- `monitoring_snapshots_retention_days`
- `soak_campaign_retention_days`
- `reports_exports_retention_days`
- `checkpoint_seals_retention_days`
- `report_file_size_rotation_mb`
- `archive_output_dir`
- `restore_output_dir`
- `allow_database_purge`
- `allow_file_rotation`

Default archive destination:

```text
archives/operational
```

Default restore-for-review destination:

```text
archives/restore_review
```

Evaluate pending archival candidates:

```powershell
python scripts/archive_records.py candidates --db data/forex_scanner.sqlite --out reports/archives
```

Create and verify an archive package:

```powershell
python scripts/archive_records.py create --db data/forex_scanner.sqlite --out archives/operational --report-out reports/archives
python scripts/archive_records.py verify --archive archives/operational/<archive_id>.zip --out reports/archives
```

Run the daily manual archive/seal workflow:

```powershell
python scripts/archive_records.py daily --db data/forex_scanner.sqlite --out archives/operational --report-out reports/archives
```

Restore an archive for review without touching active state:

```powershell
python scripts/archive_records.py restore-review --archive archives/operational/<archive_id>.zip --restore-dir archives/restore_review --out reports/archives
```

Inspect archive reports:

- `reports/archives/summary.md`
- `reports/archives/retention_policy.md`
- `reports/archives/pending_archival_candidates.csv`
- `reports/archives/archive_inventory.csv`
- `reports/archives/rotation_plan.md`
- `reports/archives/archive_verification.md`
- `reports/archives/archive_restore.md`
- `reports/backup_recovery/summary.md`
- `reports/backup_recovery/backup_inventory.csv`
- `reports/backup_recovery/backup_coverage.md`
- `reports/backup_recovery/backup_verification.md`
- `reports/backup_recovery/backup_restore.md`
- `reports/backup_recovery/recovery_validation_status.md`
- `backups/local/<backup_id>.zip`
- `backups/restore_review/<backup_id>/`

Archive packages include:

- `manifest.json`
- `manifest.sha256`
- retained record JSON/CSV files under `records/`
- preserved audit seals and integrity verification output
- archived report/export files under `files/` when filesystem archival is enabled
- sidecar `.manifest.json` and `.sha256` files beside the ZIP for quick inspection

Rotation is conservative. `rotation-plan` identifies rows/files that exceed policy, explains what can be archived, and records blocked actions. Active database purge and file movement are disabled by default so archival cannot silently weaken audit integrity.

## Local Backup And Disaster Recovery

Backups are separate from archives. Archives preserve older evidence for retention and review. Backups preserve the current critical operational state so an operator can recover after local failure, corruption, or maintenance.

Backup policy lives under `backup_recovery` in settings:

- `backup_output_dir`
- `restore_review_dir`
- `pre_restore_backup_dir`
- `report_output_dir`
- `recovery_state_path`
- `include_database`
- `include_config_snapshot`
- `include_archive_manifests`
- `include_critical_reports`
- `compression`
- `retention_count`
- `retention_days`
- `require_audit_verification_before_backup`
- `verify_after_backup`
- `verify_before_restore`
- `allow_active_restore`
- `startup_recovery_validation_required`
- `block_sensitive_actions_until_recovery_validation`

Default backup destination:

```text
backups/local
```

Default non-destructive restore-review destination:

```text
backups/restore_review
```

Create a structured backup package:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite create --out backups/local --report-out reports/backup_recovery --label operator-checkpoint --reason "manual checkpoint"
```

The package contains:

- `manifest.json`
- `manifest.sha256`
- `README.md`
- `state/forex_scanner.sqlite`
- `integrity/audit_integrity.json`
- `config/settings_snapshot.json` when enabled
- `archives/archive_manifest_inventory.json` and sidecar manifests when enabled
- sidecar `.manifest.json` and `.sha256` files beside the ZIP

Verify and inspect a backup:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite list --out backups/local --report-out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite inspect --backup backups/local/<backup_id>.zip --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite verify --backup backups/local/<backup_id>.zip --out reports/backup_recovery
```

Restore to staging/review without touching active state:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite restore-review --backup backups/local/<backup_id>.zip --restore-dir backups/restore_review --out reports/backup_recovery
```

Explicit active restore is gated and disabled by default. Use it only after package verification and staging review:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite restore-active --backup backups/local/<backup_id>.zip --target-db data/forex_scanner.sqlite --allow-active-restore --confirm-active-restore --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite post-restore-check --out reports/backup_recovery
```

Active restore creates a pre-restore safety copy when an active database already exists, writes a `post_restore_validation` recovery state, and blocks sensitive actions until post-restore validation passes.

Continuity modes:

- `normal`: recovery validation passed and sensitive actions are not blocked by restore state
- `degraded`: validation passed with warnings that need operator review
- `restore_review`: a backup was restored into staging for review only
- `post_restore_validation`: an active restore has occurred and validation is still pending
- `blocked_pending_operator_review`: recovery validation failed or an unsafe restore attempt was blocked

Inspect disaster-recovery reports:

- `reports/backup_recovery/summary.md`
- `reports/backup_recovery/backup_policy.md`
- `reports/backup_recovery/backup_inventory.csv`
- `reports/backup_recovery/backup_inventory.json`
- `reports/backup_recovery/backup_coverage.md`
- `reports/backup_recovery/backup_verification.md`
- `reports/backup_recovery/backup_restore.md`
- `reports/backup_recovery/recovery_validation_status.md`

After restore or crash recovery, run post-restore validation before considering any broker-live path. If recovery validation fails, keep live submissions blocked, inspect the backup package and active database, then rerun audit integrity and broker reconciliation from a clean operator session.

## Limitations

This is not production-grade autonomous execution. It lacks high-availability deployment, broker-certified reconciliation, enterprise identity, off-machine immutable storage, and independent risk oversight. Local archives and backups improve evidence preservation and local recovery, but they are not a substitute for external compliance-grade retention or off-machine disaster recovery.
