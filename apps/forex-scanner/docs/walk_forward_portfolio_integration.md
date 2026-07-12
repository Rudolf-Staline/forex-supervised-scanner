# Walk-forward portfolio integration

The walk-forward harness and the portfolio allocator serve different purposes and
are intentionally kept as separate layers.

1. Each fold tunes its score threshold using only the in-sample segment.
2. The selected threshold filters that fold's out-of-sample trades.
3. Overlapping OOS windows are de-duplicated by `(symbol, entry_time)`.
4. The resulting canonical candidate stream is replayed chronologically through
   portfolio constraints.
5. Headline expectancy, drawdown, profit factor, and equity are calculated only
   from accepted trades.

This ordering matters. Applying portfolio constraints inside each fold would make
cross-fold allocation inconsistent and could count the same overlapping trade
more than once. Applying the allocator only after canonical de-duplication gives
one deterministic portfolio path.

## Default research command

```bash
python scripts/walk_forward_report.py \
  --provider csv \
  --symbols AUD/USD EUR/USD GBP/USD USD/CAD USD/CHF USD/JPY \
  --style day_trading \
  --from-date 2019-02-15 \
  --to-date 2023-12-21 \
  --in-sample-days 45 \
  --out-of-sample-days 15 \
  --step-days 15 \
  --min-in-sample-trades 5 \
  --max-concurrent-positions 3 \
  --max-same-symbol-positions 1 \
  --max-abs-currency-exposure 2 \
  --daily-loss-limit-r 3
```

Portfolio constraints are enabled by default in this CLI. Use `--no-portfolio`
only to reproduce the candidate-level aggregate or inspect signal-generation
behaviour without capital-allocation constraints.

## Output artifacts

When portfolio mode is enabled:

- `portfolio_walk_forward.json`: full machine-readable result;
- `portfolio_walk_forward.txt`: concise human-readable summary;
- `oos_trade_registry.csv`: all canonical OOS candidates, preserving the existing
  score-calibration and signal-diagnostics contract;
- `portfolio_trade_registry.csv`: accepted trades only; this matches portfolio
  headline metrics;
- `portfolio_rejections.csv`: rejected candidates with reason and state snapshot.

The two trade registries are intentionally distinct. Signal calibration and
candidate-level score analysis should use `oos_trade_registry.csv`. Attainable
portfolio edge, drawdown, and allocation analysis should use
`portfolio_trade_registry.csv`. This avoids conditioning the score calibration on
portfolio constraints while keeping the portfolio headline sample fully auditable.

## Current assumptions

- each accepted trade risks one equal R unit;
- exposure limits are signed currency units, not broker notionals;
- positions exiting exactly at a new entry timestamp are closed first;
- the daily-loss lock uses only P&L realized before the new entry decision;
- per-fold metrics remain candidate-level diagnostics;
- no margin, swaps, covariance sizing, partial exits, or live execution are added.
