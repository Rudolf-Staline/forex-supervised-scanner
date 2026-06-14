# FX data-provider quality & the pluggable provider interface

This note documents the **quality limits of the default Yahoo Finance (yfinance)
FX data feed** and the **pluggable provider interface** that lets you swap the
data vendor without touching the scanner, risk, scoring, or backtest engines.

> Scope: documentation only. This change does **not** replace the data vendor.
> The system remains strictly paper/demo and sends no orders.

## Why this matters

The backtester now models transaction cost as **one full bid-ask spread per
round trip** (see `app/backtest/engine.py::_simulate_trade`), preferring the
per-symbol `spread` column carried by the data and falling back to the fixed
round-trip pip cost in settings. The realism of that cost — and of every
backtest metric derived from it — is therefore capped by the realism of the
underlying data feed.

## yfinance FX data-quality limitations

Yahoo Finance is a convenient, free, no-credentials source, but for FX it has
material limitations that bias backtests **optimistically** if ignored:

1. **No genuine bid/ask spread.** Yahoo returns indicative *mid* prices. There
   is no real spread column, so `YahooFinanceProvider` cannot populate a
   data-driven `spread`. Backtests on this feed fall back to the fixed pip cost,
   which is an assumption, not a measurement. Real execution crosses a spread
   that **widens** around news, rollover (~21:00–22:00 UTC), and illiquid
   sessions — exactly when many breakouts trigger.
2. **OTC, venue-dependent prices.** FX has no single consolidated tape. Yahoo's
   quotes differ from any specific broker/ECN, so highs/lows (and thus
   stop/target touches) will not match a given demo account tick-for-tick.
3. **Coarse and unreliable volume.** FX "volume" from Yahoo is not true traded
   volume; do not build volume-dependent logic on it.
4. **Missing / late / back-adjusted bars.** Intraday history is limited in depth
   and occasionally has gaps, duplicate timestamps, or revised bars. The
   `app/data/validation.py` layer and `app/reporting/data_health.py` exist to
   catch the worst of this, but cannot reconstruct absent ticks.
5. **Weekend and holiday gaps.** Sunday-open gaps appear as large single-bar
   moves; intrabar SL/TP ordering around gaps is approximated conservatively
   (SL-before-TP) but cannot be exact without tick data.
6. **Timezone / DST edges.** Session tagging assumes clean UTC indexing; vendor
   timestamp quirks can nudge a bar across a session boundary.

**Practical guidance:** treat yfinance-based backtests as *directional/relative*
evidence (ranking setups, walk-forward stability), not as absolute P&L
forecasts. For absolute realism, run the local MT5 demo path (Windows), which
provides true per-symbol spreads.

## The pluggable provider interface

All data access goes through one abstract seam, so the vendor is replaceable in
isolation:

```python
# app/data/providers.py
class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> pd.DataFrame:
        """UTC-indexed candles: open/high/low/close/volume and an optional `spread` column."""
```

Contract for any implementation:

- Return a `pd.DataFrame` indexed by tz-aware UTC timestamps, ascending, with
  columns `open, high, low, close, volume` and **optionally** `spread` in
  **price units** (e.g. `0.00012` for EUR/USD, not pips). When `spread` is
  present, the backtester uses it as the round-trip cost.
- Set `df.attrs["provider"]` to `self.name`; optionally `df.attrs["warning"]`
  and `df.attrs["data_quality"]`.
- Raise `DataProviderError` on failure so `AutoFallbackProvider` can degrade
  gracefully.

Existing implementations: `YahooFinanceProvider` (`name="yahoo"`),
`SyntheticForexDataProvider` (`name="synthetic"`, emits a realistic `spread`),
`MetaTrader5Provider` (`name="mt5"`, real broker spreads), and
`AutoFallbackProvider` (MT5 → Yahoo → synthetic).

### Adding a new vendor (no engine changes)

1. Subclass `MarketDataProvider`, implement `get_ohlcv`, set `name`, and emit a
   `spread` column when the vendor exposes bid/ask.
2. Wire it into `build_provider(settings)` behind a `settings.provider.name`
   value.
3. Everything downstream — scanner, risk, scoring, and **scan/backtest parity** —
   is unchanged because they only depend on the `MarketDataProvider` contract.

Because the seam is already abstract, upgrading the FX data source later (e.g. a
spread-bearing REST vendor) is a localized change: implement one class and add
one `build_provider` branch.
