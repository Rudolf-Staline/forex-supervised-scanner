# Broker Operations Runbooks

These runbooks are for supervised `broker_sandbox` and gated `broker_live` usage. They do not authorize unattended trading.

## Safe Baseline

Symptom: Before any supervised broker work.

Checks:

```powershell
python scripts/operator_session.py --operator-id operator sign-in --secret operator-pass
python scripts/broker_check.py --mode broker_sandbox --provider mock
python scripts/broker_recovery.py --mode broker_sandbox --provider mock
python scripts/broker_control.py
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> checklist --acknowledge
python scripts/metrics_export.py --check
python scripts/alert_rules.py --out reports/alerts
python scripts/soak_test.py --mode broker_sandbox --provider mock --duration-minutes 15 --interval-seconds 60
python scripts/soak_campaign.py start --name weekly-sandbox --mode broker_sandbox --provider mock --target-hours 168
python scripts/broker_check.py --mode broker_sandbox
python scripts/broker_recovery.py --mode broker_sandbox
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> sign-out
```

Safe recovery steps:

- Confirm `broker_health.md`, `incident_report.md`, `alert_summary.md`, and `restart_recovery.md`.
- Confirm `resume_readiness.md` is `safe_to_resume` or an explicitly reviewed `degraded_but_safe`.
- Confirm `operator_controls.csv` does not show maintenance mode or broker submissions disabled unless intentionally paused.
- Continue only if there are no active high/critical alerts or blocking incidents.
- Use dry-run broker submit first.

Stop condition: Any critical alert, manual intervention, unknown broker state, or severe reconciliation mismatch.

## Pre-Session Checklist

Symptom: Operator is about to start a supervised broker session.

Checks:

```powershell
python scripts/operator_session.py --operator-id operator sign-in --secret operator-pass
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> checklist --acknowledge
python scripts/operator_session.py status --out reports/operator
```

Safe recovery steps:

- Inspect `reports/operator/latest_checklist.md`.
- Do not proceed if required checklist items failed.
- Resolve any checklist blocker related to connectivity, stale syncs, reconciliation, severe alerts/incidents, degraded mode, kill switch, or campaign readiness.
- If the previous session ended with carry-over state, inspect `reports/operator/latest_handover.md` and `reports/operator/continuity_summary.md` before opening the next session.

Stop condition: Checklist status is `fail`.

## Pre-Live Authorization

Symptom: Operator wants to allow controlled `broker_live` submission review.

Checks:

```powershell
python scripts/operator_session.py --mode broker_live --operator-id supervisor sign-in --secret supervisor-pass
python scripts/operator_session.py --mode broker_live --operator-id supervisor --auth-session-id <auth_session_id> authorize-live --acknowledge-checklist --confirm --comment "manual review complete" --reauth-secret supervisor-pass
python scripts/operator_session.py status --out reports/operator
```

Safe recovery steps:

- Confirm the latest checklist is acknowledged.
- Confirm `reports/operator/latest_authorization.md` shows `granted`.
- Confirm campaign readiness meets the configured minimum when live authorization requires it.
- Confirm there is no pending, refused, or expired handover still blocking continuity.
- Confirm the authorization has not expired before any broker-live submit attempt.
- Confirm the approval is attributable to an authenticated supervisor/admin session.

Stop condition: Authorization is denied, expired, or checklist/readiness blockers remain.

## Session Open Procedure

Symptom: Operator is ready to begin a supervised trading session.

Checks:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> open --acknowledge-checklist --confirm --comment "London supervised session"
```

Safe recovery steps:

- Confirm the session-open workflow refreshed broker/account/position/reconciliation state when in broker mode.
- Inspect `reports/operator/current_session.md`.
- Inspect `reports/operator/continuity_summary.md` if the previous session was handed off.
- Confirm `reports/operator/outstanding_live_blockers.md` is empty before any live-supervised broker path is considered.

Stop condition: Session open is blocked due to checklist blockers, missing acknowledgement, missing confirmation, or live-authorization blockers.

## Session Close Procedure

Symptom: Operator is ending a supervised session.

Checks:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> close --comment "clean close"
```

