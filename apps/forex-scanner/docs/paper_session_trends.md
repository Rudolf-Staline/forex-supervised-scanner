# Paper Session Trends

Paper Session Trends is an offline, report-based analyzer for completed
paper/demo session history. It reads the existing Paper Session History Ledger
and produces multi-session insight reports for operator review.

It is **not trading logic**:

- it analyzes paper/demo history only;
- it reads `reports/paper_session_history.jsonl`;
- it does not run strategies or session review/history commands;
- it does not call MT5 and does not import MT5 APIs;
- it does not call `order_send` and does not submit broker orders;
- it does not authorize live trading;
- it does not mutate `.env` or any source report;
- it is a bounded one-shot command, not a daemon or infinite loop.

## CLI

Run from `apps/forex-scanner`.

Analyze the latest ten ledger entries and export both trend reports:

```bash
python scripts/paper_session_trends.py --reports-dir reports --window 10 --export-json --export-txt
```

Analyze the latest five entries in strict mode:

```bash
python scripts/paper_session_trends.py --reports-dir reports --window 5 --strict
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--reports-dir` | `reports` | Directory containing `paper_session_history.jsonl` |
| `--window` | `10` | Number of most recent valid history entries to analyze |
| `--export-json` | off | Write `reports/paper_session_trends_summary.json` |
| `--export-txt` | off | Write `reports/paper_session_trends_report.txt` |
| `--strict` | off | Exit non-zero for EMPTY or BLOCKED trend results |

## Inputs and outputs

Input:

- `reports/paper_session_history.jsonl`

Outputs, when the export flags are enabled:

- `reports/paper_session_trends_summary.json`
- `reports/paper_session_trends_report.txt`

Trend output paths are fixed filenames under the selected reports directory.
The analyzer does not rewrite or append to `paper_session_history.jsonl`, does
not mutate `paper_session_history_summary.json`, and does not mutate any source
review, dashboard, performance, or bundle artifact.

If the history file is missing or empty, non-strict mode exits `0` with
`PAPER_SESSION_TRENDS_EMPTY` so cloud smoke runs can safely inspect the report
state. Invalid or corrupt JSONL lines are skipped and counted as warnings; valid
history lines are still analyzed.

## How `--window` works

The analyzer loads valid JSON object lines from `paper_session_history.jsonl`,
sorts them by `recorded_at` and `review_generated_at`, then analyzes only the
latest `--window` entries. `total_sessions_available` reports all valid loaded
entries, while `total_sessions_analyzed` reports the number used in the current
window. `analysis_window_size` echoes the requested window.

## Trend labels

Final review statuses are mapped to simple severity scores for the status trend:

| Review status bucket | Score |
| --- | --- |
| BLOCKED | 0 |
| INCOMPLETE | 0 |
| WARN | 1 |
| READY | 2 |
| Unknown | 1 |

The status trend is then labeled from the ordered score sequence:

- `improving` — every meaningful movement goes upward and at least one movement improves;
- `degrading` — every meaningful movement goes downward and at least one movement degrades;
- `stable` — all compared values are unchanged (or there is only one status);
- `mixed` — the window contains both improving and degrading movements.

Win-rate and realized-R trends use the same direction logic on numeric values,
but return `insufficient_data` when fewer than two computable values exist.

## Warning and blocking reason comparisons

For the latest session, the analyzer compares message sets against the union of
previous sessions in the selected window:

- `new_warnings_latest` — warnings present in the latest session but absent from previous sessions;
- `new_blocking_reasons_latest` — blocking reasons present in the latest session but absent from previous sessions;
- `resolved_warnings` — previous warnings absent from the latest session;
- `resolved_blocking_reasons` — previous blocking reasons absent from the latest session.

`recurring_warnings` and `recurring_blocking_reasons` list messages that appear
at least twice in the analysis window, with counts.

## Metrics in the JSON/TXT reports

The summary includes:

- total sessions available and analyzed;
- latest session and latest final review status;
- counts and ratios for READY/WARN/INCOMPLETE/BLOCKED status buckets;
- status trend direction;
- recurring, new, and resolved warnings/blocking reasons;
- total closed trades, wins, losses, and breakevens;
- average win rate across sessions where win rate is present;
- win-rate and realized-R trend labels;
- aggregate realized R and realized PnL;
- worst available max drawdown value;
- distinct symbols traded;
- symbol concentration by occurrence;
- safety flag summary and unsafe flag detections;
- recommended next actions.

These values are history insights only. If source performance summaries are
cumulative snapshots, aggregate trade/PnL/R values across snapshots can
double-count and should be interpreted as trend evidence rather than account
statements.

## Statuses

| Status | Meaning |
| --- | --- |
| `PAPER_SESSION_TRENDS_READY` | Valid history was analyzed, latest status is not warning/blocked, and no analyzer warnings were produced |
| `PAPER_SESSION_TRENDS_EMPTY` | History is missing or empty, or no valid session records were available |
| `PAPER_SESSION_TRENDS_WARN` | Corrupt lines or other analyzer warnings were found, or the latest session is WARN |
| `PAPER_SESSION_TRENDS_BLOCKED` | Unsafe safety flags were detected in recorded sessions, or the latest session is BLOCKED |

Unsafe source flags include live-trading, broker execution/submission,
`order_send`, and `.env` mutation indicators. Any detected unsafe flag blocks
the trend result for operator investigation; it still does not perform live
trading or broker execution.

## Strict mode

Without `--strict`, missing or empty history returns a safe EMPTY report and
exits `0`. This lets CI/cloud smoke commands run before local operators have
recorded paper history.

With `--strict`, EMPTY and BLOCKED statuses exit `1`. Invalid CLI input, such as
`--window 0`, exits `2`. WARN remains inspectable and exits `0` because corrupt
lines are already isolated from valid trend analysis and reported in the output.

## Testing

```bash
python -m pytest -q tests/test_paper_session_trends.py
```

The tests run offline, require no MT5, and assert no `order_send`, no `.env`
mutation, no daemon/infinite loop pattern, no source history mutation, and JSON
and TXT export behavior.
