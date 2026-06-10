# Autonomous Supervisor v0 (paper/demo only)

Autonomous Supervisor v0 is a **bounded foreground runner** for the existing paper/demo bot. It is designed for local paper/demo operation and for producing an auditable run report.

## Non-goals and safety constraints

This feature intentionally does **not** add live trading:

- no live broker mode is enabled;
- no MT5 demo or live submission path is called;
- no `order_send` call is introduced;
- no hidden daemon, service manager, scheduler, or background process is created;
- no subprocess is spawned by the supervisor;
- no report authorizes broker execution.

The supervisor blocks before scanning unless the safety environment is paper-only:

```bash
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
BROKER_MODE=paper
AUTO_BOT_ENABLED=false
```

## What v0 does

For each explicitly requested foreground cycle, the supervisor:

1. builds the daily safe-operations checklist in paper mode;
2. evaluates the safety environment doctor in paper mode;
3. enforces the central demo safety lock;
4. runs the existing `DemoBotService` for paper order simulation only;
5. writes an audit summary to `reports/autonomous_supervisor_last_run.json` and `reports/autonomous_supervisor_last_run.md` unless `--no-export` is passed.

The default run is one cycle. Multiple cycles are allowed only when the operator passes `--cycles`; the process remains attached to the terminal and exits after the bounded count.

## CLI usage

From `apps/forex-scanner`:

```bash
python scripts/run_autonomous_supervisor.py --provider synthetic --symbols EUR/USD GBP/USD --cycles 1
```

Optional examples:

```bash
python scripts/run_autonomous_supervisor.py --watchlist major_forex --cycles 1
python scripts/run_autonomous_supervisor.py --provider synthetic --symbols EUR/USD --cycles 3 --interval-seconds 60
python scripts/run_autonomous_supervisor.py --provider synthetic --symbols EUR/USD --cycles 3 --no-sleep
```

The command exits with code `2` when safety checks block the run.

## Report fields to verify

The JSON and Markdown reports include explicit paper/demo assertions:

- `paper_demo_only: true`
- `live_trading_enabled: false`
- `broker_mode: paper`
- `mt5_called: false`
- `broker_orders_sent: false`
- `hidden_daemon_created: false`
- `subprocess_used: false`

## Recommended validation

```bash
python -m pytest -q tests/test_autonomous_supervisor.py tests/test_demo_bot.py --maxfail=1
python scripts/run_autonomous_supervisor.py --provider synthetic --symbols EUR/USD --cycles 1 --no-export
```
