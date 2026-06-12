# Realtime Paper Command Center

The Realtime Paper Command Center is the single operator-facing entrypoint for safe bounded paper/demo realtime operation. It coordinates the runtime safety heartbeat, realtime data health, evidence builder, readiness gate, policy engine, optional recovery planning, optional autonomous scenarios, realtime paper supervisor, optional paper position manager, and a final operator summary.

It **does not enable live trading**. It does not add broker-live execution, call `order_send`, mutate `.env`, require MT5 in CI, start a daemon, or run an infinite loop.

## Run

```bash
python scripts/realtime_command_center.py --provider synthetic --symbols EUR/USD --timeframe M1 --dry-run --max-cycles 1 --export-json --export-txt
```

Synthetic data is useful for a cloud-safe smoke test and report validation, but it is still blocked for realtime paper operation. The command should complete and write a report whose final status is blocked by synthetic fallback rather than treating synthetic candles as broker-quality realtime data.

For an operator workstation with real/demo market data configured, use a bounded run such as:

```bash
python scripts/realtime_command_center.py --provider mt5 --symbols EUR/USD GBP/USD --timeframe M1 --dry-run --max-cycles 1 --interval-seconds 0 --build-evidence-first --plan-recovery-on-block --export-json --export-txt
```

## CLI options

- `--provider synthetic|yahoo|mt5|auto`: data provider selection. Synthetic remains diagnostic and blocking for realtime paper operation.
- `--symbols`: explicit symbol list such as `EUR/USD GBP/USD`.
- `--watchlist`: configured watchlist name.
- `--timeframe`: scanner timeframe, for example `M1`.
- `--interval-seconds`: bounded delay between supervisor cycles.
- `--max-cycles`: hard cycle cap; prevents daemon behavior.
- `--max-runtime-minutes`: optional hard runtime cap.
- `--dry-run`: diagnostic paper-safe mode.
- `--build-evidence-first`: explicitly build evidence before supervisor operation.
- `--run-scenarios`: include the autonomous scenario suite in the command-center audit trail.
- `--manage-positions`: include the realtime paper position lifecycle manager when the supervisor completes a safe cycle.
- `--plan-recovery-on-block`: include a recovery plan when blockers are found.
- `--export-json`: write JSON reports.
- `--export-txt`: write text reports.
- `--reports-dir`: target report directory; defaults to `reports`.
- `--strict`: use strict scenario behavior when scenarios are requested.

## Expected command-center reports

When export flags are used, the command center writes:

- `reports/realtime_command_center_summary.json`
- `reports/realtime_command_center_report.txt`

Other stage reports may also be written by delegated safe components, including realtime data health, evidence, supervisor heartbeat, supervisor summary, scenario suite, recovery plan, and position-manager reports.

## Summary fields

The summary includes:

- `final_status`
- `data_health_status`
- `evidence_status`
- `readiness_status`
- `policy_decision`
- `recovery_plan_status`
- `scenario_suite_status` when `--run-scenarios` is used
- `supervisor_status`
- `position_manager_status` when `--manage-positions` is used
- `paper_orders_created`
- `paper_positions_updated`
- `stop_reason`
- `blocking_reasons`
- `warnings`
- `safety_flags`
- `output_paths`
- per-stage audit records under `stages`

## Stage order

The command center records stages in this order:

1. Runtime safety heartbeat / safety drift check.
2. Realtime data health.
3. Evidence builder.
4. Readiness gate.
5. Policy engine.
6. Recovery planner, only when requested and blockers exist.
7. Autonomous scenario runner, only when requested.
8. Realtime paper supervisor.
9. Realtime paper position manager, only when requested and the supervisor completes a safe cycle.
10. Final operator summary exported as JSON/TXT.

## Safety invariants

The command center remains paper/demo only:

- no live trading
- no broker-live execution
- no broker order submission
- no MT5 order execution
- no `.env` mutation
- no hidden daemon
- no infinite loop
- no MT5 dependency in CI

If safety drift is detected, the command center and supervisor report blocking reasons such as `BLOCKED_BY_SAFETY_DRIFT` and keep order/position side effects at zero unless the injected paper-only test doubles report local paper updates.
