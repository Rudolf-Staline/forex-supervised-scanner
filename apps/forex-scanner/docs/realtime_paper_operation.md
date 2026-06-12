# Realtime Paper/Demo Operation Readiness

The realtime paper readiness layer is a bounded, foreground-only safety wrapper for validating real market data before paper/demo operation. It **does not authorize live trading**.

## What it does

- Checks market data freshness, spread availability, spread/ATR, missing bars, duplicate bars, provider fallback, synthetic fallback, MT5 usage, and an overall data quality score.
- Runs realtime data health first, then autonomous evidence, readiness, and policy before any paper supervisor cycle is allowed.
- Writes a heartbeat record for every cycle, including data health, evidence, readiness, policy, operator controls, and safety flags.
- Stops on stale data, synthetic fallback (`BLOCKED_SYNTHETIC_FALLBACK`), provider failures, operator maintenance/degraded mode, evidence failure, readiness denial, policy denial, or safety environment drift.
- Stops at `--max-cycles` or `--max-runtime-minutes`; it is not a daemon.

## What it never does

- It does not enable live trading.
- It does not add broker-live execution.
- It does not call `order_send`.
- It does not mutate `.env`.
- It does not require MT5 for cloud tests.

## Data check

```bash
python scripts/realtime_data_check.py --provider mt5 --symbols EUR/USD GBP/USD --timeframe M1 --export-json --export-txt
```

For CI or offline validation, use synthetic data. Synthetic data is useful for checking the CLI and report path, but it is blocking for realtime paper mode by design. Reports distinguish an explicitly requested synthetic provider from an automatic synthetic fallback, and both remain unsafe for realtime paper operation:

```bash
python scripts/realtime_data_check.py --provider synthetic --symbols EUR/USD --timeframe M1 --export-json --export-txt
```

## Bounded paper supervisor

```bash
python scripts/realtime_paper_supervisor.py --provider mt5 --symbols EUR/USD --timeframe M1 --interval-seconds 60 --max-cycles 5 --dry-run --export-json --export-txt
```

The supervisor writes:

- `reports/realtime_data_health.json`
- `reports/realtime_data_health.txt`
- `reports/realtime_paper_supervisor_summary.json`
- `reports/realtime_paper_supervisor_report.txt`
- `reports/realtime_heartbeat.jsonl`

The supervisor JSON/TXT summary includes `evidence_status`; heartbeat entries include per-cycle `evidence_status`, `heartbeat_sequence`, `runtime_safety_heartbeat`, `paper_demo_only`, and `live_execution_allowed=false` so operators can prove evidence ran before readiness and policy allowed paper-only supervision without live execution.


## Local MT5 realtime validation before paper operation

Before using local Windows MT5 market data for realtime paper/demo workflows, operators can run the read-only validation runbook:

```bash
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD GBP/USD --timeframes M1 M5 --duration-minutes 15 --interval-seconds 30 --export-json --export-txt
```

The validation is local-only and Windows/MT5-dependent. It checks MT5 package import, terminal initialization, account and terminal info reads, symbol resolution and selection, latest requested timeframe candles, latest tick, candle age, spread, spread/ATR when possible, missing bars, duplicate bars, provider latency (`provider_latency_ms`), and bounded repeated polling. It validates market-data readiness only; it does not authorize live trading, does not call `order_send`, does not submit broker orders, does not mutate `.env`, does not run as a daemon, and does not add live broker execution capability.

CI must rely on mocks/stubs only. A real MT5 terminal is not required in CI, and the CLI remains CI-safe by returning a blocked report rather than failing solely because MT5 is absent unless `--strict` is explicitly supplied. See [`local_mt5_realtime_validation.md`](local_mt5_realtime_validation.md).

## Realtime data thresholds

Both CLI entry points expose the same data-quality controls so operators can tighten local readiness checks without editing code or `.env`:

- `--max-data-age-seconds` overrides the default stale-candle cutoff.
- `--min-data-quality-score` blocks symbols below the minimum quality score.
- `--warn-data-quality-score` emits a warning below the warning threshold when the symbol is otherwise safe.
- `--max-spread-atr-ratio` blocks candles whose latest spread is too large relative to ATR.

