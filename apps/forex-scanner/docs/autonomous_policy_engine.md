# Autonomous Policy Engine

The Autonomous Policy Engine centralizes autonomy permissions and safety decisions for the paper/demo autonomy pipeline. It answers one question for every autonomous action:

> Is this autonomous action allowed under the current mode, evidence, readiness, recovery, operator controls, and safety state?

It is used by the Evidence Builder, Readiness Gate, Recovery Planner, and Autonomous Supervisor to obtain a single, auditable policy decision before proceeding.

## What it does

- Evaluates whether a requested autonomous action (evidence build, readiness check, recovery execution, supervisor invocation) is permitted.
- Applies 11 safety invariants that always hold for paper/demo operation.
- Applies domain-specific rules for each pipeline component.
- Returns an auditable `AutonomousPolicyDecision` with an explicit `ALLOW`, `WARN_ALLOW`, or `DENY` outcome, structured reasons, warnings, rule results, and safety flags.
- Recommends a concrete next action when a request is denied.

## What it does NOT do

- **Does not enable live trading.** Live trading is always denied by policy.
- **Does not call MT5.** No terminal connection, no market data fetch.
- **Does not submit orders.** No `order_send`, no broker order submission.
- **Does not mutate `.env`.** Environment files are never written.
- **Does not create daemons.** No hidden background processes or infinite loops.
- **Does not bypass readiness.** The policy engine cannot unblock readiness for non-dry-run cycles.
- **Does not execute recovery actions.** It authorizes them; the planner executes.

## How it differs from the Readiness Gate

- **Readiness Gate**: inspects the current state of local safety settings, operator controls, paper risk, and report artifacts. Returns `READY`, `WARN_READY`, or a blocking status describing what is wrong.
- **Policy Engine**: decides whether a specific action is allowed given the current mode, evidence status, readiness status, recovery context, and operator state. Returns `ALLOW`, `WARN_ALLOW`, or `DENY` with reasons.

The readiness gate inspects current state. The policy engine decides if an action is allowed.

## How it differs from the Recovery Planner

- **Recovery Planner**: reads blocked/degraded evidence, readiness, and supervisor reports; classifies blocker causes; recommends safe recovery actions.
- **Policy Engine**: authorizes whether a recovery action may be executed. The planner proposes; the policy engine permits or denies.

The recovery planner plans fixes. The policy engine authorizes actions.

