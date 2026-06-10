# Autonomous Evidence Builder

The Autonomous Evidence Builder creates the local report artifacts consumed by the Autonomous Readiness Gate. It is a bounded, foreground-only orchestration layer over existing read-only report builders; it does **not** add live trading, broker-live execution, MT5 terminal requirements, or any call to `order_send`.

## Pipeline

```text
Evidence Builder -> Readiness Gate -> Autonomous Supervisor -> Reports/Audit
```

The builder should be run before readiness evaluation when operators want a reproducible local evidence refresh for paper/demo autonomy diagnostics.

## Modes

- `dry-run`: prints/builds the task plan only. It does not generate evidence artifacts unless summary export flags are passed.
- `read-only`: default conservative mode. It generates reports from existing local artifacts under `reports/`.
- `refresh`: reserved for safe project-supported synthetic/paper refreshes. It remains paper/demo/read-only and still does not authorize broker execution.

## Evidence tasks

Default tasks orchestrate existing report code instead of duplicating report logic:

| Task | Output | Required for readiness? | Safety posture |
| --- | --- | --- | --- |
| Session health summary | `reports/session_health_summary.json` | Required by default | Local artifacts only |
| Data health report | `reports/data_health_report.json`, `reports/data_health_report.txt` | Required by default | Local artifacts only |
| Failure diagnostics | `reports/failure_diagnostics_summary.json`, `reports/failure_diagnostics_report.txt` | Required by default | Local artifacts only |
| Signal anomaly detector | `reports/signal_anomaly_summary.json` | Optional | Local signal journals only |
| MT5 symbol mapping audit | `reports/mt5_symbol_mapping_audit.json` | Optional | Static/no-terminal/no-MT5 mode |
| Readiness report | `reports/autonomous_readiness_report.json`, `.txt` | Optional builder step | Runs after evidence generation |

Missing or weak optional evidence produces `WARN`/`SKIP`; missing or failing required evidence can block paper autonomy after the readiness gate evaluates the generated artifacts.

## Running diagnostics

From `apps/forex-scanner`:

```bash
python scripts/autonomous_evidence_builder.py --mode dry-run --export-json --export-txt
python scripts/autonomous_evidence_builder.py --mode read-only --include-readiness --export-json --export-txt
python scripts/autonomous_readiness_report.py --build-evidence-first --evidence-mode read-only --export-json --export-txt
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --build-evidence-first --evidence-mode read-only --readiness-only --export-json --export-txt
```

The CLI prints the final evidence status, task counts, report paths, whether readiness was re-evaluated, and a safety warning.

## Summary schema

`reports/autonomous_evidence_summary.json` includes:

- `generated_at`
- `mode`
- `final_status`: `READY_EVIDENCE`, `WARN_EVIDENCE`, `BLOCKED_EVIDENCE`, or `DRY_RUN_PLAN`
- task counters and `task_results`
- `blocking_failures`
- `output_paths`
- optional embedded `readiness_report`
- `safety_flags`

## Safety guarantees

The builder is intentionally diagnostic-only:

- no live trading;
- no broker-live execution;
- no MT5 order execution;
- no `order_send` call;
- no `.env` mutation;
- no credential printing;
- no hidden daemon;
- no infinite loop;
- no network dependency for cloud-safe tests.

These reports are readiness evidence only. They never authorize live trading.

## Recovery planning on blocked evidence

The safe autonomy pipeline now includes the recovery planner:

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> Autonomous Supervisor -> Audit Reports
```

If evidence generation blocks, operators can request a bounded recovery plan without running supervisor cycles:

```bash
python scripts/autonomous_evidence_builder.py --mode read-only --plan-recovery-on-block --export-recovery-json --export-recovery-txt
```

The recovery plan recommends safe dry-run/read-only diagnostics or manual review actions. It does not bypass readiness and does not authorize live trading.
