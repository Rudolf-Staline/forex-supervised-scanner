# Portfolio backtester

The portfolio backtester replays completed historical `TradeRecord` candidates in
chronological order and decides which candidates could coexist under shared risk
constraints.

It is a research and paper-trading component only. It does not generate signals,
size broker orders, or enable live trading.

## Why it exists

The symbol backtester prevents overlapping positions on the same symbol, but a
multi-pair report can still concatenate trades that would have competed for the
same portfolio risk budget. This can overstate attainable trade count and ignore
concentrated currency bets such as several simultaneous positions against USD.

`app.backtest.portfolio.simulate_portfolio` adds a deterministic allocation layer
for those already-generated candidates.

## Constraints

- maximum concurrent positions;
- maximum overlapping positions per symbol;
- maximum absolute signed exposure per currency;
- daily realized-loss lockout in R units.

A long EUR/USD candidate contributes `+1 EUR` and `-1 USD`. A short contributes
`-1 EUR` and `+1 USD`. Exposure units are intentionally simple and auditable; they
are not a substitute for notional, volatility, margin, or covariance modeling.

When candidates share an entry timestamp, the higher `final_score` is considered
first. Positions closing exactly at a candidate entry timestamp are treated as
closed before the new decision.

The daily-loss rule uses only trades whose exit timestamp is at or before the new
candidate timestamp. A future loss on an open position cannot block an earlier
candidate.

## Python usage

```python
from app.backtest.portfolio import PortfolioConstraints, simulate_portfolio

result = simulate_portfolio(
    trade_candidates,
    PortfolioConstraints(
        max_concurrent_positions=3,
        max_same_symbol_positions=1,
        max_abs_currency_exposure=2,
        daily_loss_limit_r=3.0,
    ),
)

print(result.metrics.expectancy)
print(result.rejection_counts)
```

## CLI usage

The input may be a JSON list of serialized `TradeRecord` objects or a dictionary
containing `trades` or `accepted_trades`.

```bash
cd apps/forex-scanner
python scripts/portfolio_backtest_report.py \
  --input-json reports/trade_candidates.json \
  --max-positions 3 \
  --max-same-symbol 1 \
  --max-currency-exposure 2 \
  --daily-loss-limit-r 3
```

Use `0` for `--max-currency-exposure` or `--daily-loss-limit-r` to disable that
constraint.

## Output

The CLI writes:

- accepted trades;
- rejected candidates with the point-in-time reason and portfolio snapshot;
- rejection counts;
- equal-risk performance metrics for accepted trades only;
- an equity curve ordered by realized exit time.

## Current limitations

- each accepted trade is treated as one equal R unit;
- currency exposures are directional units, not notional amounts;
- margin, financing, swaps, partial exits, and dynamic position sizing are not
  included;
- the component consumes completed candidates and does not yet replace the
  portfolio loop inside the main walk-forward runner.

The next integration step is to feed the canonical OOS trade sample through this
allocator before publishing portfolio-level headline metrics.
