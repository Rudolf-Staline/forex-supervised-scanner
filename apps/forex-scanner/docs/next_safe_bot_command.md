# Next safe bot command

`scripts/next_safe_bot_command.py` prints **exactly one** safe, bounded command
to run next, plus a short reason. It is read-only and **never recommends live
trading**. It bases its single recommendation on the files in `reports/`.

## Usage

```bash
python scripts/next_safe_bot_command.py --reports-dir reports
```

Flags: `--reports-dir`, `--max-age-hours`, `--export-json`, `--strict`.

`scripts/explain_last_block.py` and `scripts/explain_last_decision.py` are
companion read-only CLIs:

```bash
python scripts/explain_last_block.py --reports-dir reports --export-json --export-txt
python scripts/explain_last_decision.py --reports-dir reports --export-json --export-txt
```

`explain_last_decision.py` reads `decision_trace.json` when present and falls
back to `score_decomposition.json`, then `signal_journal.jsonl`, then
`autonomous_supervisor_summary.json`. It never crashes when
`decision_trace.json` is missing.

## Recommendation rules (in priority order)

1. **Unsafe safety flag detected** → `STOP_AND_REVIEW` (read-only diagnostics only).
2. **MT5 data stale** → run a synthetic paper diagnostic, or wait for market open. The staleness gate is never bypassed.
3. **MT5 spread/ATR too wide** → synthetic paper diagnostic, or wait for spreads to normalize. The spread gate is never relaxed.
4. **Data health blocked** → synthetic paper diagnostic; realtime non-dry-run is not recommended.
5. **Evidence missing/stale/blocked** → `autonomous_evidence_builder.py --mode read-only --include-readiness`.
6. **Readiness blocked** → `autonomous_readiness_report.py --build-evidence-first --evidence-mode read-only`.
7. **MT5 validation missing** → `local_mt5_realtime_validation.py ... --duration-minutes 0` (bounded read-only).
8. **Readiness READY + data health HEALTHY** → bounded realtime paper **dry-run** supervisor.
9. **Readiness READY + data health HEALTHY + safety clean + a prior dry-run supervisor completed** → bounded realtime paper run (still paper/demo only, never live).
10. Otherwise → safe synthetic paper diagnostic (`run_one_cycle.py --provider synthetic --broker paper`).

A realtime **non-dry-run** paper command is only ever recommended when readiness
and data health are not blocked and the safety flags are clean. Synthetic
diagnostics are convenient and fully safe, but they are **not** live-quality
realtime MT5 data — see
[`realtime_mt5_decision_blocks.md`](realtime_mt5_decision_blocks.md).

## Example output

```
SAFETY: read-only operator diagnostics; paper/demo only; ...
next_safe_command: python scripts/autonomous_evidence_builder.py --mode read-only --include-readiness --export-json --export-txt
reason: No fresh readiness evidence is available; build it read-only first.
```

**Passing diagnostics never authorizes live trading.** Every recommended
command is paper/demo only and bounded (no daemon, no unbounded loop).
