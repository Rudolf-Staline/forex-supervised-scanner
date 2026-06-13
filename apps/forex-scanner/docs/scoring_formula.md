# Scoring formula

This document explains how the scanner assembles the 0–100 `final_score` for a
candidate setup. It is descriptive only: nothing here changes the formula,
weights, or thresholds. The implementation lives in
[`app/scoring/engine.py`](../app/scoring/engine.py).

## Layers

The final score is a weighted blend of four layer scores plus a small,
capped pattern bonus:

```
base_final  = weighted_average(
                 technical_score,
                 execution_score,
                 context_score,
                 empirical_score,
                 layer_weights)
final_score = clip(base_final + min(15.0, pattern_score) * 0.2)   # 0..100
```

`pattern_score` is bounded to `[0, 15]`, so the pattern bonus can add at most
`3.0` points. `clip` keeps the result in `[0, 100]`.

### Layer weights

Default `layer_weights` (from `default_settings.json`):

| Layer      | Weight |
| ---------- | ------ |
| technical  | 0.30   |
| execution  | 0.30   |
| context    | 0.24   |
| empirical  | 0.16   |

## Technical score

A weighted average of technical components using the component `weights`:

`trend_clarity`, `structure_quality`, `multi_timeframe_alignment`,
`volatility_suitability`, `momentum_confirmation`, `level_proximity`.

Default component `weights`:

| Component                  | Weight |
| -------------------------- | ------ |
| trend_clarity              | 18.0   |
| structure_quality          | 14.0   |
| multi_timeframe_alignment  | 18.0   |
| volatility_suitability     | 12.0   |
| momentum_confirmation      | 14.0   |
| spread_friction            | 8.0    |
| risk_reward                | 10.0   |
| level_proximity            | 6.0    |

## Execution score

Average of execution-quality components: `spread_friction`, `spread_to_stop`,
`risk_reward`, `target_clearance`, `activation_quality`, `invalidation_quality`,
`data_quality_execution`. When no valid risk plan exists, the risk-related
components score as failed so rejected candidates remain diagnostically useful
without becoming tradable.

## Context score

Average of `session_quality`, `data_quality`, `spread_to_atr`, and
`volatility_exploitability`, with penalties for dead sessions, stale/low-quality
data, and poor spread-to-ATR ratios.

## Empirical score

Historical calibration support. Defaults to the configured `neutral_score`
(55.0) when there is insufficient sample history.

## Why a high technical score can still be rejected

`final_score` is only one of several independent gates. A setup with a strong
technical score can still be rejected because:

- the **final score** is below the active minimum (see
  [`min_score_policy.md`](min_score_policy.md));
- an **approval gate** fails (execution, context, empirical, data quality,
  activation, or invalidation quality below its minimum);
- **spread/ATR** friction exceeds the instrument maximum;
- the **status** is not `approved`/`premium`;
- **executable levels** (entry, stop, take-profit, staged targets) are missing
  or incoherent;
- the **direction** is not executable, or the **regime/session** is blocked;
- a **demo bot** safety gate (daily limits, cooldown, max open trades,
  operator maintenance/degraded mode) blocks the paper order.

Each of these is represented as a structured `GateResult`
(`name`, `layer`, `value`, `minimum`, `maximum`, `margin`, `passed`,
`severity`, `reason`) in the decision trace, so it is always explicit which
gate blocked an otherwise high-scoring setup.

## Inspecting a score decomposition

```bash
python scripts/score_decomposition.py --provider synthetic --symbols EUR/USD --style day_trading --export-json --export-txt
```

Exports `reports/score_decomposition.json` and `reports/score_decomposition.txt`
(both git-ignored). See [`decision_traces.md`](decision_traces.md) for the full
decision trace.
