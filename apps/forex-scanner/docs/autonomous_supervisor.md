# Autonomous Supervisor v0 (paper/demo only)

## Purpose

Autonomous Supervisor v0 is a conservative orchestration layer for Forex Supervisor paper/demo operation. It runs the existing demo bot in bounded foreground cycles so operators can observe supervised autonomy without enabling live trading or broker-live execution.

It is intentionally limited to paper/demo workflows. This feature does **not** authorize live trading.

## Safety model

Before every cycle, the supervisor calls the central demo-bot safety lock and only then uses `DemoBotService.run_cycle(...)` as its execution primitive. The supervisor:

- requires the normal paper/demo safety environment (`EXECUTION_MODE=paper`, `ALLOW_LIVE_TRADING=false`, `BROKER_MODE=paper`, `AUTO_BOT_ENABLED=false`);
- defaults to disabled and dry-run mode;
- never mutates `.env` files;
- never prints credentials;
- never creates a hidden daemon, scheduler, or default infinite loop;
- never enables broker-live execution;
- never performs broker order submission;
- writes report safety flags proving live execution was not allowed.

## Environment variables

The conservative defaults are:

```bash
AUTONOMOUS_SUPERVISOR_ENABLED=false
AUTONOMOUS_SUPERVISOR_MAX_CYCLES=3
AUTONOMOUS_SUPERVISOR_INTERVAL_SECONDS=300
AUTONOMOUS_SUPERVISOR_DRY_RUN=true
AUTONOMOUS_SUPERVISOR_MAX_CONSECUTIVE_FAILURES=2
AUTONOMOUS_SUPERVISOR_MAX_ZERO_ORDER_CYCLES=3
```

Required paper/demo safety variables remain:

```bash
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
BROKER_MODE=paper
AUTO_BOT_ENABLED=false
```

## Usage examples

From `apps/forex-scanner`:

```bash
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --export-json --export-txt
```

Run a bounded enabled dry-run loop:

```bash
python scripts/run_autonomous_supervisor.py --enabled --dry-run --max-cycles 3 --interval-seconds 300 --symbols EUR/USD GBP/USD
```

Run one enabled paper/demo cycle that may create paper orders only:

```bash
python scripts/run_autonomous_supervisor.py --enabled --no-dry-run --once --symbols EUR/USD
```

Use a named watchlist:

```bash
python scripts/run_autonomous_supervisor.py --enabled --dry-run --watchlist major_forex --max-cycles 1
```

## Reports

When exports are requested, the supervisor writes:

- `reports/autonomous_supervisor_summary.json`
- `reports/autonomous_supervisor_report.txt`

Reports include:

- `started_at`
- `completed_at`
- `cycle_count`
- `style`
- `symbols`
- `watchlist`
- `dry_run`
- `final_status`
- `stop_reason`
- paper orders created
- risk summaries
- safety flags proving live execution was not allowed

## Autonomy limits

The supervisor stops when any configured bound or safety condition is reached:

- `max_cycles` is reached;
- operator `maintenance_mode` or `degraded_mode` is active;
- safety checks block paper/demo operation;
- consecutive failures reach `AUTONOMOUS_SUPERVISOR_MAX_CONSECUTIVE_FAILURES`;
- consecutive zero-paper-order, rejected, or blocked cycles reach `AUTONOMOUS_SUPERVISOR_MAX_ZERO_ORDER_CYCLES`.

Expected conservative stops such as dry-run, safety block, operator controls, or risk stops are normal outcomes for demo operation, not live-trading permission.

## Explicit live-trading warning

Autonomous Supervisor v0 does **not** authorize live trading. It does not add broker-live execution and must not be used as evidence that a real-money broker account is approved for automated execution.