If unresolved state must be handed forward:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> close --handoff-required --comment "open broker state left for next shift"
```

Safe recovery steps:

- Inspect `reports/operator/session_history.csv`.
- Inspect `reports/operator/unresolved_handoffs.csv`.
- If handoff is required, document the unresolved broker/orders/incident/anomaly state before leaving the system.
- If handoff is required, confirm a handover package was created and inspect `reports/operator/latest_handover.md`.

Stop condition: Do not mark a session as cleanly closed while unresolved anomalies, severe incidents, or open broker state still require review.

## Handover Review And Acceptance

Symptom: One operator is transferring responsibility to the next session or next operator.

Checks:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> handover-create --comment "carry-over items summarized for next shift"
python scripts/operator_session.py status --out reports/operator
python scripts/operator_session.py --operator-id supervisor --auth-session-id <auth_session_id> handover-accept --acknowledge --comment "carry-over reviewed and accepted" --reauth-secret supervisor-pass
```

If the next operator cannot safely take over:

```powershell
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> handover-refuse --reason "fresh reconciliation required before takeover"
```

Safe recovery steps:

- Inspect `reports/operator/latest_handover.md`.
- Inspect `reports/operator/continuity_summary.md`.
- Review `reports/operator/pending_handovers.csv`, `reports/operator/carry_over_items.csv`, and `reports/operator/open_risk_items.csv`.
- Accept the handover only after unresolved blockers, warnings, and open exposures are understood.
- For severe carry-over risk, require the signed approval trail in `reports/operator/approval_history.csv`.
- Rerun broker recovery/reconciliation before reopening a supervised session when the continuity summary says health or reconciliation are stale after handover.

Stop condition: Do not open a new supervised session or pursue live authorization while the latest handover is pending, refused, expired, or still requires a fresh health/reconciliation refresh.

## Metrics Export Check

Symptom: Operator wants to verify that external monitoring can read local health state.

Checks:

```powershell
python scripts/metrics_export.py --check
python scripts/metrics_export.py --stdout --check
```

Safe recovery steps:

- Confirm `reports/broker/forex_scanner.prom` exists.
- Confirm metrics include `forex_scanner_broker_connected`, `forex_scanner_operational_alerts_active`, and `forex_scanner_reconciliation_fresh`.
- If using node-exporter textfile collection, point the collector at the directory containing the `.prom` file.

Stop condition: Do not trust dashboards if `metrics_export.py --check` fails or the metrics file is stale relative to the latest recovery/monitoring run.

## Alert Rules And Routing Check

Symptom: Operator wants to verify that metrics become actionable local alerts.

Checks:

```powershell
python scripts/alert_rules.py --db data/forex_scanner.sqlite --out reports/alerts
python scripts/alert_rules.py --db data/forex_scanner.sqlite --out reports/alerts --route
```

Safe recovery steps:

- Inspect `reports/alerts/rule_evaluations.csv` for each threshold and observed value.
- Inspect `reports/alerts/active_alerts.csv` for recommendations and runbook references.
- Inspect `reports/alerts/suppressed_alerts.csv` before assuming repeated alerts disappeared.
- Inspect `reports/alerts/routing_failures.csv` if webhook routing is enabled.
- Confirm `reports/alerts/alert_events.jsonl` receives local delivery records when routing is enabled.

Stop condition: Do not rely on webhook delivery if routing failures exist. Keep local alert summaries as the source of truth until the endpoint and operator response process are verified.

## Dashboard Import Check

Symptom: Operator wants a visual dashboard over exported metrics.

Checks:

```powershell
python scripts/metrics_export.py --check
python -m json.tool docs/dashboards/forex_scanner_ops_grafana.json
```

Safe recovery steps:

- Import `docs/dashboards/forex_scanner_ops_grafana.json` into Grafana.
- Bind `${DS_PROMETHEUS}` to the Prometheus datasource that reads the textfile-collected metrics.
- Confirm panels populate for broker connectivity, sync freshness, reconciliation anomalies, active alerts/incidents, retry/reject trends, guardrails, kill switch, degraded mode, live submission failures, recovery activity, and soak reliability proxy.

