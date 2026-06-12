# Local MT5 Realtime Validation Runbook

This runbook describes a **local-only, Windows/MetaTrader 5 dependent** operator check for realtime market-data readiness. It validates whether the workstation can read current MT5 market data with acceptable freshness and quality before an operator considers paper/demo realtime workflows.

It is strictly **read-only**:

- It validates market-data readiness only.
- It does **not** authorize live trading.
- It does **not** submit broker orders.
- It does **not** call `order_send`.
- It does **not** mutate `.env` files or enable live-trading flags.
- It does **not** run as a daemon and has no infinite loop.
- It is bounded by `--duration-minutes` and `--interval-seconds`.

CI and cloud environments use mocks/stubs only. A real local MT5 terminal is **not** required in CI, and the CLI exits successfully with a blocked report when MT5 is unavailable unless `--strict` is explicitly requested.

## When to use it

Use this runbook on a local Windows workstation when you need to confirm that MT5 market data is available and fresh enough for paper/demo realtime checks. It complements these existing paper/demo safety layers:

- Realtime Data Health
- Realtime Paper Supervisor
- Realtime Paper Position Manager
- Realtime Paper Command Center
- Runtime Safety Heartbeat

This runbook does not replace those layers and does not permit broker-live execution.

## Command

Run from `apps/forex-scanner`:

```bash
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD GBP/USD --timeframes M1 M5 --duration-minutes 15 --interval-seconds 30 --export-json --export-txt
```

CI-safe smoke command:

```bash
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD --timeframes M1 --duration-minutes 0 --interval-seconds 0 --export-json --export-txt --export-csv
```

Strict local workstation validation:

```bash
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD GBP/USD --timeframes M1 M5 --duration-minutes 15 --interval-seconds 30 --export-json --export-txt --export-csv --strict
```

`--strict` returns a non-zero exit code when MT5 is unavailable or the validation status is blocked. Without `--strict`, the command still writes blocked reports but remains CI-safe.

## Options

- `--symbols`: one or more logical symbols, for example `EUR/USD GBP/USD`.
- `--watchlist`: configured watchlist name to add to the validation set.
- `--timeframes`: one or more MT5 timeframe names (`M1`, `M5`, `M15`, `H1`, `H4`, `D1`).
- `--duration-minutes`: bounded polling duration. Use `0` for one immediate sample.
- `--interval-seconds`: delay between samples. Use `0` for no sleep.
- `--max-candle-age-seconds`: blocks stale candles older than this threshold.
- `--max-spread-atr-ratio`: blocks when spread divided by ATR exceeds this threshold.
- `--reports-dir`: output directory, defaulting to `reports`.
- `--export-json`: writes the JSON report.
- `--export-txt`: writes the text summary.
- `--export-csv`: writes per-sample CSV rows.
- `--strict`: makes blocked local validation return a non-zero process exit code.

## Read-only checks performed

The CLI performs only local market-data reads:

1. Imports the `MetaTrader5` Python package.
2. Initializes the local terminal connection.
3. Reads account information.
4. Reads terminal information.
5. Resolves logical symbols to MT5 symbols.
6. Selects symbols in Market Watch.
7. Reads latest M1/M5 or requested timeframe candles and normalizes returned bars by timestamp before freshness/gap checks.
8. Reads the latest tick when available.
9. Computes latest candle age.
10. Computes spread from bid/ask.
11. Computes ATR from recent candles when possible.
12. Computes spread/ATR when possible.
13. Detects missing bars.
14. Detects duplicate bars.
15. Measures MT5 request latency in `latency_ms`.
16. Measures provider-data latency from the freshest tick/candle timestamp in `provider_latency_ms`.
17. Repeats polling for the bounded duration only.

## Expected reports

When exports are enabled, the command writes:

- `reports/local_mt5_realtime_validation.json`
- `reports/local_mt5_realtime_validation.txt`
- `reports/local_mt5_realtime_samples.csv`

Reports include:

- `started_at`
- `completed_at`
- `duration_minutes`
- `interval_seconds`
- `symbols`
- `timeframes`
- `mt5_import_ok`
- `terminal_initialized`
- `account_info_available`
- `terminal_info_available`
- `symbol_selected`
- `resolved_symbols`
- `latest_candle_time`
- `latest_candle_age_seconds`
- `latest_tick_time`
- `spread`
- `atr`
- `spread_atr_ratio`
- `missing_bars`
- `duplicate_bars`
- `latency_ms`
- `provider_latency_ms`
- `sample_count`
- `final_status`
- `blocking_reasons`
- `warnings`
- `safety_flags`
- `output_paths`

## Statuses

- `MT5_REALTIME_READY`: MT5 import, terminal initialization, account info, symbol selection, candles, data freshness, data quality, and spread/ATR checks passed.
- `MT5_REALTIME_WARN`: validation completed but non-blocking warnings exist, such as missing terminal info or unavailable tick/ATR-derived warning context.
- `BLOCKED_MT5_UNAVAILABLE`: the `MetaTrader5` package or local terminal path is unavailable.
- `BLOCKED_TERMINAL_INIT_FAILED`: terminal initialization failed.
- `BLOCKED_ACCOUNT_INFO_UNAVAILABLE`: account info could not be read.
- `BLOCKED_SYMBOL_UNAVAILABLE`: symbol resolution or selection failed.
- `BLOCKED_STALE_DATA`: latest candle age exceeded `--max-candle-age-seconds`.
- `BLOCKED_SPREAD_TOO_WIDE`: spread/ATR exceeded `--max-spread-atr-ratio`.
- `BLOCKED_POOR_DATA_QUALITY`: candles were missing, duplicated, or unavailable.

## Safety interpretation

A ready status means only that local MT5 market data looked usable during the bounded validation window. It does not mean live trading is enabled, approved, or safe. Operators must continue to use paper/demo-only command-center, supervisor, readiness, policy, and heartbeat layers for realtime paper workflows.

## CI expectations

Tests for this runbook use mocks/stubs and do not require a real MT5 installation. CI should run:

```bash
python -m pytest -q tests/test_local_mt5_realtime_validation.py
```

The CLI can also be smoke-tested in CI with a zero-duration run. When real MT5 is absent, it should write `BLOCKED_MT5_UNAVAILABLE` reports and return zero unless `--strict` is used.
