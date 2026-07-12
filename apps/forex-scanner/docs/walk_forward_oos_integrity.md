# Walk-forward OOS sample integrity

## Canonical aggregate

Walk-forward folds may overlap when `step_days < out_of_sample_days`. In that configuration, the same realized trade can appear in more than one fold.

Per-fold reports keep those records because each fold remains a useful diagnostic unit. Portfolio-level figures must not count them repeatedly.

The canonical aggregate now applies this rule:

1. process folds by ascending `fold_index`;
2. identify a trade by `(symbol, entry_time)`;
3. keep the first occurrence from the earliest fold;
4. sort the retained trades by realized chronology (`exit_time`, `symbol`, `entry_time`);
5. compute aggregate metrics, OOS equity, JSON/TXT summaries, and the CSV registry from that one sample.

The JSON report exposes:

- `out_of_sample.total_trades`: unique canonical trades;
- `out_of_sample.raw_fold_trade_records`: records before de-duplication;
- `out_of_sample.duplicates_removed`: overlap records excluded from the aggregate;
- `out_of_sample.aggregation`: the identity rule used.

## Interpretation

Per-fold trade counts can sum to more than `out_of_sample.total_trades`. That is expected when windows overlap. Do not sum per-fold P&L or trade counts to reconstruct portfolio performance.

Use these artifacts as the single source of truth for aggregate analysis:

- `reports/walk_forward.json`;
- `reports/walk_forward.txt`;
- `reports/oos_trade_registry.csv`.

They now describe the same canonical trade population.

## Existing historical reports

Reports generated before this change may show a larger aggregated trade count than the accompanying de-duplicated registry. They remain historical evidence of the run that was executed, but their headline aggregate should not be compared directly with a post-fix run.

After merging this change, rerun the pre-registered real-data command before making any new edge verdict. Record the commit SHA, data manifest, parameters, raw fold count, unique trade count, and duplicates removed.

## Safety

This change affects reporting and historical evaluation only. It does not send orders, enable live trading, modify broker settings, or weaken paper/demo safety gates.