Stop condition: Do not use dashboard state for decisions if the metrics textfile is stale or the datasource cannot read the expected metric families.

## Soak Validation Before Supervised Live Consideration

Symptom: Operator wants evidence that broker/recovery/monitoring remain stable over time.

Checks:

```powershell
python scripts/soak_test.py --mode paper --duration-minutes 5 --interval-seconds 30
python scripts/soak_test.py --mode broker_sandbox --provider mock --duration-minutes 15 --interval-seconds 60
python scripts/soak_test.py --mode broker_sandbox --duration-minutes 60 --interval-seconds 60
```

Safe recovery steps:

- Inspect `reports/soak/<run_id>/readiness.md`.
- Inspect `reliability.md` for connectivity, reconciliation, account sync, position sync, stale state, retry, and degraded-time metrics.
- Inspect `anomalies.md` and `unresolved_issues.csv`.
- Extend the soak duration if the result is `pass_with_warnings`.

Stop condition: Do not continue toward broker-live supervised checks after a `fail`, unresolved high/critical incidents, retry exhaustion, manual intervention, repeated stale state, or severe reconciliation anomalies.

## Multi-Session Soak Campaign Before Serious Supervised Use

Symptom: Operator wants evidence across multiple sessions or days instead of one isolated soak run.

Checks:

```powershell
python scripts/soak_campaign.py start --name weekly-sandbox --mode broker_sandbox --provider mock --target-hours 168
python scripts/soak_campaign.py run-session --name weekly-sandbox --provider mock --duration-minutes 30 --interval-seconds 60
python scripts/soak_campaign.py status --name weekly-sandbox
python scripts/soak_campaign.py finalize --campaign-id <campaign_id> --out reports/soak_campaigns
```

Safe recovery steps:

- Inspect `reports/soak_campaigns/<campaign_id>/weekly_reliability.md`.
- Inspect `readiness.md` for `not_ready`, `limited_ready`, or `supervised_ready`.
- Inspect `recurring_anomalies.md` for worsening or repeated operational failure modes.
- Inspect `campaign_timeline.csv` and `restart_recovery_events.csv` around any degraded windows.
- Add manual context to `operator_notes.md` if terminal maintenance, broker outages, or manual review happened during the campaign.

Stop condition: Do not proceed toward serious broker-live supervised checks unless the campaign is `supervised_ready`, there are no unresolved high/critical issues, and a fresh recovery/reconciliation pass is clean.

## Broker Unavailable

Symptom: `broker_down`, `broker_unavailable`, or `mt5_terminal_not_reachable`.

Probable causes: MT5 package missing, terminal closed, invalid login, server unreachable, network interruption.

Checks:

```powershell
python scripts/broker_check.py --mode broker_sandbox
python scripts/broker_recovery.py --mode broker_sandbox
```

Safe recovery steps:

- Open MT5 and verify the account is logged in.
- Confirm `.env` values match the intended sandbox/live account.
- Rerun recovery and inspect `broker_health_history.csv`.

Stop condition: Do not submit while health is `unavailable` or connectivity alerts remain active.

## Operator Maintenance Or Degraded Mode

Symptom: Work must pause for investigation, or operation should continue only in reduced-confidence monitoring mode.

Checks:

```powershell
python scripts/broker_control.py
python scripts/broker_report.py --out reports/broker
```

Safe recovery steps:

- Use maintenance mode for hard pauses:

```powershell
python scripts/broker_control.py --maintenance on --reason "operator investigation"
```

- Use degraded mode only when checks are incomplete but monitoring may continue:

```powershell
python scripts/broker_control.py --degraded on --reason "recent broker instability"
```

- Clear controls only after recovery and reconciliation reports are clean:

```powershell
python scripts/broker_recovery.py --mode broker_sandbox
python scripts/broker_control.py --maintenance off --degraded off --broker-submissions on --reason "operator reviewed clean recovery"
```

Stop condition: Do not resume broker submissions while `resume_readiness.md` says `blocked_pending_manual_review`.

## MT5 Terminal Disconnected

Symptom: MT5 initialization or account sync fails.

