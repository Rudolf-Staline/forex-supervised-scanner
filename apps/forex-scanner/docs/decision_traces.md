# Decision traces

A `DecisionTrace` is a structured, sanitized explanation of one
scanner/demo-bot decision. It answers, for every paper/demo decision:

1. What setup was detected? (`setup_family`, `setup_subtype`, `direction`)
2. How was the score calculated? (layer scores + `score_components` + `weights`)
3. Which min-score threshold was used? (`min_score_policy`, `active_min_score`)
4. Which gates passed or failed? (`gate_results`)
5. Why was the opportunity accepted/watchlisted/detected/rejected?
   (`status`, `accepted`, `rejection_reasons`, `primary_rejection_reason`)
6. Why did the bot create or refuse a paper order?
   (`order_ids`, demo-bot `gate_results`)

The model lives in
[`app/reporting/decision_trace.py`](../app/reporting/decision_trace.py).

## Safety

Traces are **paper/demo only**. Every trace carries `safety_flags`
(`live_trading=false`, `broker_live_execution=false`, `order_send_called=false`,
`env_mutated=false`), and all exported content is run through a sanitizer that
redacts any key containing credential-like substrings (`secret`, `password`,
`token`, `login`, `account`, `server`, `key`, `.env`). Traces never contain MT5
credentials or raw `.env` values. Generated reports are git-ignored and must not
be committed.

## Fields

The trace includes (non-exhaustive): `trace_id`, `cycle_id`, `timestamp`,
`symbol`, `style`, `provider`, `broker_mode`, `setup_family`, `setup_subtype`,
`direction`, `status`, `accepted`, `order_ids`, the layer scores
(`technical_score`, `execution_score`, `context_score`, `empirical_score`,
`pattern_score`, `final_score`), `score_components`, the risk plan (`entry`,
`stop_loss`, `take_profit`, `tp1/2/3`, `risk_reward`), microstructure (`spread`,
`atr`, `spread_atr_ratio`, `data_quality_score`), context (`session`,
`market_regime`, `htf_regime`, `entry_regime`, `trigger_regime`),
`min_score_policy`, `gate_results`, `rejection_reasons`,
`primary_rejection_reason`, and `safety_flags`.

## GateResult

Every gate is represented by a reusable `GateResult`:

| Field      | Meaning |
| ---------- | ------- |
| `name`     | Gate name (e.g. `final score`, `risk/reward`). |
| `layer`    | `score`, `execution`, `context`, `empirical`, `data`, `risk`, `market`, or `bot`. |
| `value`    | Observed value. |
| `minimum`  | Required minimum (for min gates). |
| `maximum`  | Allowed maximum (for max gates, e.g. spread/ATR). |
| `margin`   | `value - minimum` for min gates, `maximum - value` for max gates. |
| `passed`   | Whether the gate passed. |
| `severity` | `info`, `warning`, or `blocker`. |
| `reason`   | Human-readable explanation. |

Gates represented include: final score vs active min, demo bot min score,
risk/reward, execution/context/empirical scores, data quality, activation and
invalidation quality, spread/ATR, status allowed, executable levels present, and
direction executable.

## Inspecting the latest decision

A full paper cycle generates one trace per scanned opportunity:

```bash
python scripts/run_one_cycle.py --provider synthetic --broker paper --symbols EUR/USD
```

This exports `reports/decision_trace.json`, `reports/decision_trace.txt`,
`reports/min_score_policy_report.json`, and
`reports/min_score_policy_report.txt`.

To explain the score decomposition for a one-shot scan without running the bot:

```bash
python scripts/score_decomposition.py --provider synthetic --symbols EUR/USD --style day_trading --export-json --export-txt
```

The human-readable `decision_trace.txt` highlights the strongest positive
factors, the weakest gates, exact threshold comparisons, the final decision, and
the **next diagnostic action** when a decision is blocked.
