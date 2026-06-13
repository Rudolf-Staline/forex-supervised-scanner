# Operator decision doctor

`scripts/decision_doctor.py` is a **read-only** operator diagnostic. It reads the
files in `reports/`, normalizes them, and answers — in one command — whether the
paper/demo bot can run, what is blocking it, and what to run next.

It never runs trading logic, never imports the MT5 terminal API, never calls
`order_send`, and never mutates `.env`. It works fully offline and tolerates a
missing reports directory, missing files, empty files, and malformed JSON/JSONL.

## Usage

```bash
python scripts/decision_doctor.py --reports-dir reports --export-json --export-txt
```

Flags: `--reports-dir` (default `reports`), `--max-age-hours` (default 24),
`--export-json`, `--export-txt`, `--strict` (exit non-zero when a hard blocker
exists). Exit code is `0` for a normal diagnostic conclusion.

Exports `reports/decision_doctor_summary.json` and
`reports/decision_doctor_report.txt` (both git-ignored).

## What it answers

- Is the bot ready to run a paper/demo diagnostic?
- What is the primary blocker (and its category)?
- Which reports are missing or stale?
- Is a decision trace available?
- Is MT5 reachable / are candles stale / is spread/ATR too wide?
- Is the readiness gate blocked?
- Did the last supervisor run stop before completing cycles?
- Did a score / min-score gate block an opportunity?
- What is the next safe bounded command?

## Example output (empty reports directory)

```
SAFETY: read-only operator diagnostics; paper/demo only; no live trading; ...
OPERATOR DECISION DOCTOR (read-only, paper/demo only)
overall_status=REPORTS_MISSING
confidence=low

Q: Is the bot ready to run a paper/demo diagnostic? -> no
Q: What is the primary blocker? -> none (category=none)
Q: Missing reports? -> autonomous_readiness_report.json, ...
Q: Next safe command -> python scripts/autonomous_evidence_builder.py --mode read-only --include-readiness --export-json --export-txt
```

## Normalized diagnostic fields

`overall_status`, `primary_blocker`, `blocker_category`, `blockers`,
`warnings`, `missing_reports`, `stale_reports`, `available_reports`,
`safety_summary`, `next_safe_command`, `next_safe_command_reason`,
`confidence`.

Each entry in `blockers` carries `code`, `category`, `severity`,
`source_report`, `raw_blocker`, `human_explanation`, and `safe_next_action`.

## Safety

The `safety_summary` always reports `paper_demo_only=true` and the unsafe flags
(`live_execution_allowed`, `broker_order_submission_allowed`,
`mt5_order_execution_allowed`, `order_send_called`, `env_mutation_performed`,
`hidden_daemon_created`, `infinite_loop_default`) as `false`. If any report
contains an unsafe flag set to `true`, `overall_status` becomes
`STOP_AND_REVIEW` and the only recommendation is to stop and run read-only
diagnostics. **Passing diagnostics never authorizes live trading.**

See also: [`next_safe_bot_command.md`](next_safe_bot_command.md) and
[`realtime_mt5_decision_blocks.md`](realtime_mt5_decision_blocks.md).
