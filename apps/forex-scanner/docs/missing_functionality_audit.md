# Missing Functionality Audit — Paper/Demo Platform

Date: 2026-06-12
Scope: `apps/forex-scanner` (paper/demo only — live trading is **not** authorized and is **not** in scope).

## 1. Delivered functionality (verified on `main`)

| Layer | Implementation | Reports |
| --- | --- | --- |
| Autonomous Supervisor | `app/execution/autonomous_supervisor.py`, `app/supervisor/autonomous.py`, `scripts/run_autonomous_supervisor.py` | supervisor summaries |
| Autonomous Readiness Gate | `app/execution/autonomous_readiness.py`, `scripts/autonomous_readiness_report.py` | `reports/autonomous_readiness_report.json` |
| Autonomous Evidence Builder | `app/execution/autonomous_evidence.py`, `scripts/autonomous_evidence_builder.py` | `reports/autonomous_evidence_report.json` |
| Autonomous Recovery Planner | `app/execution/autonomous_recovery.py`, `scripts/autonomous_recovery_planner.py` | `reports/autonomous_recovery_plan.json` |
| Autonomous Policy Engine | `app/execution/autonomous_policy.py`, `scripts/autonomous_policy_report.py` | `reports/autonomous_policy_report.json` |
| Autonomous Scenario Runner | `app/execution/autonomous_scenarios.py`, `scripts/autonomous_scenario_runner.py` | `reports/autonomous_scenario_suite.json` |
| Realtime Data Health | `app/execution/realtime_data_health.py`, `scripts/realtime_data_check.py` | data health summaries |
| Realtime Paper Supervisor | `app/execution/realtime_paper_supervisor.py`, `scripts/realtime_paper_supervisor.py` | `reports/realtime_paper_supervisor_summary.json` |
| Realtime Paper Position Manager | `app/execution/realtime_paper_positions.py`, `scripts/realtime_paper_positions.py` | `reports/realtime_paper_positions.json` |
| Realtime Paper Command Center | `app/execution/realtime_command_center.py`, `scripts/realtime_command_center.py` | `reports/realtime_command_center_summary.json` |
| Local MT5 Realtime Validation | `scripts/local_mt5_realtime_validation.py`, `docs/local_mt5_realtime_validation.md` | `reports/local_mt5_realtime_validation.json` |
| Local Paper Operation Runbook | `docs/local_paper_operation_runbook.md` | n/a |
| Runtime Safety Heartbeat | `RealtimePaperSupervisorService._write_heartbeat` | `reports/realtime_heartbeat.jsonl` |
| Paper performance (record-level) | `app/reporting/paper_performance.py`, `scripts/paper_performance_report.py` | JSON/TXT paper performance report |

## 2. Stale open issues that appear already implemented

All 11 currently open issues appear to be implemented by merged PRs. Evidence:

| Issue | Title | Merged PRs | Proving files |
| --- | --- | --- | --- |
| #48 | Build Autonomous Supervisor v0 | #49, #50 | `app/supervisor/autonomous.py`, `scripts/run_autonomous_supervisor.py`, `tests/test_autonomous_supervisor.py` |
| #51 | Audit and harden Autonomous Supervisor v0 | #52, #59 | `app/execution/autonomous_supervisor.py`, `docs/autonomous_supervisor.md` |
| #53 | Autonomous Readiness Gate | #54 | `app/execution/autonomous_readiness.py`, `tests/test_autonomous_readiness.py` |
| #55 | Autonomous Evidence Builder | #56 | `app/execution/autonomous_evidence.py`, `tests/test_autonomous_evidence.py` |
| #57 | Autonomous Recovery Planner | #58 | `app/execution/autonomous_recovery.py`, `tests/test_autonomous_recovery.py` |
| #64 | Autonomous Scenario Runner | #65–#69 | `app/execution/autonomous_scenarios.py`, `tests/test_autonomous_scenarios.py` |
| #70 | Realtime Paper Readiness Layer | #71–#79 | `app/execution/realtime_data_health.py`, `app/execution/realtime_paper_supervisor.py` |
| #75 | Realtime Paper Position Manager | #80 | `app/execution/realtime_paper_positions.py`, `tests/test_realtime_paper_positions.py` |
| #82 | Realtime Paper Command Center | #83 | `app/execution/realtime_command_center.py`, `tests/test_realtime_command_center.py` |
| #84 | Local MT5 Realtime Validation | #85–#89 | `scripts/local_mt5_realtime_validation.py`, `tests/test_local_mt5_realtime_validation.py` |
| #90 | Local Paper Operation Runbook | #91 | `docs/local_paper_operation_runbook.md`, `tests/test_local_paper_operation_runbook.py` |

