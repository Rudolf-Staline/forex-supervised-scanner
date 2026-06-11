# Autonomous Scenario Runner

The Autonomous Scenario Runner validates the end-to-end autonomous decision stack with synthetic, cloud-safe inputs. It answers: given a system state, what do the Policy Engine, Evidence Builder posture, Readiness Gate state, Recovery Planner, and Autonomous Supervisor simulation decide, and why?

## Safety scope

The runner is strictly paper/demo/read-only:

- it creates synthetic JSON reports in a temporary or explicitly supplied reports directory;
- it does not require MT5, broker connectivity, credentials, or network access;
- it does not mutate `.env`;
- it does not start a daemon or infinite supervisor loop;
- it does not authorize live trading, broker-live execution, MT5 order execution, or order submission paths.

## How scenario testing differs from unit tests

Unit tests verify small policy and report-building functions in isolation. Scenario tests validate a full decision story across the autonomous pipeline:

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> Policy Engine -> Autonomous Supervisor -> Audit Reports
```

Each built-in scenario declares synthetic report inputs, operator controls, readiness status, evidence status, recovery status, the policy action under evaluation, expected policy decision, expected supervisor behavior, expected recovery behavior, and expected blockers or warnings. The runner then compares actual decisions to expectations and marks each scenario `PASS`, `FAIL`, `WARN`, or `SKIP`.

## Built-in scenarios

The built-in suite covers dry-run evidence gaps, PAPER readiness/evidence blocks, stale evidence, healthy PAPER readiness, maintenance/degraded operator controls, failure diagnostics, signal anomalies, readiness-skip safety, manual recovery actions, forbidden live/broker/order paths, and diagnostic dry-run supervisor behavior.

List scenarios:

```bash
python scripts/autonomous_scenario_runner.py --list
```

Run all scenarios and export reports:

```bash
python scripts/autonomous_scenario_runner.py --all --export-json --export-txt
```

Run one scenario:

```bash
python scripts/autonomous_scenario_runner.py --scenario paper_missing_evidence_denied --export-json --export-txt
```

Useful options:

- `--reports-dir <path>` writes synthetic reports and suite exports under a caller-selected directory. Use a temp/test path for CI.
- `--strict` turns expectation mismatches into failures.
- `--fail-fast` stops after the first failed scenario.
- `--include-policy-report` embeds policy decisions in the JSON suite report.
- `--include-recovery-plan` embeds recovery plans and exports per-scenario recovery JSON where applicable.

## Report outputs

The default exports are:

- `reports/autonomous_scenario_suite.json`
- `reports/autonomous_scenario_suite.txt`

The JSON report includes generation time, final suite status, counts for passed/failed/warned/skipped scenarios, scenario results, safety flags, optional policy decisions, optional recovery plans, and output paths.

Each scenario result includes expected vs. actual policy decision, expected vs. actual supervisor behavior, expected vs. actual recovery behavior, mismatches, warnings, blocking reasons, and paths to synthetic reports.

## Interpreting results

- `PASS`: actual policy, supervisor simulation, and recovery posture match the expected scenario definition.
- `WARN`: a non-strict mismatch or documented warning occurred. Re-run with `--strict` in CI if expectation drift should fail the build.
- `FAIL`: strict comparison found a mismatch.
- `SKIP`: reserved for future scenarios that are intentionally not evaluated in the current environment.

## Why this validates the Policy Engine

The central Autonomous Policy Engine is still the authority for policy decisions. The scenario runner builds realistic synthetic contexts around that engine so reviewers can see whether readiness, evidence, operator controls, recovery recommendations, and supervisor simulation agree with the intended safety model.

## Live trading remains unauthorized

Passing the scenario suite does not grant permission to trade live. It only demonstrates that synthetic paper/demo/read-only safety scenarios match expectations. Live trading, broker-live execution, MT5 order execution, and order submission remain denied.