Probable causes: terminal process unavailable, wrong path, stale session, local permissions.

Checks:

```powershell
python scripts/broker_check.py --mode broker_sandbox
```

Safe recovery steps:

- Start the terminal manually.
- If needed, set `FOREX_SCANNER_MT5_PATH`.
- Rerun `broker_recovery.py`.

Stop condition: Account state cannot be retrieved or `can_trade=False`.

## Stale Account State

Symptom: `stale_account_state` alert or degraded flag.

Probable causes: failed account refresh, frozen terminal, interrupted process.

Checks:

```powershell
python scripts/broker_recovery.py --mode broker_sandbox
```

Safe recovery steps:

- Confirm `last_successful_account_sync_at` is recent in `broker_health.md`.
- Confirm `broker_connected=1` in `operational_metrics.csv`.

Stop condition: Account snapshot is older than configured thresholds.

## Reconciliation Mismatch

Symptom: severe reconciliation alert, unknown broker state, partial desync, manual broker-side change.

Checks:

```powershell
python scripts/broker_recovery.py --mode broker_sandbox
python scripts/broker_report.py --out reports/broker
```

Safe recovery steps:

- Inspect `reconciliation_anomalies.csv`.
- Compare local `broker_orders.csv` with the broker terminal.
- Manually resolve broker-side state before retrying.

Stop condition: Any high/critical anomaly remains open.

## Repeated Broker Rejects

Symptom: repeated reject alert or incident.

Probable causes: invalid volume, symbol disabled, price level invalid, account permission, market closed.

Checks:

```powershell
python scripts/broker_report.py --out reports/broker
```

Safe recovery steps:

- Inspect `rejections.csv`.
- Reduce scope to mock/sandbox dry-run.
- Validate symbol, volume, stop distance, and account permissions.

Stop condition: Reject streak remains at or above configured threshold.

## Startup Recovery After Restart

Symptom: Process restarted after pending/open broker activity.

Checks:

```powershell
python scripts/broker_recovery.py --mode broker_sandbox
```

Safe recovery steps:

- Inspect `restart_recovery.md`.
- Inspect `resume_readiness.md`, `unresolved_incidents.csv`, and `alert_aging.csv`.
- Confirm no orphaned local state or unknown broker state.
- Resolve open incidents before any submit.

Stop condition: `restart_unfinished_state`, `unknown_broker_state`, or severe mismatch is active.

## Kill Switch Activation

Symptom: `kill_switch_active` alert.

Checks:

```powershell
$env:FOREX_SCANNER_BROKER_KILL_SWITCH
python scripts/broker_check.py --mode broker_sandbox
```

Safe recovery steps:

- Keep submissions blocked.
- Optionally persist the pause in operator controls:

```powershell
python scripts/broker_control.py --broker-submissions off --reason "kill switch review"
```

- Inspect alerts/incidents and broker terminal.
- Clear the variable only after operator review:

```powershell
Remove-Item Env:\FOREX_SCANNER_BROKER_KILL_SWITCH
```

Stop condition: Do not resume while the kill switch is active.

## Manual Intervention Required

Symptom: `manual_intervention_required` state, alert, or report row.

Checks:

```powershell
python scripts/broker_report.py --out reports/broker
```

Safe recovery steps:

- Inspect `manual_intervention.csv`.
- Verify broker terminal order/position state manually.
- Reconcile local records before another submit.

Stop condition: Any unresolved manual intervention alert remains active.

## Archive And Retention Maintenance

Symptom: Operational database, audit history, monitoring outputs, or reports are growing beyond the configured retention windows.

Checks:

```powershell
python scripts/archive_records.py candidates --db data/forex_scanner.sqlite --out reports/archives
python scripts/archive_records.py rotation-plan --db data/forex_scanner.sqlite --out reports/archives
```

Safe archival steps:

- Inspect `reports/archives/pending_archival_candidates.csv`.
- Create a local archive package:

```powershell
python scripts/archive_records.py create --db data/forex_scanner.sqlite --out archives/operational --report-out reports/archives
```

- Verify the package:

```powershell
python scripts/archive_records.py verify --archive archives/operational/<archive_id>.zip --out reports/archives
```

