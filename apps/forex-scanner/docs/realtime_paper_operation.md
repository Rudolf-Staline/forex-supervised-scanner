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

## Safety environment

Every cycle verifies:

- `EXECUTION_MODE=paper`
- `BROKER_MODE=paper` or explicit supported `mt5_demo`
- `ALLOW_LIVE_TRADING=false`
- `AUTO_BOT_ENABLED=false`
- `broker.live_enabled=false`
- `execution_capabilities.broker_live_enabled=false`
- the live confirmation environment variable is unset

Any drift stops the run with `BLOCKED_BY_SAFETY_DRIFT`.

## Local MT5 warning

`--provider mt5` requires a local MT5 terminal and Python package configured on the operator workstation. Cloud tests do not require MT5 and should rely on mocks or synthetic CLI smoke checks. If MT5 fails and the provider path falls back to synthetic data, realtime paper mode stops explicitly with `BLOCKED_SYNTHETIC_FALLBACK` instead of silently accepting the synthetic fallback.

## Realtime paper vs live trading

Realtime paper/demo mode consumes current market data and may run paper-only autonomous checks. Live trading would submit broker-live orders. This readiness layer remains paper/demo only and still does **not** authorize live trading.