**Recommendation:** all 11 issues are candidates to close as completed. This audit does **not**
close them; a detailed per-issue resolution plan is delivered separately in
`docs/stale_issue_resolution_plan.md` (Priority 4). Closing requires explicit operator approval.

## 3. Genuinely missing functionality

1. **Operator Report Dashboard / Viewer (Priority 1)** — there is no single read-only view that
   aggregates the existing report artifacts (`local_mt5_realtime_validation.json`,
   `realtime_command_center_summary.json`, supervisor/positions/heartbeat, autonomous reports)
   into one operator status. `scripts/dashboard.py` is a watchlist/monitoring dashboard, and
   `app/reporting/operator.py` covers operator workflow records — neither summarizes the
   paper/demo report stack.
2. **Paper Session Bundle Export (Priority 2)** — no tooling packages a session's report
   artifacts into an auditable zip with manifest + sha256 checksums.
3. **Paper Performance Analytics (lifecycle-level, Priority 3)** — `app/reporting/paper_performance.py`
   covers record-level fill/signal analytics but does not aggregate paper order lifecycle
   metrics (open/closed counts, realized R, partial exits, breakeven moves, invalidations,
   time-in-trade) from position manager and command center reports.
4. **Stale Issue Resolution Plan (Priority 4)** — documentation mapping open issues to merged PRs.
5. **Documentation Index (Priority 5)** — `docs/` has 40+ documents and no `docs/index.md`.

## 4. Proposed implementation order

1. PR 1 — Operator Report Dashboard / Viewer (+ this audit document).
2. PR 2 — Paper Session Bundle Export (consumes dashboard output when present).
3. PR 3 — Paper Performance Analytics (lifecycle-level).
4. PR 4 — Stale Issue Resolution Plan (docs only).
5. PR 5 — Documentation Index (docs only).

## 5. Safety constraints (apply to every PR)

- Paper/demo only; live trading is not authorized and must not be enabled.
- No broker-live execution; no `order_send` calls.
- No `.env` mutation; no secrets committed.
- No daemons; no infinite loops; bounded, read-only operations.
- MT5 must not be required in CI or by any new module.
- Generated reports stay under `reports/` (gitignored) and are never committed.

## 6. Files likely to be modified or created

- `app/reporting/operator_dashboard.py`, `scripts/operator_dashboard.py`,
  `tests/test_operator_dashboard.py`, `docs/operator_dashboard.md` (PR 1)
- `app/reporting/session_bundle.py`, `scripts/export_paper_session_bundle.py`,
  `tests/test_session_bundle.py`, `docs/paper_session_bundle.md` (PR 2)
- `app/reporting/paper_lifecycle_performance.py` (or extension of
  `app/reporting/paper_performance.py`), `scripts/paper_performance_report.py`,
  `tests/test_paper_performance.py`, `docs/paper_performance.md` (PR 3)
- `docs/stale_issue_resolution_plan.md` (PR 4)
- `docs/index.md`, `README.md` (PR 5)

## 7. Test plan

- `python -m pytest -q tests/test_operator_dashboard.py`
- `python -m pytest -q tests/test_session_bundle.py`
- `python -m pytest -q tests/test_paper_performance.py`
- `python -m pytest -q tests/test_local_mt5_realtime_validation.py tests/test_realtime_command_center.py --maxfail=1`
- CLI smoke runs (all offline, no MT5):
  - `python scripts/operator_dashboard.py --reports-dir reports --export-json --export-txt`
  - `python scripts/export_paper_session_bundle.py --reports-dir reports --output-dir reports/bundles --session-name paper-session-smoke`
  - `python scripts/paper_performance_report.py --reports-dir reports --export-json --export-txt`
- Each new test suite must assert: no `order_send`, no live-trading flags, no MT5 import
  requirement, and no `.env`/environment mutation.
