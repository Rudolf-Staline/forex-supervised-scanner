# Realtime MT5 decision blocks

This note explains the realtime MT5 data blocks that the operator diagnostics
(`decision_doctor.py`, `next_safe_bot_command.py`, `explain_last_block.py`)
surface, and why they must never be bypassed.

## `BLOCKED_STALE_DATA` — stale candles

The most recent candle is older than the allowed age. This usually means the
market is closed (weekend / off-hours), the MT5 terminal lost its feed, or the
symbol is not actively quoted.

- **Why it blocks:** a setup computed on stale candles does not reflect current
  price; entries, stops, and spread are unreliable.
- **Safe next action:** wait for the market to open, or run a synthetic paper
  diagnostic. Do **not** lower the staleness threshold.

```bash
python scripts/run_one_cycle.py --provider synthetic --broker paper --symbols EUR/USD
```

## `BLOCKED_SPREAD_TOO_WIDE` — spread/ATR too high

The spread relative to ATR is above the configured maximum (default 0.25 in the
realtime supervisor; per-instrument `max_spread_atr` in the scanner).

- **Why it blocks:** wide spreads relative to volatility make the planned
  risk/reward unachievable and inflate execution friction.
- **Safe next action:** wait for the main session when spreads normalize, or run
  a synthetic diagnostic. Do **not** relax the spread gate.

## `BLOCKED_BY_READINESS` / `BLOCKED_BY_SESSION_HEALTH`

The readiness gate or session-health check is blocking. Rebuild evidence
read-only and re-run readiness; or wait for a healthy session window.

```bash
python scripts/autonomous_readiness_report.py --build-evidence-first --evidence-mode read-only --export-json --export-txt
```

## Synthetic diagnostics are not live-quality data

`--provider synthetic` produces deterministic, bounded data that is ideal for
exercising the decision pipeline and generating decision traces **offline and in
CI**. It is **not** a substitute for live-quality realtime MT5 data: synthetic
runs never prove that a live feed is fresh, that spreads are acceptable, or that
a broker symbol is mapped. Use `local_mt5_realtime_validation.py` on a local
Windows machine with an MT5 demo terminal to confirm live-quality data.

## Diagnostics never authorize live trading

All of these CLIs are read-only and paper/demo only. A clean diagnostic, a
passing readiness gate, or a successful dry-run supervisor **never** authorizes
live trading or broker-live execution. There is no command in this toolset that
enables live trading; the strongest recommendation is a bounded, paper/demo
realtime run.
