# Paper Session History Ledger (read-only, paper/demo only)

The history ledger records compact snapshots of completed paper/demo session
reviews over time, so an operator can track local session outcomes across
days. It is **local paper/demo history only**:

- it reads existing review/report artifacts (`paper_session_review_summary.json`,
  `paper_performance_summary.json`, `operator_dashboard_summary.json`, and the
  optional bundle manifest);
- it does **not** run strategies or trading logic;
- it does **not** call MT5 and never imports the terminal API;
- it does **not** call `order_send` or submit broker orders;
- it does **not** mutate `.env` or the process environment;
- it does **not** authorize live trading — history entries are diagnostic
  paper/demo evidence only;
- it writes only under `reports/`, rejects history output paths that resolve
  outside the reports directory, and never modifies source reports;
- it is bounded and one-shot: no daemon, no infinite loop.

## CLI

Run from `apps/forex-scanner`.

Append the latest review into history and export the aggregate reports:

```bash
python scripts/paper_session_history.py --reports-dir reports --append-latest --session-name paper-session-review --export-json --export-txt
```

Rebuild the summary from the existing ledger without appending:

```bash
python scripts/paper_session_history.py --reports-dir reports --export-json --export-txt
```

Strict mode (non-zero exit unless the history status is READY or WARN):

```bash
python scripts/paper_session_history.py --reports-dir reports --append-latest --strict
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--reports-dir` | `reports` | Directory containing report artifacts and the ledger |
| `--append-latest` | off | Append a snapshot of the latest review summary |
| `--session-name` | `paper-session-review` | Session name recorded with the entry (also used to locate the optional bundle manifest) |
| `--export-json` | off | Write `reports/paper_session_history_summary.json` |
| `--export-txt` | off | Write `reports/paper_session_history_report.txt` |
| `--strict` | off | Exit `1` unless status is READY or WARN |

## How `--append-latest` works

1. `reports/paper_session_review_summary.json` is read. If it is missing or
   unreadable, **nothing is appended and the CLI does not crash**: without
   `--strict` the run exits `0` with a safe status
   (`PAPER_SESSION_HISTORY_EMPTY` when the ledger is empty,
   `PAPER_SESSION_HISTORY_INCOMPLETE` when prior entries exist); with
   `--strict` it exits `1`.
2. `reports/paper_performance_summary.json` supplies the trade metrics
   (counts, win rate, realized R/PnL, max drawdown, symbols). If absent, the
   metrics are recorded as `null` with a warning.
3. The entry is appended as one JSON line to
   `reports/paper_session_history.jsonl` (stable, audit-friendly append-only
   format with sorted keys).

## Duplicate handling

The duplicate policy is deterministic and documented: an entry whose
`session_name` **and** `review_generated_at` both match an existing ledger
record is **skipped** — the first recorded snapshot is kept unchanged, the
run reports `append_result=DUPLICATE_SKIPPED`, and a warning explains the
skip. A new review run (new `generated_at`) is never treated as a duplicate.
The ledger file is never rewritten by append operations.

## Outputs

- `reports/paper_session_history.jsonl` — append-only ledger (one entry per session snapshot)
- `reports/paper_session_history_summary.json` — aggregate summary
- `reports/paper_session_history_report.txt` — human-readable report

Before reading or writing any generated history artifact, the ledger resolves
the destination and rejects pre-existing symlinks that would escape the reports
directory. This keeps generated history files under `reports/` and avoids
mutating external paths.

Each ledger entry records: `recorded_at`, `session_name`,
`review_generated_at`, `final_review_status`, `operator_status`,
`performance_status`, `bundle_status`, `total_paper_trades`, `closed_count`,
`win_count`, `loss_count`, `breakeven_count`, `win_rate`, `realized_r_total`,
`average_r`, `realized_pnl_total`, `max_drawdown`, `symbols_traded`,
`blocking_reasons`, `warnings`, `safety_flags`, `source_paths`.

## Interpreting the summary

| Field | Meaning |
| --- | --- |
| `final_history_status` | Overall ledger state (see statuses below) |
| `total_sessions`, `status_counts` | Volume and distribution of recorded review outcomes |
| `latest_session` / `latest_ready_session` / `latest_warn_session` / `latest_incomplete_session` / `latest_blocked_session` | Most recent entry overall and per outcome class |
| `aggregate_closed_trades`, `aggregate_wins/losses/breakevens` | Sums across recorded snapshots |
| `average_win_rate`, `aggregate_realized_r`, `aggregate_realized_pnl` | Computed only from entries where the metric was available, otherwise `null` |
| `distinct_symbols_traded` | Union of symbols across entries |
| `recurring_warnings`, `recurring_blocking_reasons` | Messages seen at least twice, with counts — useful for spotting systemic issues |
| `safety_flags_summary` | The ledger's own paper/demo-only flags plus `unsafe_source_flags_detected` from recorded entries |

Note: entries are point-in-time snapshots of the performance summary. If the
underlying paper order store is cumulative, aggregate sums across overlapping
snapshots can double-count; treat the aggregates as a trend indicator, not an
accounting statement.

## Statuses

| Status | Meaning |
| --- | --- |
| `PAPER_SESSION_HISTORY_READY` | Entries recorded, latest review READY, no warnings |
| `PAPER_SESSION_HISTORY_EMPTY` | No ledger entries yet |
| `PAPER_SESSION_HISTORY_WARN` | Warnings present or latest review is WARN |
| `PAPER_SESSION_HISTORY_INCOMPLETE` | Latest review INCOMPLETE, or `--append-latest` could not find a review while prior entries exist |
| `PAPER_SESSION_HISTORY_BLOCKED` | Unsafe safety flags detected in recorded sessions, or the latest review is BLOCKED |

## Strict mode

`--strict` exits `1` whenever the final status is not
`PAPER_SESSION_HISTORY_READY` or `PAPER_SESSION_HISTORY_WARN` — i.e. on
EMPTY, INCOMPLETE (including a missing review with `--append-latest`), or
BLOCKED. Without `--strict` the CLI exits `0` for safe report statuses and
`2` for invalid input such as an invalid `--session-name` or an unsafe history
output path, so the report can be inspected when the configured paths are safe.

## Testing

```bash
python -m pytest -q tests/test_paper_session_history.py
```

The tests run offline, require no MT5, and assert no `order_send`, no `.env`
mutation, no writes outside the reports directory, and no modification of
source reports.
