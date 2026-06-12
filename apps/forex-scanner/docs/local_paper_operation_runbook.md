# Local Paper Operation Runbook

This runbook gives a local operator a single, end-to-end workflow for running
the project in **safe paper/demo mode**. It chains the existing safety layers —
local MT5 realtime validation, the Realtime Paper Command Center, realtime data
health, the paper supervisor, the paper position manager, the runtime safety
heartbeat, and the autonomous policy/readiness/evidence/recovery layers — into
one human-readable procedure and explains how to interpret every report.

## ⚠️ Safety statement (read first)

- **No live trading is authorized by this project or this runbook.**
- Local MT5 realtime validation is **read-only**: it never calls `order_send`,
  never submits broker orders, and never mutates `.env`.
- Running paper/demo operation successfully is not a go-live approval.
- Passing reports are **evidence only**, not permission to trade real money.
- Any real-money decision requires a **separate manual review process that is
  not implemented here**.
- Do **not** add secrets (broker/MT5 credentials, tokens) to commits.
- Do **not** enable broker live mode.
- Keep `EXECUTION_MODE=paper`.
- Keep `ALLOW_LIVE_TRADING=false`.
- Keep `BROKER_MODE=paper`.

This runbook is **local-only** for the human operator. Local MT5 steps are
**Windows/MT5-dependent** when run by a person on their workstation. In CI and
cloud the same flows run with **mocks/stubs or the synthetic provider**, which
is diagnostic-only and intentionally blocks realtime paper operation.

## When to use this runbook

Use it when you want to run the full local paper/demo operator workflow and
need a checklist plus a report-interpretation guide. It complements, and does
not replace:

- [`local_mt5_realtime_validation.md`](local_mt5_realtime_validation.md)
- [`realtime_command_center.md`](realtime_command_center.md)
- [`realtime_paper_operation.md`](realtime_paper_operation.md)

All commands below are run from `apps/forex-scanner`.

## Operator workflow

### 1. Confirm safe environment defaults

Confirm these values (see `.env.example` for the canonical safe set):

```env
EXECUTION_MODE=paper
BROKER_MODE=paper
ALLOW_LIVE_TRADING=false
MT5_DEMO_ONLY=true
AUTO_BOT_ENABLED=false
ALLOW_MULTI_ASSET_DEMO_TRADING=false
ENABLE_DEMO_EXECUTION=false
NOTIFICATIONS_ENABLED=false
```

### 2. Confirm `.env` does not enable live trading

Verify `ALLOW_LIVE_TRADING=false`, `EXECUTION_MODE=paper`, and `BROKER_MODE=paper`.
Confirm no live broker is selected and no live-confirmation variable is set. If
any live guard is flipped, **stop** — the safety layers will report
`BLOCKED_BY_SAFETY_DRIFT`, and so must you.

### 3. Run local MT5 realtime validation (read-only)

Local Windows/MT5 operator command:

```bash
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD --timeframes M1 M5 --duration-minutes 5 --interval-seconds 30 --export-json --export-txt --export-csv
```

This is strictly read-only. It validates MT5 import, terminal initialization,
account/terminal info, symbol resolution/selection, latest candles, latest tick,
candle age, spread, ATR, spread/ATR, missing/duplicate bars, and provider
latency over a bounded polling window. It does **not** trade.

### 4. Inspect MT5 validation reports

Open and review:

- `reports/local_mt5_realtime_validation.json`
- `reports/local_mt5_realtime_validation.txt`
- `reports/local_mt5_realtime_samples.csv`

Check `final_status`, `latest_candle_age_seconds`, `spread_atr_ratio`,
`missing_bars`, `duplicate_bars`, and `safety_flags` (see interpretation guide
below). Confirm `safety_flags.order_send_called` is `false`.

### 5. Run the Realtime Paper Command Center

Local MT5 operator command:

```bash
python scripts/realtime_command_center.py --provider mt5 --symbols EUR/USD --timeframe M1 --dry-run --max-cycles 1 --build-evidence-first --plan-recovery-on-block --export-json --export-txt
```

This coordinates safety heartbeat, data health, evidence, readiness, policy,
optional recovery planning, paper supervision, and final reporting — all
paper/demo only.

### 6. Optionally run with `--run-scenarios`

Add `--run-scenarios` to also execute the cloud-safe autonomous scenario suite
(synthetic policy/readiness simulations) alongside the bounded cycle.

### 7. Optionally run with `--manage-positions`

Add `--manage-positions` to advance **local paper orders only** through their
lifecycle after the safety gates pass. Combined optional run:

```bash
python scripts/realtime_command_center.py --provider mt5 --symbols EUR/USD --timeframe M1 --dry-run --max-cycles 1 --build-evidence-first --plan-recovery-on-block --run-scenarios --manage-positions --export-json --export-txt
```

### 8. Inspect command-center reports

- `reports/realtime_command_center_summary.json`
- `reports/realtime_command_center_report.txt`

