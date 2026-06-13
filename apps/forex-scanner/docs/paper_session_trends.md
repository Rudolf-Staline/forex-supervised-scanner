# Paper Session Trends

Paper Session Trends analyzes the local paper/demo session history ledger and produces multi-session trend reports.

It reads:

```text
reports/paper_session_history.jsonl
```

It writes, when exports are enabled:

```text
reports/paper_session_trends_summary.json
reports/paper_session_trends_report.txt
```

The analyzer is local, offline, and paper/demo only. It does not run strategies, connect to MT5, submit orders, mutate `.env`, start a daemon, or modify `paper_session_history.jsonl`.

## CLI

Run from `apps/forex-scanner`:

```bash
python scripts/paper_session_trends.py --reports-dir reports --window 10 --export-json --export-txt
```

Strict mode exits non-zero when the trend status is not ready or warning-level:

```bash
python scripts/paper_session_trends.py --reports-dir reports --window 5 --strict
```

## Window behavior

`--window` controls how many of the most recent history entries are analyzed. The default is `10`.

The full ledger is not rewritten. The analyzer only reads the JSONL file, selects the most recent entries, and writes a separate trend summary.

## Trend outputs

The summary includes:

- total available sessions;
- total sessions analyzed;
- latest session;
- latest review status;
- status counts;
- status trend;
- ready/warn/incomplete/blocked ratios;
- recurring warnings;
- recurring blocking reasons;
- new warnings in the latest session;
- new blocking reasons in the latest session;
- resolved warnings compared with previous sessions;
- resolved blocking reasons compared with previous sessions;
- aggregate closed trades;
- aggregate wins/losses/breakevens;
- average win rate;
- win-rate trend;
- realized-R trend;
- aggregate realized R;
- aggregate realized PnL;
- worst max drawdown when available;
- symbols traded;
- symbol concentration;
- unsafe flag detections;
- recommended next actions.

## Trend labels

Status trend uses a simple ordered posture model:

```text
BLOCKED < INCOMPLETE/EMPTY < WARN < READY
```

The label is:

- `improving` when the sequence only improves and at least one step improves;
- `degrading` when the sequence only degrades and at least one step degrades;
- `stable` when all comparable values are unchanged;
- `mixed` when the sequence both improves and degrades;
- `insufficient_data` when fewer than two comparable values exist.

Win-rate and realized-R trends use the same labels over numeric series.

## Statuses

The final trend status is one of:

- `PAPER_SESSION_TRENDS_READY`
- `PAPER_SESSION_TRENDS_EMPTY`
- `PAPER_SESSION_TRENDS_WARN`
- `PAPER_SESSION_TRENDS_BLOCKED`

Missing or empty history returns `PAPER_SESSION_TRENDS_EMPTY` without crashing. In strict mode, an empty trend status exits non-zero.

Corrupt JSONL lines are skipped and reported as warnings; they do not crash the analysis.

Unsafe safety flags in the analyzed window force `PAPER_SESSION_TRENDS_BLOCKED`.

## Safety notes

Trend reports are diagnostic paper/demo evidence only. They do not authorize live trading or broker execution.

Generated trend artifacts are reports and should not be committed.
