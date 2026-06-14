# Forex Supervisor Documentation Index

This index maps the `apps/forex-scanner` paper/demo documentation stack.

The project remains **paper/demo only**:

- no live trading is authorized;
- keep `EXECUTION_MODE=paper`;
- keep `ALLOW_LIVE_TRADING=false`;
- keep `BROKER_MODE=paper`;
- do not commit broker or MT5 secrets;
- local MT5 checks are validation-only unless explicitly stated otherwise.

## Start here

| Need | Read |
| --- | --- |
| Understand the project safety scope | [`../README.md`](../README.md) |
| Run the full local operator workflow | [`local_paper_operation_runbook.md`](local_paper_operation_runbook.md) |
| Validate local MT5 market data safely | [`local_mt5_realtime_validation.md`](local_mt5_realtime_validation.md) |
| Run one bounded realtime paper/demo entrypoint | [`realtime_command_center.md`](realtime_command_center.md) |
| Summarize all report artifacts | [`operator_dashboard.md`](operator_dashboard.md) |
| Export an auditable paper/demo bundle | [`paper_session_bundle.md`](paper_session_bundle.md) |
| Analyze paper/demo performance | [`paper_performance.md`](paper_performance.md) |
| Run a post-session operator review | [`paper_session_review.md`](paper_session_review.md) |
| Track session review history over time | [`paper_session_history.md`](paper_session_history.md) |
| Analyze multi-session paper/demo history trends | [`paper_session_trends.md`](paper_session_trends.md) |

## Safety and setup

| Document | Purpose |
| --- | --- |
| [`../README.md`](../README.md) | Main setup, cloud/local notes, safety defaults, and feature overview. |
| [`local_mt5_realtime_validation.md`](local_mt5_realtime_validation.md) | Local Windows/MT5 read-only market-data validation path. |
| [`local_paper_operation_runbook.md`](local_paper_operation_runbook.md) | Human operator procedure for safe paper/demo workflow execution and report interpretation. |
| [`data_provider_quality.md`](data_provider_quality.md) | yfinance FX data-quality limits and the pluggable `MarketDataProvider` interface. |

## Autonomous paper/demo stack