Review `final_status`, `stop_reason`, evidence/readiness/policy results, and any
recovery plan produced when blocked.

### 9. Inspect realtime supervisor reports

- `reports/realtime_paper_supervisor_summary.json`
- `reports/realtime_paper_supervisor_report.txt`

Confirm `live_execution_allowed=false`, `paper_demo_only=true`, and that
`evidence_status` ran before readiness and policy.

### 10. Inspect position manager reports (if `--manage-positions`)

- `reports/realtime_paper_positions.json`
- `reports/realtime_paper_positions.txt`

Review the `position_lifecycle_summary` and per-cycle `positions_updated`,
`positions_closed`, and `partial_exits_created` counts. These are **local paper
orders only**.

### 11. Inspect heartbeat JSONL safety markers

- `reports/realtime_heartbeat.jsonl`

Each line is one cycle. Confirm `runtime_safety_heartbeat`, `paper_demo_only`,
and `live_execution_allowed=false` on every entry, plus monotonically
increasing `heartbeat_sequence`.

### 12. Decide manually whether paper/demo operation is acceptable

There is **no automatic go decision**. Using the checklist below, the operator
decides — by hand — whether paper/demo operation looks acceptable. A clean set
of reports is evidence, not authorization.

### 13. Stop safely if any blocker appears

If any step reports a `BLOCKED*` status, **stop**. Review the recovery plan if
one was generated, fix the underlying condition (data freshness, spread,
provider, environment, evidence/readiness/policy), and re-run from step 3. Never
work around a blocker by enabling live trading or a live broker.

## CI / demo-safe commands (synthetic provider)

Where real MT5 is unavailable (CI, cloud, or any non-Windows workstation), use
mocks/stubs and the **synthetic** provider. Synthetic data is diagnostic-only
and intentionally **blocks** realtime paper operation with
`BLOCKED_SYNTHETIC_FALLBACK`, so it can never be mistaken for live-quality data.

```bash
# Read-only MT5 validation stays CI-safe: with no real MT5 it writes a
# BLOCKED_MT5_UNAVAILABLE report and exits 0 (exit 2 only under --strict).
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD --timeframes M1 --duration-minutes 0 --interval-seconds 0 --export-json --export-txt --export-csv

# Command center with the synthetic provider (diagnostic path only).
python scripts/realtime_command_center.py --provider synthetic --symbols EUR/USD --timeframe M1 --dry-run --max-cycles 1 --export-json --export-txt
```

## Copy-paste operator checklist

Fill in each field with PASS or FAIL. A single FAIL means **do not proceed**.

```text
LOCAL PAPER OPERATION CHECKLIST
date/operator: __________________________

[ ] live trading disabled (ALLOW_LIVE_TRADING=false) ......... PASS / FAIL
[ ] broker mode paper (BROKER_MODE=paper) ................... PASS / FAIL
[ ] MT5 validation read-only (no writes/orders) ............. PASS / FAIL
[ ] no order_send used by validation (order_send_called=false) PASS / FAIL
[ ] data freshness acceptable (candle age within limit) ..... PASS / FAIL
[ ] spread/ATR acceptable (within max ratio) ................ PASS / FAIL
[ ] readiness status acceptable ............................. PASS / FAIL
[ ] policy decision acceptable .............................. PASS / FAIL
[ ] recovery plan reviewed if blocked ....................... PASS / FAIL / N/A
[ ] scenario runner reviewed if executed .................... PASS / FAIL / N/A
[ ] position lifecycle summary reviewed if executed ......... PASS / FAIL / N/A
[ ] heartbeat safety markers reviewed ....................... PASS / FAIL
[ ] final human decision (paper/demo only, NOT go-live) ..... PASS / FAIL

Notes: ____________________________________________________
```

## Report interpretation guide

### `reports/local_mt5_realtime_validation.json`

Machine-readable MT5 validation result. Key fields: `final_status`,
`mt5_import_ok`, `terminal_initialized`, `account_info_available`,
`latest_candle_age_seconds`, `spread`, `atr`, `spread_atr_ratio`,
`missing_bars`, `duplicate_bars`, `latency_ms`, `provider_latency_ms`,
`sample_count`, `blocking_reasons`, `warnings`, `safety_flags`, `output_paths`.
Confirm `safety_flags.order_send_called=false` and
`safety_flags.live_trading_authorized=false`.

### `reports/local_mt5_realtime_validation.txt`

Human-readable summary of the same fields. Use it for a quick scan of
`final_status` and blocking reasons.

### `reports/local_mt5_realtime_samples.csv`

One row per (symbol, timeframe) sample over the bounded polling window. Use it
to inspect freshness and spread trends across the run rather than a single
snapshot.

### `reports/realtime_command_center_summary.json`

The unified command-center result: `final_status`, `stop_reason`, and the nested
data-health, evidence, readiness, policy, recovery, scenario, supervisor, and
position-lifecycle results, plus `safety_flags`.

### `reports/realtime_command_center_report.txt`

