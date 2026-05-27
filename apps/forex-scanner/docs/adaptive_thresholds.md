# Adaptive Thresholds

The Adaptive Thresholds engine is an optional feature in Forex Supervisor designed to dynamically adjust the minimum score required to trigger or approve a trading signal. The adjustment is based on historical backtesting/paper-trading data and the chosen trading style.

**⚠️ WARNING:** This adaptive threshold calculation is for informational/paper testing purposes only. It is not a proof of profitability and must not be used for live trading.

## Configuration

Adaptive thresholds are configured in `app/config/settings.py` (or your overridden JSON config file) under the `adaptive_thresholds` key:

```json
  "adaptive_thresholds": {
    "enabled": false,
    "mode": "report_only",
    "min_sample_size": 30,
    "max_daily_change": 2.0,
    "hard_floor_forex": 70.0,
    "hard_floor_commodities": 78.0,
    "hard_floor_indices": 80.0,
    "hard_cap": 92.0,
    "persist_latest_report": true
  }
```

*   `enabled`: Feature toggle. Default is `false`.
*   `mode`: `report_only` keeps the threshold informational and does not affect signal logic. `scanner_effective` will actually apply the dynamic thresholds during the scan cycle.
*   `min_sample_size`: Minimum number of historical trades needed for a given symbol and style to apply an adjustment.
*   `max_daily_change`: The maximum amount the effective score can diverge from the base score.
*   `hard_floor_*`: Absolute floors per asset class to ensure no threshold drops into unsafe territory.
*   `hard_cap`: The maximum score cap for adaptive thresholds.

## Safety & Fallbacks

*   If history is missing or sample size is below the minimum, the engine defaults back to the static score defined in the application config.
*   The system includes a progressive cap (`max_daily_change`) so thresholds do not swing wildly.
*   If the system encounters an error or crash, it will transparently fail open by reverting to the strict static settings.
*   It operates safely without making external API calls or modifying global `.env` states.

## CLI Reporting

You can calculate the recommended adaptive thresholds locally and export them into CSV/JSON formats:

```bash
python scripts/adaptive_thresholds_report.py --style day_trading --export-json --export-csv
```

Outputs will be placed inside the `reports/` folder.