## Pipeline position

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> [Policy Engine] -> Autonomous Supervisor -> Audit Reports
```

The policy engine sits between the Recovery Planner and the Autonomous Supervisor in the pipeline, but it is also consulted by every other component at the start of their operations:

- Evidence Builder calls `can_build_evidence()` before generating artifacts.
- Readiness Gate calls `can_run_readiness()` before evaluating readiness.
- Recovery Planner calls `can_execute_recovery_action()` before executing each action.
- Supervisor calls `can_run_supervisor()` before starting cycles and `can_skip_readiness_gate()` before allowing a readiness bypass.

## Decision model

Every policy evaluation returns an `AutonomousPolicyDecision` with a three-level outcome:

| Decision | Meaning |
| --- | --- |
| `ALLOW` | Action is permitted. No warnings, no failures. |
| `WARN_ALLOW` | Action is permitted but warnings were raised by one or more rules. |
| `DENY` | Action is blocked. One or more rules failed with CRITICAL severity. |

The decision includes:

- `allowed` — boolean permission result
- `decision` — `ALLOW`, `WARN_ALLOW`, or `DENY`
- `action` — the action that was evaluated
- `mode` — the operating mode (`DRY_RUN`, `READ_ONLY`, `PAPER`, `DIAGNOSTIC`)
- `reasons` — list of human-readable explanations
- `warnings` — list of warning messages from WARN-status rules
- `blocking_reasons` — list of reasons for denial (empty if allowed)
- `rule_results` — full list of `AutonomousPolicyRuleResult` with rule name, status, reason, and severity
- `safety_flags` — dictionary proving safety invariants hold
- `recommended_next_action` — suggested follow-up when denied
- `timestamp` — UTC timestamp of the decision

## Safety invariants

The policy engine evaluates 11 safety invariants on every policy check. These always pass for paper/demo operation:

| # | Invariant name | Description |
| --- | --- | --- |
| 1 | `no_live_trading` | Live trading is always denied by policy |
| 2 | `no_broker_live` | Broker-live execution is always denied by policy |
| 3 | `no_mt5_order_execution` | MT5 order execution is always denied by policy |
| 4 | `no_order_send` | `order_send` is always denied by policy |
| 5 | `no_env_mutation` | `.env` mutation is always denied by policy |
| 6 | `no_credential_printing` | Credential printing is always denied by policy |
| 7 | `no_hidden_daemon` | Hidden daemon creation is always denied by policy |
| 8 | `no_infinite_loop` | Infinite loops are always denied by policy |
| 9 | `no_readiness_bypass_for_non_dry_run` | Readiness bypass is not allowed for non-dry-run cycles |
| 10 | `recovery_cannot_override_readiness` | Recovery planner cannot override readiness gate decisions |
| 11 | `missing_evidence_cannot_permit_non_dry_run_paper` | Missing/stale/failing evidence cannot permit non-dry-run paper autonomy |

Invariants 1–8 are unconditionally PASS. Invariants 9–11 are conditional on the context and can produce FAIL when violated, resulting in an immediate DENY.

## Domain rules

### Evidence Builder

Evaluated by `can_build_evidence()`:

- `DRY_RUN` mode: always allowed.
- `READ_ONLY` mode: always allowed.
- `PAPER` mode (refresh): denied if `require_mt5` is true; otherwise allowed.
- Subprocess fallback: produces WARN if enabled, PASS if disabled (default safe behavior).

### Readiness Gate

Evaluated by `can_run_readiness()`:

- Readiness checks are always allowed in all modes. The gate is a read-only inspection layer.

### Readiness Gate Skip

Evaluated by `can_skip_readiness_gate()`:

- Allowed only for dry-run diagnostic modes (`DRY_RUN`, `DIAGNOSTIC` with `dry_run=true`).
- Denied for all other modes. Readiness skip must never allow non-dry-run paper supervisor cycles.

### Recovery Planner

Evaluated by `can_execute_recovery_action()`:

- Plan generation is always allowed.
- Manual-review actions are always denied for automatic execution.
- Safe dry-run/read-only actions are allowed in safe modes.
- Unsafe or untagged actions are denied.
- Recovery can never directly unblock the supervisor.

### Supervisor

Evaluated by `can_run_supervisor()`:

- Dry-run or safe-mode invocation: always allowed.
- Non-dry-run paper cycles: require readiness `READY` or `WARN_READY`.
- Blocked evidence denies non-dry-run supervisor cycles.
- Operator maintenance/degraded mode denies supervisor cycles.

Evaluated by `can_run_supervisor_cycle()`:

- Dry-run cycles: always allowed.
- Paper cycles: require readiness `READY`.
- Other safe modes: allowed.

## Policy decisions in reports

Every pipeline component embeds a `policy_decision` field in its output report when the policy engine is consulted. The field contains the serialized `AutonomousPolicyDecision` including the decision type, reasons, warnings, blocking reasons, rule results, and safety flags.

| Component | Report file | Policy field |
| --- | --- | --- |
| Evidence Builder | `autonomous_evidence_summary.json` | `policy_decision` |
| Readiness Gate | `autonomous_readiness_report.json` | `policy_decision` |
| Recovery Planner | `autonomous_recovery_plan.json` | `policy_decision` |
| Supervisor | `autonomous_supervisor_summary.json` | `policy_decision` |
| Policy Engine | `autonomous_policy_report.json` | (top-level decision) |

## CLI usage

From `apps/forex-scanner`:

```bash
python scripts/autonomous_policy_report.py --action build_evidence --mode dry_run --export-json --export-txt
python scripts/autonomous_policy_report.py --action run_supervisor --mode paper --export-json --export-txt
python scripts/autonomous_policy_report.py --action execute_recovery_action --mode read_only --export-json --export-txt
python scripts/autonomous_policy_report.py --action skip_readiness_gate --mode dry_run --export-json --export-txt
```

Options:

- `--action`: the action to evaluate (e.g. `build_evidence`, `run_readiness`, `execute_recovery_action`, `run_supervisor`, `run_supervisor_cycle`, `skip_readiness_gate`)
- `--mode`: the operating mode (`dry_run`, `read_only`, `paper`, `diagnostic`)
- `--export-json`: write `reports/autonomous_policy_report.json`
- `--export-txt`: write `reports/autonomous_policy_report.txt`

## Report outputs

When requested, the policy engine writes:

- `reports/autonomous_policy_report.json`
- `reports/autonomous_policy_report.txt`

The JSON report is a serialized `AutonomousPolicyDecision` with all fields. The text report is a human-readable summary with the decision, reasons, warnings, blocking reasons, rule results, and safety flags.

## Operating modes

| Mode | Description |
| --- | --- |
| `DRY_RUN` | Validation only. No evidence artifacts generated, no cycles run. |
| `READ_ONLY` | Reads existing local artifacts under `reports/`. No mutations. |
| `PAPER` | Paper/demo/synthetic operation. Bounded, foreground, no broker-live. |
| `DIAGNOSTIC` | Diagnostic inspection mode. Safe for cloud CI. |

## Configuration

The policy engine is configured via `AutonomousPolicyConfig`:

- `mode` — operating mode (default: `DRY_RUN`)
- `dry_run` — whether the pipeline is in dry-run mode (default: `true`)
- `allow_subprocess_fallback` — whether subprocess fallback is permitted (default: `false`)
- `require_mt5` — whether MT5 terminal is required (default: `false`)
- `operator_mode` — operator control state: `normal`, `maintenance`, or `degraded` (default: `normal`)
- `readiness_status` — current readiness gate status (default: `UNKNOWN`)
- `evidence_status` — current evidence builder status (default: `UNKNOWN`)
- `skip_readiness_gate` — whether readiness gate skip is requested (default: `false`)
- `recovery_action_safe` — whether the recovery action is marked safe (default: `false`)
- `recovery_action_manual` — whether the recovery action is manual-review (default: `false`)
- `recovery_can_override_readiness` — whether recovery can override readiness (default: `false`)

## Safety guarantees

The policy engine is intentionally permission-only:

- no live trading;
- no broker-live execution;
- no MT5 order execution;
- no `order_send` call;
- no `.env` mutation;
- no credential printing;
- no hidden daemon;
- no infinite loop;
- no readiness bypass for non-dry-run;
- recovery cannot override readiness;
- missing evidence blocks non-dry-run paper autonomy.

## Why this still does not authorize live trading

The policy engine is a paper/demo-only permission layer. Every decision includes safety flags proving that `live_trading_enabled`, `live_execution_allowed`, `broker_live_execution_allowed`, `broker_order_submission_allowed`, and `mt5_order_execution_allowed` are all `false`. The first four safety invariants unconditionally deny live trading, broker-live execution, MT5 order execution, and `order_send`. No policy mode, configuration, or action can override these invariants. The policy engine authorizes paper/demo diagnostic actions only and must not be used as evidence that live trading is approved.

## Scenario-based validation

Use the Autonomous Scenario Runner to validate how this component behaves as part of the wider autonomous stack. The runner creates synthetic local reports, evaluates policy decisions, simulates supervisor outcomes, and can recommend recovery plans without MT5, network access, `.env` mutation, daemon creation, live trading, broker-live execution, or order submission.

```bash
python scripts/autonomous_scenario_runner.py --list
python scripts/autonomous_scenario_runner.py --all --export-json --export-txt --strict
```

See [Autonomous Scenario Runner](autonomous_scenario_runner.md) for scenario definitions, report schema, and interpretation guidance. Passing scenarios are audit evidence only; they do not authorize live trading.
