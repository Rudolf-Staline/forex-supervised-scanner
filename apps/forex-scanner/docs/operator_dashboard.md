# Operator Report Dashboard (read-only, paper/demo only)

The operator dashboard summarizes the current paper/demo system state from
existing report artifacts in `reports/`. It is strictly read-only:

- it runs **no trading logic**,
- it never imports or calls **MT5**,
- it never calls **`order_send`**,
- it never mutates **`.env`** or the process environment,
- it never submits broker orders,
- it works fully **offline** from local report files.

## Usage

From `apps/forex-scanner`:

```bash
python scripts/operator_dashboard.py --reports-dir reports --export-json --export-txt
```

Strict mode (non-zero exit unless the final status is READY or WARN — useful
for operator checklists):

```bash
python scripts/operator_dashboard.py --reports-dir reports --strict
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--reports-dir` | `reports` | Directory containing the report artifacts |
| `--max-age-hours` | `24` | Reports older than this are treated as stale |
| `--export-json` | off | Write `reports/operator_dashboard_summary.json` |
| `--export-txt` | off | Write `reports/operator_dashboard_report.txt` |
| `--strict` | off | Exit `1` unless final status is READY or WARN |

## Inputs

Required reports (missing ones are listed in `missing_reports`):

- `reports/local_mt5_realtime_validation.json`
- `reports/realtime_command_center_summary.json`
- `reports/realtime_paper_supervisor_summary.json`
- `reports/realtime_paper_positions.json`
- `reports/realtime_heartbeat.jsonl`
- `reports/autonomous_scenario_suite.json`

Optional reports (summarized only if present):

- `reports/autonomous_policy_report.json`
- `reports/autonomous_readiness_report.json`
- `reports/autonomous_evidence_report.json`
- `reports/autonomous_recovery_plan.json`

## Outputs

- `reports/operator_dashboard_summary.json`
- `reports/operator_dashboard_report.txt`

Summary fields: `final_operator_status`, `mt5_validation_status`,
`command_center_status`, `supervisor_status`, `position_manager_status`,
`heartbeat_status`, `readiness_status`, `evidence_status`, `policy_decision`,
`recovery_status`, `scenario_status`, `latest_report_times`, `stale_reports`,
`missing_reports`, `blocking_reasons`, `warnings`, `safety_flags`,
`recommended_next_actions`, `output_paths`.

## Final operator statuses

| Status | Meaning |
| --- | --- |
| `OPERATOR_READY_FOR_PAPER_REVIEW` | All required reports present, fresh, and healthy |
| `OPERATOR_WARN_REVIEW_REQUIRED` | Non-blocking warnings need operator review |
| `OPERATOR_BLOCKED` | At least one report shows a blocking condition (safety drift, synthetic fallback, blocked readiness/policy, unsafe safety flags, …) |
| `OPERATOR_REPORTS_MISSING` | One or more required reports are missing or unreadable |
| `OPERATOR_REPORTS_STALE` | One or more required reports are older than `--max-age-hours` |

Precedence: `BLOCKED` > `REPORTS_MISSING` > `REPORTS_STALE` > `WARN` > `READY`.

## Blocking conditions surfaced

- runtime safety heartbeat drift (`BLOCKED_BY_SAFETY_DRIFT` or drift reasons in
  the latest heartbeat record),
- synthetic-fallback usage anywhere in the supervisor/command-center/position
  reports,
- any source report whose `safety_flags` claim `live_execution_allowed`,
  `live_trading_enabled`, `broker_live_execution_allowed`, or
  `order_send_called` is true,
- blocked statuses from MT5 validation, command center, supervisor, position
  manager, scenarios, readiness, evidence, policy (DENY), or recovery
  (RECOVERY_BLOCKING).

If a recovery plan is present, its `next_recommended_command`, safe actions,
and manual actions are surfaced in `recommended_next_actions`.

## Testing

```bash
python -m pytest -q tests/test_operator_dashboard.py
```

The tests run offline, require no MT5, and assert that the dashboard performs
no live trading, no `order_send`, and no `.env` mutation.

## Paper session bundle handoff

After reviewing the operator dashboard, operators can export a portable
paper/demo archive for manual audit:

```bash
python scripts/export_paper_session_bundle.py --reports-dir reports --output-dir reports/bundles --session-name paper-session-smoke
```

The bundle exporter reads `operator_dashboard_summary.json` when present and
copies its `final_operator_status`, `blocking_reasons`, `warnings`, and safety
flags into the session manifest. The exporter is archive-only: it does not run
trading logic, does not call MT5, does not call `order_send`, does not submit
broker orders, and does not mutate `.env`. A passing dashboard or bundle is
review evidence only and must never be treated as live-trading authorization.

## Paper performance analytics companion

After generating the operator dashboard and paper-session evidence, operators can run read-only Paper Performance Analytics:

```bash
python scripts/paper_performance_report.py --reports-dir reports --export-json --export-txt
```

The analytics report reads the dashboard, command-center, supervisor, heartbeat, position-manager, and local paper-order artifacts when present. It does not run strategies, does not call MT5, does not call `order_send`, does not submit broker orders, and does not authorize live trading. Unsafe safety flags propagated from dashboard/source reports block the paper-performance status. Metrics are diagnostic evidence only for paper/demo review.
