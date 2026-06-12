# Stale Issue Resolution Plan — Paper/Demo Features

Date: 2026-06-12  
Scope: `apps/forex-scanner`  
Nature: documentation/audit only. This plan does **not** close issues, mutate GitHub state, add issue comments, change runtime behavior, or change safety defaults.

## Purpose

Several open GitHub issues appear to have been implemented by later merged pull requests. This plan gives a human maintainer a clear, evidence-based closure checklist.

The repository remains strictly paper/demo only:

- no live trading is authorized;
- no broker-live execution is introduced;
- no `order_send` calls are added by this audit;
- no `.env` mutation is introduced;
- no safety defaults are changed.

## Summary

| Issue | Feature area | Merged evidence | Proving files | Recommendation |
| --- | --- | --- | --- | --- |
| #48 | Autonomous Supervisor v0 | #49, #50, #52 | `app/execution/autonomous_supervisor.py`, `app/supervisor/autonomous.py`, `scripts/run_autonomous_supervisor.py`, `tests/test_autonomous_supervisor.py` | Close as completed |
| #51 | Autonomous Supervisor hardening | #52, #59 | `app/execution/autonomous_supervisor.py`, `docs/autonomous_supervisor.md`, supervisor tests | Close as completed |
| #53 | Autonomous Readiness Gate | #54 | `app/execution/autonomous_readiness.py`, `scripts/autonomous_readiness_report.py`, `tests/test_autonomous_readiness.py` | Close as completed |
| #55 | Autonomous Evidence Builder | #56 | `app/execution/autonomous_evidence.py`, `scripts/autonomous_evidence_builder.py`, `tests/test_autonomous_evidence.py` | Close as completed |
| #57 | Autonomous Recovery Planner | #58 | `app/execution/autonomous_recovery.py`, `scripts/autonomous_recovery_planner.py`, `tests/test_autonomous_recovery.py` | Close as completed |
| #64 | Autonomous Scenario Runner | #65-#69 | `app/execution/autonomous_scenarios.py`, `scripts/autonomous_scenario_runner.py`, `tests/test_autonomous_scenarios.py` | Close as completed |
| #70 | Realtime Paper Readiness Layer | #71-#79 | `app/execution/realtime_data_health.py`, `app/execution/realtime_paper_supervisor.py`, realtime tests | Close as completed |
| #75 | Realtime Paper Position Manager | #80 | `app/execution/realtime_paper_positions.py`, `scripts/realtime_paper_positions.py`, `tests/test_realtime_paper_positions.py` | Close as completed |
| #82 | Realtime Paper Command Center | #83 | `app/execution/realtime_command_center.py`, `scripts/realtime_command_center.py`, `tests/test_realtime_command_center.py` | Close as completed |
| #84 | Local MT5 Realtime Validation | #85-#89 | `scripts/local_mt5_realtime_validation.py`, `docs/local_mt5_realtime_validation.md`, `tests/test_local_mt5_realtime_validation.py` | Close as completed |
| #90 | Local Paper Operation Runbook | #91 | `docs/local_paper_operation_runbook.md`, `tests/test_local_paper_operation_runbook.py` | Close as completed |
| #94 | Paper Session Bundle Export | #95 | `app/reporting/session_bundle.py`, `scripts/export_paper_session_bundle.py`, `tests/test_session_bundle.py` | Close as completed |
| #97 | Paper Performance Analytics | #98 | `app/reporting/paper_performance.py`, `scripts/paper_performance_report.py`, `tests/test_paper_performance.py` | Close as completed |

## Suggested manual closure order

Close in dependency order so the project history remains readable:

1. #48
2. #51
3. #53
4. #55
5. #57
6. #64
7. #70
8. #75
9. #82
10. #84
11. #90
12. #94
13. #97

## Suggested closure comment template

```text
Closing as completed. The requested functionality appears implemented by the merged PRs and files listed in `apps/forex-scanner/docs/stale_issue_resolution_plan.md`. The feature remains within the project safety scope and no additional runtime changes are required for this umbrella issue.
```

## Per-issue notes

### #48 — Build Autonomous Supervisor v0

Implemented by PRs #49, #50, and #52. The current implementation is under `app/execution/autonomous_supervisor.py`, with CLI/docs/tests in place. Recommendation: close as completed.

### #51 — Audit and harden Autonomous Supervisor v0

Implemented by PRs #52 and #59. Recommendation: close as completed.

### #53 — Add Autonomous Readiness Gate before supervisor runs

Implemented by PR #54. Recommendation: close as completed.

### #55 — Add Autonomous Evidence Builder

Implemented by PR #56. Recommendation: close as completed.

### #57 — Add Autonomous Recovery Planner

Implemented by PR #58. Recommendation: close as completed.

### #64 — Add Autonomous Scenario Runner

Implemented and hardened across PRs #65-#69. Recommendation: close as completed.

### #70 — Add Realtime Paper Readiness Layer

Implemented and hardened across PRs #71-#79. Recommendation: close as completed.

### #75 — Add Realtime Paper Position Manager

Implemented by PR #80. Recommendation: close as completed.

### #82 — Add Realtime Paper Command Center

Implemented by PR #83. Recommendation: close as completed.

### #84 — Add Local MT5 Realtime Validation Runbook

Implemented and hardened through PRs #85-#89. Recommendation: close as completed.

### #90 — Add Local Paper Operation Runbook

Implemented by PR #91. Recommendation: close as completed.

### #94 — Add Paper Session Bundle Export

Implemented by PR #95. Recommendation: close as completed.

### #97 — Add Paper Performance Analytics

Implemented by PR #98. Recommendation: close as completed.

## Maintainer notes

Do not bulk-close blindly. Before closing, verify that the referenced files still exist on `main` and that the relevant tests still pass. If a gap is found, keep the issue open or create a focused follow-up issue.

## Follow-up

The next documentation task should be a full documentation index at:

```text
apps/forex-scanner/docs/index.md
```

That index should map the whole paper/demo stack and link safety, setup, operator workflows, reports, analytics, and bundle export documentation.