- Restore to a review area before relying on the archive:

```powershell
python scripts/archive_records.py restore-review --archive archives/operational/<archive_id>.zip --restore-dir archives/restore_review --out reports/archives
```

Safe recovery steps:

- Use only restore-for-review for normal evidence review.
- Do not delete active SQLite rows or audit seals after creating an archive.
- Keep the ZIP, sidecar `.manifest.json`, and `.sha256` files together.
- Re-run `scripts/audit_integrity.py verify` if any archive verification fails.

Stop condition: Archive verification fails, `rotation_safe=false`, or the archive cannot be restored into the review directory.

## Daily Archive Seal

Symptom: End-of-day or session-close maintenance needs a fresh integrity checkpoint plus an archive package.

Checks and workflow:

```powershell
python scripts/archive_records.py daily --db data/forex_scanner.sqlite --out archives/operational --report-out reports/archives
```

Safe recovery steps:

- Inspect `reports/archives/summary.md`.
- Verify the created ZIP and sidecar hash.
- Restore the archive into `archives/restore_review` for spot checks when needed.

Stop condition: Daily archive verification fails or the restore-for-review step is blocked.

## Local Backup Before Maintenance

Symptom: Operator is about to perform maintenance, upgrade work, manual data review, or any change that could affect local operational state.

Checks:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite create --out backups/local --report-out reports/backup_recovery --label pre-maintenance --reason "pre-maintenance checkpoint"
python scripts/backup_recovery.py --db data/forex_scanner.sqlite list --out backups/local --report-out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite verify --backup backups/local/<backup_id>.zip --out reports/backup_recovery
```

Safe recovery steps:

- Inspect `reports/backup_recovery/summary.md`.
- Confirm `reports/backup_recovery/backup_coverage.md` includes the active SQLite state, audit integrity metadata, config snapshot, and archive manifest inventory.
- Keep the ZIP, sidecar `.manifest.json`, and `.sha256` files together.
- Do not continue with maintenance if backup verification fails.

Stop condition: Backup verification fails, audit integrity verification fails before backup, or the package cannot be listed/inspected.

## Backup Restore Review

Symptom: Operator needs to inspect backup contents after local failure, suspected corruption, or before deciding whether active restore is safe.

Checks:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite inspect --backup backups/local/<backup_id>.zip --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite verify --backup backups/local/<backup_id>.zip --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite restore-review --backup backups/local/<backup_id>.zip --restore-dir backups/restore_review --out reports/backup_recovery
```

Safe recovery steps:

- Review `backups/restore_review/<backup_id>/README.md`.
- Inspect the restored `state/forex_scanner.sqlite` in the review directory only.
- Compare manifest hashes and verification output before trusting the staged state.
- Keep active broker submissions blocked while recovery evidence is being reviewed.

Stop condition: Verification fails, the staged database fails SQLite integrity check, or audit-chain verification fails in the backup.

## Explicit Active Restore And Post-Restore Validation

Symptom: Active local operational state is missing, corrupted, or unusable and the operator has decided to restore from a verified backup.

Checks:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite verify --backup backups/local/<backup_id>.zip --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite restore-active --backup backups/local/<backup_id>.zip --target-db data/forex_scanner.sqlite --allow-active-restore --confirm-active-restore --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite post-restore-check --out reports/backup_recovery
python scripts/operator_session.py status --out reports/operator
```

Safe recovery steps:

- Confirm a pre-restore safety copy was created under `backups/pre_restore_safety` when an active database existed.
- Inspect `reports/backup_recovery/recovery_validation_status.md`.
- Run broker recovery/reconciliation before any supervised broker workflow:

```powershell
python scripts/broker_recovery.py --mode broker_sandbox --provider mock
```

- Keep broker-live sensitive actions blocked until recovery validation is `passed`, unresolved incidents/alerts are reviewed, and a fresh operator checklist is clean.

Stop condition: Recovery validation is `pending`, `failed`, or `blocked_pending_operator_review`; active restore was attempted without explicit confirmation; or broker/account/reconciliation state cannot be trusted after restore.