Human-readable command-center summary line and section breakdown.

### `reports/realtime_paper_supervisor_summary.json`

Bounded supervisor result including `evidence_status` and per-cycle safety
markers. Confirm `live_execution_allowed=false` and `paper_demo_only=true`.

### `reports/realtime_paper_supervisor_report.txt`

Human-readable supervisor summary.

### `reports/realtime_heartbeat.jsonl`

Append-only, one JSON object per cycle. Each entry includes `heartbeat_sequence`,
`runtime_safety_heartbeat`, `paper_demo_only`, `live_execution_allowed=false`,
and per-cycle data-health/evidence/readiness/policy markers. Use it to prove the
run never enabled live execution.

## Statuses and required operator actions

### MT5 realtime validation statuses

| Status | Meaning | Operator action |
| --- | --- | --- |
| `MT5_REALTIME_READY` | MT5 import, init, account/terminal info, symbol selection, candles, freshness, quality, and spread/ATR all passed. | Proceed to the command center. Still paper/demo only. |
| `MT5_REALTIME_WARN` | Completed with non-blocking warnings (e.g. missing terminal info or unavailable tick/ATR). | Review the warnings; proceed only if you accept them. |
| `BLOCKED_MT5_UNAVAILABLE` | MetaTrader5 package or terminal path unavailable. | On Windows, fix MT5; in CI this is expected (exit 0 without `--strict`). Do not proceed to live anything. |
| `BLOCKED_STALE_DATA` | Latest candle age exceeded `--max-candle-age-seconds`. | Wait for fresh data / check connection; re-run. Do not operate on stale data. |
| `BLOCKED_SPREAD_TOO_WIDE` | Spread/ATR exceeded `--max-spread-atr-ratio`. | Wait for normal spread (avoid news/illiquid windows); re-run. |
| `BLOCKED_POOR_DATA_QUALITY` | Candles missing, duplicated, or unavailable. | Investigate the feed; re-run once quality is restored. |

### Command-center / supervisor statuses

| Status | Meaning | Operator action |
| --- | --- | --- |
| `COMPLETED` | The bounded cycle completed with no blockers. | Review reports; make the manual paper/demo decision. Not a go-live approval. |
| `WARN` | Completed with non-blocking warnings. | Review warnings; proceed only if acceptable. |
| `BLOCKED` | A generic blocker stopped the cycle. | Read `stop_reason`, fix the cause, re-run. |
| `BLOCKED_BY_POLICY` | The autonomous policy engine denied the action. | Review the policy result and recovery plan; do not bypass policy. |
| `BLOCKED_BY_SAFETY_DRIFT` | A live-trading safety guard was flipped (drift detected). | **Stop immediately.** Restore `EXECUTION_MODE=paper`, `ALLOW_LIVE_TRADING=false`, `BROKER_MODE=paper`; never override. |

Other blockers you may see (`BLOCKED_SYNTHETIC_FALLBACK`, `BLOCKED_STALE_DATA`,
`BLOCKED_DATA_HEALTH`, `BLOCKED_BY_EVIDENCE`, `BLOCKED_BY_READINESS`,
`BLOCKED_BY_PROVIDER_FAILURES`, `BLOCKED_BY_OPERATOR_CONTROL`) follow the same
rule: read `stop_reason`, fix the underlying condition, re-run. Never resolve a
blocker by enabling live trading.

## Final reminder

This runbook validates and documents **paper/demo readiness only**. It does not
enable live trading, does not authorize real-money trading, and keeps MT5
validation read-only. Real-money decisions require a separate manual review
process that is intentionally **not** part of this repository.

## Archive the paper/demo session bundle

After completing the local paper/demo report review, export an auditable bundle
for manual review or archival:

```bash
python scripts/export_paper_session_bundle.py --reports-dir reports --output-dir reports/bundles --session-name paper-session-smoke
```

This step is read-only with respect to trading and report sources. It packages
existing JSON/TXT/CSV/JSONL reports, writes a ZIP plus JSON/TXT manifests,
computes SHA-256 checksums, records missing required and optional artifacts, and
propagates the operator dashboard final status when available. It does not call
MT5, does not call `order_send`, does not submit broker orders, does not run a
daemon, and does not mutate `.env`.

The bundle is evidence for human audit only. Even if all included reports pass,
that does **not** authorize live trading or broker-live execution.

## Post-session paper performance review

For a completed paper/demo session, generate performance analytics from existing reports only:

```bash
python scripts/paper_performance_report.py --reports-dir reports --export-json --export-txt
```

Review `reports/paper_performance_summary.json` and `reports/paper_performance_report.txt` for trade counts, win/loss/breakeven counts, realized R, realized PnL when available, partial exits, stop movement events, data completeness, warnings, blocking reasons, and propagated safety flags. This step is read-only: it does not run strategies, does not call MT5, does not call `order_send`, does not submit broker orders, and does not authorize live trading. Treat all metrics as diagnostic evidence only.
