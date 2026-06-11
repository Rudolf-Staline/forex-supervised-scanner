# Autonomous Recovery Planner

The Autonomous Recovery Planner answers the safe follow-up question when evidence, readiness, or supervisor reports are blocked or degraded:

> What should be fixed next, safely, without enabling live trading?

It is a diagnostic planning layer between the Readiness Gate and the Autonomous Supervisor. It **does not bypass readiness**, does not enable broker-live execution, does not change strategy thresholds, and does not submit orders.

## Safe autonomy pipeline

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> Autonomous Supervisor -> Audit Reports
```

The Recovery Planner only helps restore readiness. If readiness is blocked, the supervisor remains blocked until the evidence/readiness reports are healthy again.

## Inputs inspected

The planner reads local artifacts under `reports/` and tolerates missing files:

- `autonomous_evidence_summary.json`
- `autonomous_readiness_report.json`
- `autonomous_supervisor_summary.json`
- `session_health_summary.json`
- `data_health_report.json` or `data_health_summary.json`
- `failure_diagnostics_summary.json`
- `signal_anomaly_summary.json` or `signal_anomalies_summary.json`
- `mt5_symbol_mapping_audit.json`

Missing required evidence and stale reports are classified as recovery causes.

## Cause classifications

Plans can report causes such as:

- `MISSING_EVIDENCE`
- `STALE_EVIDENCE`
- `DATA_QUALITY_BLOCKED`
- `SESSION_HEALTH_BLOCKED`
- `RISK_LIMIT_BLOCKED`
- `OPERATOR_MAINTENANCE_MODE`
- `OPERATOR_DEGRADED_MODE`
- `FAILURE_DIAGNOSTICS_BLOCKED`
- `SIGNAL_ANOMALIES_BLOCKED`
- `SYMBOL_MAPPING_BLOCKED`
- `SUPERVISOR_ZERO_ORDER_STREAK`
- `SUPERVISOR_FAILURE_STREAK`
- `SAFETY_MODE_BLOCKED`
- `UNKNOWN_BLOCKER`

Each cause includes the source report, severity, reason, evidence path, and suggested action IDs.

## Recovery actions

The planner proposes bounded recovery actions:

- `REBUILD_EVIDENCE_DRY_RUN`
- `REBUILD_EVIDENCE_READ_ONLY`
- `RUN_READINESS_ONLY`
- `RUN_FAILURE_DIAGNOSTICS`
- `RUN_DATA_HEALTH_REPORT`
- `RUN_SESSION_HEALTH_REPORT`
- `RUN_SIGNAL_ANOMALY_DETECTOR`
- `RUN_STATIC_SYMBOL_MAPPING_AUDIT`
- `REVIEW_OPERATOR_CONTROLS`
- `REVIEW_RISK_LIMITS`
- `REVIEW_STALE_REPORTS`
- `KEEP_SUPERVISOR_BLOCKED`

Only dry-run or read-only actions are marked safe for optional automatic execution. Manual review actions are never executed automatically.

## CLI usage

Plan only, with exports:

```bash
python scripts/autonomous_recovery_planner.py --export-json --export-txt
```

Simulate safe execution without launching diagnostics:

```bash
python scripts/autonomous_recovery_planner.py --execute-safe-actions --dry-run --export-json --export-txt
```

Options:

- `--reports-dir`
- `--export-json`
- `--export-txt`
- `--execute-safe-actions`
- `--max-actions`
- `--include-manual-actions` / `--no-include-manual-actions`
- `--fail-fast`
- `--dry-run`

Default behavior is plan-only. No action executes unless `--execute-safe-actions` is explicitly passed.

## Outputs

When requested, the planner writes:

- `reports/autonomous_recovery_plan.json`
- `reports/autonomous_recovery_plan.txt`

The JSON schema includes:

- `generated_at`
- `final_status`
- `causes`
- `actions`
- `safe_actions`
- `manual_actions`
- `executed_actions`
- `skipped_actions`
- `blocking_reasons`
- `safety_flags`
- `next_recommended_command`

Final statuses are `NO_RECOVERY_NEEDED`, `RECOVERY_RECOMMENDED`, `RECOVERY_BLOCKING`, `RECOVERY_EXECUTED`, and `RECOVERY_PARTIAL`.

## Integrations

Evidence, readiness, and supervisor CLIs can generate a recovery plan when they detect a block:

```bash
python scripts/autonomous_evidence_builder.py --mode read-only --plan-recovery-on-block --export-recovery-json --export-recovery-txt
python scripts/autonomous_readiness_report.py --build-evidence-first --plan-recovery-on-block --export-recovery-json --export-recovery-txt
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --build-evidence-first --readiness-only --plan-recovery-on-block --export-recovery-json --export-recovery-txt
```

Supervisor cycles are not run to recover readiness. The recovery plan is an audit artifact and a set of safe next steps.

## Safety guarantees

The planner forbids automatic broker-live actions, terminal order actions, `.env` mutation, credential changes, report deletion, strategy-threshold changes, and safety-gate bypasses. It remains cloud-safe and does not require a local MT5 terminal for tests.

## Policy Engine Integration

The Recovery Planner now consults the Autonomous Policy Engine via `can_execute_recovery_action()` before executing each recovery action. The policy engine evaluates whether the action is safe for automatic execution under the current mode, checking that:

- Plan generation is always allowed.
- Manual-review actions are always denied for automatic execution.
- Only explicitly safe dry-run/read-only actions are permitted in safe modes.
- Recovery can never directly unblock the supervisor or override the readiness gate.

Policy decisions are included in the recovery plan under the `policy_decision` field of `reports/autonomous_recovery_plan.json`. Each executed action's policy result is recorded for auditability.

The updated safe autonomy pipeline is:

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> [Policy Engine] -> Autonomous Supervisor -> Audit Reports
```

The policy engine does not change recovery planning behavior. It centralizes the permission check for action execution that was previously implicit in safe-action classification. This remains diagnostic-only and does not authorize live trading. See [`autonomous_policy_engine.md`](autonomous_policy_engine.md).

## Scenario-based validation

Use the Autonomous Scenario Runner to validate how this component behaves as part of the wider autonomous stack. The runner creates synthetic local reports, evaluates policy decisions, simulates supervisor outcomes, and can recommend recovery plans without MT5, network access, `.env` mutation, daemon creation, live trading, broker-live execution, or order submission.

```bash
python scripts/autonomous_scenario_runner.py --list
python scripts/autonomous_scenario_runner.py --all --export-json --export-txt --strict
```

See [Autonomous Scenario Runner](autonomous_scenario_runner.md) for scenario definitions, report schema, and interpretation guidance. Passing scenarios are audit evidence only; they do not authorize live trading.