The autonomous stack is a bounded paper/demo pipeline. It does not authorize live trading.

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> Policy Engine -> Autonomous Supervisor -> Reports/Audit
```

| Document | Purpose |
| --- | --- |
| [`autonomous_supervisor.md`](autonomous_supervisor.md) | Bounded foreground supervisor for paper/demo operation. |
| [`autonomous_readiness_gate.md`](autonomous_readiness_gate.md) | Pre-run readiness decision layer. |
| [`autonomous_evidence_builder.md`](autonomous_evidence_builder.md) | Reproducible local evidence generation for readiness. |
| [`autonomous_recovery_planner.md`](autonomous_recovery_planner.md) | Safe recovery recommendations when readiness/evidence blocks. |
| [`autonomous_policy_engine.md`](autonomous_policy_engine.md) | Central policy gate for autonomy permissions and safety decisions. |
| [`autonomous_scenario_runner.md`](autonomous_scenario_runner.md) | Cloud-safe end-to-end scenario simulations. |

## Realtime paper/demo stack

Realtime paper/demo operation validates market data and paper-only behavior without broker-live execution.

| Document | Purpose |
| --- | --- |
| [`realtime_paper_operation.md`](realtime_paper_operation.md) | Realtime data health, realtime paper supervisor, heartbeat, synthetic fallback blocking, and position lifecycle overview. |
| [`realtime_command_center.md`](realtime_command_center.md) | Unified bounded command-center entrypoint for realtime paper/demo checks. |
| [`local_mt5_realtime_validation.md`](local_mt5_realtime_validation.md) | Local MT5 market-data readiness checks before realtime operation. |
| [`local_paper_operation_runbook.md`](local_paper_operation_runbook.md) | End-to-end operator workflow combining MT5 validation, command center, supervisor, positions, and heartbeat review. |

## Operator reporting and analytics

| Document | Purpose |
| --- | --- |
| [`operator_dashboard.md`](operator_dashboard.md) | Offline read-only aggregation of paper/demo report artifacts into one operator status. |
| [`paper_session_bundle.md`](paper_session_bundle.md) | ZIP bundle export with JSON/TXT manifests and SHA-256 checksums. |
| [`paper_performance.md`](paper_performance.md) | Read-only analytics from paper/demo reports and local paper order artifacts. |
| [`paper_session_review.md`](paper_session_review.md) | One post-session review command that composes dashboard, performance, and optional bundle export. |
| [`paper_session_history.md`](paper_session_history.md) | Append-only JSONL ledger of session review snapshots with aggregate JSON/TXT history reports. |
| [`paper_session_trends.md`](paper_session_trends.md) | Offline multi-session trend insights from the paper session history ledger. |

## Decision explainability

| Document | Purpose |
| --- | --- |
| [`decision_traces.md`](decision_traces.md) | Structured, sanitized per-decision traces with gate results and next diagnostic action. |
| [`scoring_formula.md`](scoring_formula.md) | How the 0–100 final score is assembled from layer and component weights. |
| [`min_score_policy.md`](min_score_policy.md) | Static vs adaptive min score, scanner vs demo-bot thresholds, and mismatch warnings. |

## Operator diagnostics

| Document | Purpose |
| --- | --- |
| [`operator_decision_doctor.md`](operator_decision_doctor.md) | One read-only command to diagnose bot state and the primary blocker. |
| [`next_safe_bot_command.md`](next_safe_bot_command.md) | Recommends exactly one safe bounded next command; explain-last-block/decision companions. |
| [`realtime_mt5_decision_blocks.md`](realtime_mt5_decision_blocks.md) | Stale data, spread/ATR, readiness blocks, and why synthetic data is not live-quality. |

## Audit and maintenance

| Document | Purpose |
| --- | --- |
| [`missing_functionality_audit.md`](missing_functionality_audit.md) | Audit of delivered and missing paper/demo platform features. |
| [`stale_issue_resolution_plan.md`](stale_issue_resolution_plan.md) | Manual closure plan for older open issues already implemented by merged PRs. |

## Cloud-safe smoke commands

Run from `apps/forex-scanner`.

```bash
python -m pytest -q tests/test_operator_dashboard.py tests/test_session_bundle.py tests/test_paper_performance.py tests/test_paper_session_review.py tests/test_paper_session_history.py tests/test_paper_session_trends.py --maxfail=1
```

```bash
python scripts/operator_dashboard.py --reports-dir reports --export-json --export-txt
python scripts/export_paper_session_bundle.py --reports-dir reports --output-dir reports/bundles --session-name paper-session-smoke
python scripts/paper_performance_report.py --reports-dir reports --export-json --export-txt
python scripts/paper_session_review.py --reports-dir reports --export-json --export-txt --export-bundle --session-name paper-session-review
python scripts/paper_session_history.py --reports-dir reports --append-latest --session-name paper-session-review --export-json --export-txt
python scripts/paper_session_trends.py --reports-dir reports --window 10 --export-json --export-txt
python scripts/score_decomposition.py --provider synthetic --symbols EUR/USD --style day_trading --export-json --export-txt
python scripts/min_score_policy_report.py --symbols EUR/USD --style day_trading --export-json --export-txt
python scripts/decision_doctor.py --reports-dir reports --export-json --export-txt
python scripts/next_safe_bot_command.py --reports-dir reports
python scripts/explain_last_block.py --reports-dir reports --export-json --export-txt
python scripts/explain_last_decision.py --reports-dir reports --export-json --export-txt
```

## Local MT5 commands

Local MT5 commands require a configured Windows MetaTrader 5 terminal. CI and cloud environments should use mocks, stubs, skips, or synthetic smoke paths.

```bash
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD GBP/USD --timeframes M1 M5 --duration-minutes 15 --interval-seconds 30 --export-json --export-txt --export-csv
```

## Maintainer notes

- Keep generated reports under `reports/` and do not commit them.
- Do not add live-trading or broker-live execution paths through documentation tasks.
- Do not close stale umbrella issues automatically; use [`stale_issue_resolution_plan.md`](stale_issue_resolution_plan.md) for manual review.
