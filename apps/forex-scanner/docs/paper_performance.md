# Paper Performance Analytics

Paper Performance Analytics is a **read-only paper/demo diagnostic** layer for reviewing a completed local paper session. It reads existing files in `reports/` plus any existing local SQLite `paper_orders` table it can safely inspect. It does **not** run strategies, does **not** connect to MT5, does **not** call `order_send`, does **not** submit broker orders, does **not** mutate `.env`, and does **not** authorize live trading.

Performance metrics are diagnostic evidence only. A good paper result is not proof of profitability and is not permission to move to live execution.

## Command

Run from `apps/forex-scanner`:

```bash
python scripts/paper_performance_report.py --reports-dir reports --export-json --export-txt
```

Options:

| Flag | Meaning |
| --- | --- |
| `--reports-dir` | Directory containing existing paper/demo report artifacts. |
| `--export-json` | Writes `paper_performance_summary.json`. |
| `--export-txt` | Writes `paper_performance_report.txt`. |
| `--strict` | Treats missing, stale, or incomplete source evidence as incomplete data. |

## Inputs

The service reads these local artifacts when present:

- `reports/realtime_paper_positions.json`
- `reports/realtime_command_center_summary.json`
- `reports/realtime_paper_supervisor_summary.json`
- `reports/operator_dashboard_summary.json`
- `reports/realtime_heartbeat.jsonl`

It also inspects existing local SQLite files for a repository-native `paper_orders` table. This is compatibility support for the existing paper order store only; the analytics service does not create a new trading storage format and does not write orders.

## Metrics

When available, the summary computes:

- total paper trades/orders and pending/open/closed/cancelled counts
- win/loss/breakeven counts and win rate
- realized R total, average R, best R, and worst R
- realized paper PnL total and average realized PnL
- max drawdown when an equity curve is present
- partial-exit, stop-moved, breakeven, and trailing-stop event counts
- average time in trade when entry/close timestamps exist
- symbols traded, timeframe summary, and strategy/source summary
- data completeness score, missing/stale input files, warnings, blocking reasons, and propagated safety flags

## Outputs

When exports are enabled, generated outputs are:

- `reports/paper_performance_summary.json`
- `reports/paper_performance_report.txt`

Do not commit generated reports. They are session artifacts for local operator review.

## Statuses

- `PAPER_PERFORMANCE_READY`: metrics were computed from available complete evidence.
- `PAPER_PERFORMANCE_WARN`: metrics exist but warnings, missing files, or stale files require review.
- `PAPER_PERFORMANCE_NO_TRADES`: input artifacts exist but no paper trades/orders were found.
- `PAPER_PERFORMANCE_INCOMPLETE_DATA`: evidence is missing or strict-mode completeness requirements are not met.
- `PAPER_PERFORMANCE_BLOCKED_UNSAFE_FLAGS`: source reports propagated unsafe flags such as live execution or order-send activity.

## Safety guarantees

Paper Performance Analytics is intentionally limited to local file inspection. It has no live-trading path, no broker-live execution path, no MT5 dependency, no order submission, no daemon loop, and no strategy execution. Any unsafe source safety flag blocks the summary so an operator cannot mistake unsafe evidence for a clean paper result.

## Tests

```bash
python -m pytest -q tests/test_paper_performance.py
```