The supervisor forwards those values into the realtime data-health check on every bounded foreground cycle, before evidence, readiness, policy, or paper-only autonomous supervision can run.

## Safety environment

Every cycle verifies:

- `EXECUTION_MODE=paper`
- `BROKER_MODE=paper` or explicit supported `mt5_demo`
- `ALLOW_LIVE_TRADING=false`
- `AUTO_BOT_ENABLED=false`
- `broker.live_enabled=false`
- `execution_capabilities.broker_live_enabled=false`
- the live confirmation environment variable is unset

Safety drift is checked at cycle start and again before a successful cycle heartbeat is written, so a paper-only runner cannot finish a cycle after flipping a live-trading guard. Any drift stops the run with `BLOCKED_BY_SAFETY_DRIFT`.

## Local MT5 warning

`--provider mt5` requires a local MT5 terminal and Python package configured on the operator workstation. Cloud tests do not require MT5 and should rely on mocks or synthetic CLI smoke checks. If MT5 fails and the provider path falls back to synthetic data, realtime paper mode stops explicitly with `BLOCKED_SYNTHETIC_FALLBACK` instead of silently accepting the synthetic fallback.

## Realtime paper vs live trading

Realtime paper/demo mode consumes current market data and may run paper-only autonomous checks. Live trading would submit broker-live orders. This readiness layer remains paper/demo only and still does **not** authorize live trading.

## Realtime paper position lifecycle manager

The realtime paper position manager advances **local paper orders only** after a paper order already exists. It models the lifecycle:

```text
signal -> paper order -> activation -> open position -> TP/SL/partial exits -> breakeven -> close/cancel -> audit
```

It supports pending entry activation, setup invalidation before activation, stop-loss and take-profit closure, TP1/TP2/TP3 partial exits, breakeven stop movement after TP1 when configured, stale-data blocking, session-close warnings, spread-too-wide warnings or blocking, configured gap-through-entry behavior, and conservative stop-first processing when a candle gaps through both stop and target.

Run it directly:

```bash
python scripts/realtime_paper_positions.py --provider synthetic --symbols EUR/USD --timeframe M1 --dry-run --export-json --export-txt
```

Expected exports:

- `reports/realtime_paper_positions.json`
- `reports/realtime_paper_positions.txt`

The report includes `started_at`, `completed_at`, `provider`, `symbols`, `timeframe`, `positions_seen`, `pending_orders_seen`, `positions_updated`, `positions_closed`, `partial_exits_created`, `breakeven_moves`, `invalidations`, `warnings`, `blocking_reasons`, `safety_flags`, and `output_paths`.

The manager never calls `order_send`, never enables live trading, never mutates `.env`, never creates a daemon, and does not require MT5 in CI. `--dry-run` evaluates lifecycle transitions and writes reports without persisting destructive paper-order updates.

## Supervisor-managed positions

The bounded realtime paper supervisor can optionally run the position manager after data health, safety heartbeat, evidence, readiness, and policy checks:

```bash
python scripts/realtime_paper_supervisor.py --provider synthetic --symbols EUR/USD --timeframe M1 --interval-seconds 0 --max-cycles 1 --dry-run --manage-positions --export-json --export-txt
```

When `--manage-positions` is present, supervisor reports include a `position_lifecycle_summary` plus per-cycle counts for `positions_updated`, `positions_closed`, and `partial_exits_created`, while still remaining paper/demo-only.

## Realtime Paper Command Center

For day-to-day operator use, prefer the unified command center when you want one bounded paper/demo command to coordinate data health, evidence, readiness, policy, optional recovery planning, optional scenarios, supervisor execution, optional position lifecycle management, and final reporting:

```bash
python scripts/realtime_command_center.py --provider synthetic --symbols EUR/USD --timeframe M1 --dry-run --max-cycles 1 --export-json --export-txt
```

Expected command-center exports:

- `reports/realtime_command_center_summary.json`
- `reports/realtime_command_center_report.txt`

The command center is still paper/demo only. Synthetic data remains diagnostic and blocks realtime paper operation with `BLOCKED_SYNTHETIC_FALLBACK`; MT5 is not required in CI; no live trading, broker-live execution, `.env` mutation, daemon, infinite loop, or broker order submission is added. See [`realtime_command_center.md`](realtime_command_center.md).
