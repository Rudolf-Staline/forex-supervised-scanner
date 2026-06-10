# Autonomous Readiness Gate (paper/demo only)

The Autonomous Readiness Gate answers one question before the Autonomous Supervisor is allowed to run:

> Should the autonomous supervisor run right now?

It is a conservative, read-only pre-run decision layer. It does **not** replace the supervisor and does **not** authorize live trading. The gate never calls MT5, never sends orders, and never mutates `.env`.

## How it differs from the supervisor

- **Readiness Gate**: inspects local safety settings, operator controls, paper risk, and report artifacts under `reports/`; returns `READY`, `WARN_READY`, or a blocking status.
- **Autonomous Supervisor**: runs bounded foreground dry-run or paper/demo cycles after the gate allows it.

Missing evidence is intentionally non-permissive. Dry-run diagnostics may receive `WARN_READY` when configured, but non-dry-run paper autonomy requires fresh local evidence.

## Conservative defaults

```bash
AUTONOMOUS_READINESS_MAX_REPORT_AGE_MINUTES=1440
AUTONOMOUS_READINESS_REQUIRE_SESSION_HEALTH=true
AUTONOMOUS_READINESS_REQUIRE_DATA_HEALTH=true
AUTONOMOUS_READINESS_REQUIRE_FAILURE_DIAGNOSTICS=true
AUTONOMOUS_READINESS_MIN_DATA_QUALITY=70
AUTONOMOUS_READINESS_ALLOW_WARN_READY_FOR_DRY_RUN=true
AUTONOMOUS_READINESS_BLOCK_ON_ANOMALIES=true
AUTONOMOUS_READINESS_BLOCK_ON_MAINTENANCE=true
AUTONOMOUS_READINESS_BLOCK_ON_DEGRADED=true
```

## Inputs inspected

The gate uses existing project artifacts where available:

- central demo/paper safety mode via `ensure_demo_bot_safe_mode(...)`;
- operator `maintenance_mode` and `degraded_mode`;
- current paper daily-risk summary;
- `reports/session_health_summary.json`;
- `reports/data_health_report.json`;
- `reports/failure_diagnostics_summary.json`;
- optional `reports/signal_anomaly_summary.json`;
- optional `reports/autonomous_supervisor_summary.json`;
- optional `reports/mt5_symbol_mapping_audit.json`;
- report freshness under `reports/`.

## Final statuses

The final readiness status is one of:

- `READY`
- `WARN_READY`
- `BLOCKED_BY_SAFETY`
- `BLOCKED_BY_OPERATOR_CONTROL`
- `BLOCKED_BY_DATA_QUALITY`
- `BLOCKED_BY_SESSION_HEALTH`
- `BLOCKED_BY_RISK`
- `BLOCKED_BY_STALE_REPORTS`
- `BLOCKED_BY_NO_EVIDENCE`

Only `READY` allows non-dry-run paper cycles. `WARN_READY` can allow dry-run diagnostics when `AUTONOMOUS_READINESS_ALLOW_WARN_READY_FOR_DRY_RUN=true`.

## Readiness-only CLI

From `apps/forex-scanner`:

```bash
python scripts/autonomous_readiness_report.py --export-json --export-txt
```

Expected outputs:

- `reports/autonomous_readiness_report.json`
- `reports/autonomous_readiness_report.txt`

The command prints the final status plus blocking and warning reasons. It is cloud-safe and does not require MT5.

## Supervisor integration

The supervisor builds a readiness report before cycles start. It includes that report in exported supervisor JSON and can export standalone readiness files:

```bash
python scripts/run_autonomous_supervisor.py \
  --once --symbols EUR/USD --dry-run \
  --readiness-only --export-readiness-json --export-readiness-txt
```

Diagnostic override:

```bash
python scripts/run_autonomous_supervisor.py --enabled --dry-run --skip-readiness-gate --once --symbols EUR/USD
```

`--skip-readiness-gate` is diagnostic-only and is accepted only with `--dry-run`. It never allows non-dry-run paper cycles when readiness is blocking.

## Live-trading warning

This gate remains strictly paper/demo only. It does not add broker-live execution, does not call order submission APIs, and must not be used as evidence that live trading is approved.

## Evidence Builder integration

Before evaluating the readiness gate, operators can refresh local evidence with the Autonomous Evidence Builder:

```bash
python scripts/autonomous_readiness_report.py --build-evidence-first --evidence-mode read-only --export-json --export-txt
```

The resulting pipeline is:

```text
Evidence Builder -> Readiness Gate -> Autonomous Supervisor -> Reports/Audit
```

The readiness gate remains conservative: missing required evidence, stale evidence, poor data health, failing session health, or blocking failure diagnostics can prevent paper autonomy. Optional evidence such as anomaly detection and static MT5 symbol mapping contributes warnings without requiring MT5 in CI. This still does not authorize live trading or broker execution.

## Recovery planning on readiness block

When readiness is blocked, the next safe step is recovery planning rather than supervisor execution:

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> Autonomous Supervisor -> Audit Reports
```

Generate a plan alongside a blocked readiness report with:

```bash
python scripts/autonomous_readiness_report.py --build-evidence-first --evidence-mode read-only --plan-recovery-on-block --export-recovery-json --export-recovery-txt
```

The Recovery Planner reads readiness/evidence/report artifacts, classifies blocker causes, and recommends bounded dry-run/read-only diagnostics or manual reviews. It never changes readiness status itself and never bypasses the gate.

## Policy Engine Integration

The Readiness Gate now consults the Autonomous Policy Engine via `can_run_readiness()` before evaluating readiness checks. The policy engine verifies that readiness inspection is permitted under the current operating mode and safety state. Since readiness checks are a read-only inspection layer, `can_run_readiness()` is allowed in all modes.

The policy decision is included in the readiness report under the `policy_decision` field of `reports/autonomous_readiness_report.json`. The decision contains the full rule evaluation results, safety flags, and any warnings.

The updated safe autonomy pipeline is:

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> [Policy Engine] -> Autonomous Supervisor -> Audit Reports
```

The policy engine does not change readiness evaluation behavior. It provides an auditable permission check before the gate runs. This remains paper/demo-only and does not authorize live trading. See [`autonomous_policy_engine.md`](autonomous_policy_engine.md).
