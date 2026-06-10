# Autonomous Supervisor v0 (paper/demo only)

## Purpose and canonical API

Autonomous Supervisor v0 is a conservative orchestration layer for Forex Supervisor paper/demo operation. It runs the existing demo bot in bounded foreground cycles so operators can observe supervised autonomy without enabling live trading or broker-live execution.

The canonical implementation lives at:

```text
app/execution/autonomous_supervisor.py
```

The legacy compatibility import path remains available, but it only re-exports the canonical API:

```text
app/supervisor/autonomous.py
```

This feature does **not** authorize live trading.

## Safety model

Before every attempted cycle, the supervisor calls the central demo-bot safety lock, `ensure_demo_bot_safe_mode(...)`, and only then uses `DemoBotService.run_cycle(...)` as its paper execution primitive. In disabled or dry-run mode it still validates the same safety lock and skips paper order creation.

The supervisor:

- requires the normal paper/demo safety environment (`EXECUTION_MODE=paper`, `ALLOW_LIVE_TRADING=false`, `BROKER_MODE=paper`, `AUTO_BOT_ENABLED=false`);
- defaults to disabled and dry-run mode;
- never mutates `.env` files;
- never prints broker credentials;
- never creates a hidden daemon, scheduler, or default infinite loop;
- never enables broker-live execution;
- never calls broker or MT5 order submission;
- runs with bounded `max_cycles` when invoked by the CLI;
- can run with the synthetic provider in cloud/paper mode without MT5 installed;
- writes report safety flags proving live execution was not allowed.

## Safe default environment variables

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

## Canonical CLI contract

From `apps/forex-scanner`, use:

```bash
python scripts/run_autonomous_supervisor.py [options]
```

Canonical public options:

- `--style`: trading style to pass to the demo bot.
- `--symbols`: one or more symbols to scan; ignored when `--watchlist` is supplied.
- `--watchlist`: named paper/demo watchlist.
- `--once`: force one bounded cycle regardless of `--max-cycles`.
- `--max-cycles`: maximum foreground cycles; default is `3` from the conservative config.
- `--interval-seconds`: seconds between bounded cycles.
- `--dry-run` / `--no-dry-run`: keep validation-only mode or allow paper order creation through `DemoBotService` only.
- `--export-json`: write `reports/autonomous_supervisor_summary.json`.
- `--export-txt`: write `reports/autonomous_supervisor_report.txt`.

Backward-compatible aliases from the early v0 prototype are preserved and documented:

- `--cycles`: alias for `--max-cycles`.
- `--no-export`: no-op safety alias because exports are already opt-in unless `--export-json` or `--export-txt` is supplied.
- `--no-sleep`: sets the interval to `0` for bounded local validation loops.

Expected conservative stops such as dry-run completion, safety block, operator controls, and risk stops return CLI success. Runtime implementation failures return failure.

## Usage examples

Run the default safe dry-run validation once and export reports:

```bash
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --export-json --export-txt
```

Run a bounded enabled dry-run loop:

```bash
python scripts/run_autonomous_supervisor.py --enabled --dry-run --max-cycles 3 --interval-seconds 300 --symbols EUR/USD GBP/USD
```

Run one bounded paper/demo cycle that may create **paper orders only**:

```bash
python scripts/run_autonomous_supervisor.py --enabled --no-dry-run --once --symbols EUR/USD
```

Run bounded cloud-safe paper validation without MT5 using synthetic data:

```bash
python scripts/run_autonomous_supervisor.py --provider synthetic --enabled --dry-run --max-cycles 1 --interval-seconds 0 --symbols EUR/USD
```

Use a named watchlist:

```bash
python scripts/run_autonomous_supervisor.py --enabled --dry-run --watchlist major_forex --max-cycles 1
```

## Reports

When exports are requested, the supervisor writes:

- `reports/autonomous_supervisor_summary.json`
- `reports/autonomous_supervisor_report.txt`

Reports use the stable top-level contract:

- `started_at`
- `completed_at`
- `cycle_count`
- `style`
- `symbols`
- `watchlist`
- `dry_run`
- `final_status`
- `stop_reason`
- `orders_created`
- `risk_summaries`
- `safety_flags`

The safety flags include explicit evidence that `live_execution_allowed`, `broker_live_execution_allowed`, and `broker_order_submission_allowed` were false, and that no hidden daemon or default infinite loop was created.

## Autonomy limits

The supervisor stops when any configured bound or safety condition is reached:

- `max_cycles` is reached;
- operator `maintenance_mode` or `degraded_mode` is active;
- safety checks block paper/demo operation;
- consecutive failures reach `AUTONOMOUS_SUPERVISOR_MAX_CONSECUTIVE_FAILURES`;
- consecutive zero-paper-order, rejected, or blocked cycles reach `AUTONOMOUS_SUPERVISOR_MAX_ZERO_ORDER_CYCLES`.

## Explicit live-trading warning

Autonomous Supervisor v0 does **not** authorize live trading. It does not add broker-live execution, cannot enable `ALLOW_LIVE_TRADING`, does not submit broker orders, and must not be used as evidence that a real-money broker account is approved for automated execution.

## Autonomous Readiness Gate

Before cycles start, the supervisor now builds an Autonomous Readiness Gate report. The gate checks central paper/demo safety, operator controls, paper risk, recent session/data/failure-diagnostics evidence, optional anomaly and mapping audits, and report freshness.

Non-dry-run paper cycles require `READY`. Dry-run diagnostics may continue with `WARN_READY` when conservative defaults allow it. Blocking statuses stop the supervisor before any cycle is attempted and the exported supervisor JSON embeds the readiness report.

Useful commands:

```bash
python scripts/autonomous_readiness_report.py --export-json --export-txt
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --readiness-only --export-readiness-json --export-readiness-txt
```

`--skip-readiness-gate` is diagnostic-only and only accepted in dry-run mode; it cannot enable non-dry-run paper cycles while readiness is blocking. See [`autonomous_readiness_gate.md`](autonomous_readiness_gate.md).

## Building evidence before supervisor runs

The supervisor CLI can run the Autonomous Evidence Builder before invoking the readiness gate:

```bash
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --build-evidence-first --evidence-mode read-only --readiness-only --export-json --export-txt
```

For non-dry-run paper supervisor cycles, blocking evidence failures prevent the supervisor from running. Dry-run diagnostics can still print evidence and readiness results without creating paper orders. The sequence is:

```text
Evidence Builder -> Readiness Gate -> Autonomous Supervisor -> Reports/Audit
```

The feature remains paper/demo-only and does not enable live trading, broker-live execution, MT5 order execution, or `order_send`.
