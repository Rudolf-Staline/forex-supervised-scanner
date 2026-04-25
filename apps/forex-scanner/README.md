# Forex Technical-Analysis Scanner V1

This README belongs to `apps/forex-scanner`. Run the commands below from that directory unless a command explicitly says otherwise.

Local Streamlit app for scanning major Forex pairs with rules-based technical analysis and researching those rules with a simple historical backtester.

This is a decision-support tool, not an unattended auto-trading bot. It includes a gated, supervised broker execution path for sandbox/live evaluation, but paper mode remains the default and live execution is disabled unless multiple explicit safeguards are enabled. Scanner rows rank the best current technical conditions found by the configured rules and never claim safe trades.

## What V1 Includes

- Major and cross pairs: EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/JPY, GBP/JPY, EUR/GBP, EUR/CHF, GBP/CHF, AUD/JPY, CAD/JPY, CHF/JPY, EUR/CAD, GBP/CAD, AUD/CAD, NZD/JPY, optional XAU/USD
- Styles: scalping, day trading, swing trading
- Configurable multi-timeframe mappings:
  - Scalping: M15 / M5 / M1
  - Day trading: H1 / M15 / M5
  - Swing trading: D1 / H4 / H1
- Indicators: EMA 20/50/200, RSI, MACD, ATR, Bollinger Bands, swing highs/lows
- Support and resistance approximation from clustered swings
- Regime detection: trending up, trending down, ranging, breakout candidate, high volatility / unstable, no-trade
- Setup families:
  - Trend continuation after pullback
  - Breakout with confirmation
  - Mean reversion in range markets
- Configurable scoring weights and minimum RR thresholds
- Conservative risk model using ATR, swing, structure invalidation, fixed RR, ATR extension, and nearby technical zones
- Rejected setup diagnostics with pre-gate score, failed gates, rejection category, and estimated RR levels when available
- Explicit setup lifecycle statuses: detected, watchlist, approved, premium, rejected
- Setup subtypes/archetypes for calibration: EMA pullbacks, breakout close/retest/squeeze/momentum, range-edge reversal, volatility-spike fade, and Bollinger snapback
- Separate technical, execution, context, empirical, and final scores, plus A/B/C/D setup grade
- TP1/TP2/TP3 risk plans with conservative, balanced, and aggressive target profiles
- Provider data-quality diagnostics for missing/irregular bars, stale data, spread availability, resampling, and duplicate bars
- Backtesting with win rate, average win/loss, profit factor, max drawdown, expectancy, trade count, simplified Sharpe-like metric, TP-hit flags, MAE/MFE, bars-to-event fields, and richer outcome labels
- Local paper trading foundation for approved/premium opportunities, with pending, missed, expired, cancelled, open, partially closed, and fully closed lifecycle states
- Paper execution assumptions for spread-aware fills, slippage, partial TP1/TP2/TP3 exits, optional breakeven stop movement after TP1, stale-signal expiry, missed gap triggers, and pre-entry invalidation
- Structured paper-trade journal and event-level audit trail for signal approval, activation, entry, partial exits, stop moves, closure, blocks, missed trades, expiry, and cancellation
- Controlled broker execution path with explicit `paper`, `broker_sandbox`, and gated `broker_live` modes
- Optional MetaTrader 5 broker adapter plus mock sandbox adapter for tests/preflight checks
- Broker order state machine: intent created, validation failed, submitted, acknowledged, partially filled, filled, rejected, cancelled, expired, modified, close requested, closed, and reconciliation mismatch
- Broker reconciliation reports for missing broker/internal state, partial-fill differences, stop/target mismatches, manual broker-side changes, and stale local state
- Broker operational health snapshots, incident classification, startup recovery checks, and manual-intervention reporting
- Persistent operational metrics, local alerting with suppression windows, and monitoring reports for supervised broker evaluation
- Local Prometheus textfile-style operational metrics export for external monitoring pickup without adding a SaaS dependency
- Grafana-ready operational dashboard JSON plus metric-driven alert rules, local alert sink, and optional gated webhook routing
- Operator controls for maintenance mode, degraded mode, broker/live submission gating, incident acknowledgement, and resume-live readiness checks
- Soak-validation workflow plus multi-session campaigns for long-duration, non-submitting paper/sandbox health, reconciliation, alert, incident, and readiness evaluation
- Structured pre-session checklist, manual pre-live authorization with expiry, supervised session open/close workflow, explicit handover/continuity controls, and queryable operator action records
- Lightweight local operator identities, authenticated operator sessions, signed approval records for sensitive actions, and re-auth prompts for higher-risk supervised workflows
- Tamper-evident audit integrity plus local retention/archive packages, rotation planning, and non-destructive restore-for-review workflows for long-term evidence preservation
- Structured local backup, disaster-recovery, restore-review, explicit active-restore, and post-restore continuity validation workflows
- Optional portfolio/session guardrails for simultaneous trades, net/gross currency exposure, correlated-symbol exposure, symbol exposure, setup-family/subtype/session exposure, daily loss, loss-streak cooldown, abnormal spread, degraded data quality, and off-hours blocking
- Pre-live validation checks before paper intent creation: data quality, spread tolerance, portfolio guardrails, signal freshness, required levels, invalidation, and session policy
- SQLite persistence for scan results, settings snapshots, backtest runs, selected symbols, paper orders, broker orders, reconciliation anomalies, trade events, journal entries, and paper blocks
- Calibration reports for score buckets, layer predictiveness, lifecycle status, setup family/subtype, symbol, session, regime, top-K precision, expectancy, empirical lift, false-positive rate, and suggested layer weights

## Setup Logic

V1 uses rules, not machine learning. Each setup family produces raw candidates first, then the risk and scoring engines decide whether it becomes a ranked opportunity.

- Trend continuation after pullback:
  - Eligible in higher-timeframe trending up/down regimes.
  - Looks for a pullback into EMA20/EMA50, recovery on the entry/trigger timeframes, and momentum that does not fight the trend.
  - Invalidation uses recent swing, EMA50 structure, ATR, and nearby support/resistance when available.

- Breakout with confirmation:
  - Eligible in breakout-candidate, ranging, or directional trend regimes.
  - Requires a close beyond recent support/resistance plus a configurable ATR buffer and trigger-timeframe confirmation.
  - Invalidation uses the broken support/resistance zone plus normal ATR/structure stop candidates.

- Mean reversion in range markets:
  - Eligible in ranging or breakout-candidate regimes only.
  - Looks for support/lower-band or resistance/upper-band tests with RSI turning from an extreme.
  - Invalidation uses the rejected support/resistance or Bollinger-zone structure.

The scanner keeps raw setup evaluation separate from final approval. A symbol can therefore return:

- detected: a technical setup exists, but conditions are not close enough for immediate activation
- watchlist: a setup is forming and the UI lists what must improve before approval
- approved: risk, score, and setup rules currently pass
- premium: approved setup with stronger final score, context, empirical profile, and execution quality
- rejected: no raw setup exists or risk/data quality is too weak to evaluate usefully

If no raw setup is detected at all, the scanner returns a rejected row with regime context rather than inventing a setup.

Each evaluated candidate exposes these diagnostic gates:

- trend
- structure
- momentum
- volatility
- multi-timeframe alignment
- minimum RR
- score threshold

Rejected candidates include a concise rejection summary and a main rejection category such as weak structure, insufficient RR, conflicting timeframes, weak momentum, unsuitable volatility, weak execution, weak context, low empirical support, poor data quality, weak activation, weak invalidation, score below threshold, or invalid risk.

## Scoring Model

Scores are weighted to 0-100 using weights from JSON settings:

- trend clarity
- structure quality
- multi-timeframe alignment
- volatility suitability
- momentum confirmation
- spread/friction score when spread is available
- risk/reward attractiveness
- proximity to important technical levels

Confidence buckets are configurable: low, medium, high.

Scores are computed before hard rejection whenever a raw setup exists:

- technical score: trend, structure, multi-timeframe alignment, volatility, momentum, and level proximity
- execution score: spread/friction, spread-to-stop, risk/reward, target clearance, activation quality, invalidation quality, and execution-side data quality
- context score: session quality, spread/ATR, exploitable volatility, data freshness, duplicate/missing bars, and data-quality diagnostics
- empirical score: calibration-history score for comparable setup outcomes, with a neutral default until enough samples exist
- final score: configured layer-weighted score used for ranking and approval

Risk/reward and spread-friction components score as failed when no valid risk plan can be built, but trend, structure, momentum, volatility, alignment, and level-proximity components still explain the partial quality of the candidate.

Backtest and scan persistence now records enough structured analytics for later calibration: setup subtype, session, higher/entry/trigger regimes, layer scores, component subscores, spread, ATR, key-level distances, data-quality diagnostics, status, and realized outcome fields where available.

The empirical layer uses smoothed historical comparables rather than a hard exact-match lookup. It checks narrower groups first, such as subtype + symbol + session + regime + style, then backs off through broader groups such as subtype + symbol, subtype + session, symbol + style, subtype, family + style, and family. Sparse groups are shrunk toward the neutral empirical score so one or two wins cannot create overconfident approved trades.

Default layer weights are calibrated conservatively for live-like use, with context and empirical history given enough influence to affect ranking while still avoiding overfit when local outcome history is thin:

- technical: 0.30
- execution: 0.30
- context: 0.24
- empirical: 0.16

This gives execution and context more influence in final ranking while keeping empirical influence modest until enough local calibration history exists. Approval also checks minimum data quality, activation quality, and invalidation quality separately from the final score, so a technically good setup can remain watchlist instead of becoming approved when execution conditions are not clean enough.

## Risk Model

The risk engine supports:

- ATR-based stops
- swing-based stops
- structure invalidation stops
- fixed RR targets
- next technical-zone targets
- ATR extension targets

By default, the engine chooses the most conservative valid stop. A nearby technical level no longer automatically rejects a setup that can still form a valid RR plan; instead it affects execution diagnostics and target-clearance scoring. This avoids hiding technically valid setups while still surfacing the nearby level as a practical obstacle.

The risk plan exposes:

- primary target based on the configured target profile
- TP1, TP2, TP3
- RR for the primary target and each staged target
- invalidation notes from the setup detector

Default minimum RR is now calibrated for more live-market observability while remaining strict:

- scalping: 1.1
- day trading: 1.5
- swing trading: 1.7

## Architecture

```text
app/
  backup/          local backup packages, restore workflows, and continuity validation
  backtest/        custom event-style backtester and metrics
  config/          pydantic settings models and default JSON config
  core/            typed domain models and scanner orchestration
  data/            provider interface, MT5 primary, Yahoo fallback, deterministic dev/test fallback
  indicators/      indicator calculations and technical levels
  market_regime/   regime, volatility, and swing-structure classification
  execution/       execution adapter contract, broker adapters, pre-live validation, reconciliation, and paper executor
  paper/           paper-trading orchestration, journal exports, and portfolio reports
  reporting/       calibration CSV and Markdown report generation
  risk/            SL/TP, RR validation, and portfolio guardrails
  scoring/         weighted score and confidence buckets
  setups/          setup-family detection rules
  storage/         SQLite repository
  ui/              Streamlit interface
  utils/           structured logging
scripts/
  init_db.py       creates the local SQLite schema
  smoke_check.py   deterministic scanner/backtester smoke validation
  calibration_backtest.py compact parameter-profile comparison
  calibration_report.py emits persisted calibration CSV/Markdown reports
  paper_trade.py   submits approved/premium opportunities to local paper mode
  paper_report.py  emits paper portfolio Markdown/CSV/JSON reports
  journal_export.py emits paper journal and lifecycle-event exports
  broker_check.py  safe broker connectivity/account preflight; never submits orders
  broker_recovery.py safe startup/restart sync and reconciliation without submissions
  broker_monitor.py repeated local health/recovery monitoring without submissions
  broker_control.py scriptable operator controls for maintenance/degraded mode and submission gates
  operator_session.py checklist, pre-live authorization, handover/session workflows, and operator workflow reports
  audit_integrity.py verifies, seals, reports, and exports tamper-evident audit evidence
  archive_records.py retention candidates, archive packages, rotation plans, and restore-for-review
  backup_recovery.py local backup, restore-review, explicit active restore, and post-restore validation
  metrics_export.py Prometheus textfile export and local exporter-health check
  alert_rules.py   evaluates metric-driven alert rules and writes alert summaries/routes
  soak_test.py     long-duration non-submitting operational validation workflow
  soak_campaign.py multi-session soak campaign orchestration and weekly-style reports
  broker_submit.py dry-run validates broker submissions by default; requires --submit to place
  broker_report.py emits broker execution/reconciliation reports
tests/
  pytest coverage for config, indicators, regime, setups, risk, scoring, outcomes, storage, reports, metrics
```

## Clean Deliverable Tree

Generated runtime artifacts are not part of the deliverable. The project can recreate its SQLite database with `python scripts/init_db.py`.

```text
README.md
pyproject.toml
requirements.txt
streamlit_app.py
app/
  backup/
  backtest/
  config/
  core/
  data/
  indicators/
  market_regime/
  reporting/
  risk/
  scoring/
  setups/
  storage/
  ui/
  utils/
config/
  settings.example.json
docs/
  operations.md
  runbooks/
    broker_operations.md
scripts/
  init_db.py
  smoke_check.py
  calibration_backtest.py
  calibration_report.py
  paper_trade.py
  paper_report.py
  journal_export.py
  broker_check.py
  broker_recovery.py
  broker_monitor.py
  broker_control.py
  operator_session.py
  audit_integrity.py
  archive_records.py
  backup_recovery.py
  metrics_export.py
  alert_rules.py
  soak_test.py
  soak_campaign.py
  broker_submit.py
  broker_report.py
tests/
```

Do not ship `.git`, `__pycache__`, `.pytest_cache`, Streamlit logs, or runtime SQLite databases unless you intentionally create a separate sample-data package.

## Release Quick Start

Install:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Initialize the local database:

```powershell
python scripts/init_db.py
```

Run the app:

```powershell
streamlit run streamlit_app.py
```

Run tests:

```powershell
python -m pytest
```

Run the smoke check:

```powershell
python scripts/smoke_check.py
```

The smoke check succeeds when deterministic data produces either an approved setup or a fully diagnosed watchlist/detected setup plus a minimal backtest run. This keeps release validation honest when the stricter execution/context gates correctly prevent an immediate approved trade.

Generate calibration reports from the local SQLite database:

```powershell
python scripts/calibration_report.py --db data/forex_scanner.sqlite --out reports/calibration
```

Run a scan and submit only approved/premium rows to local paper mode:

```powershell
python scripts/paper_trade.py --style day_trading --symbols EUR/USD GBP/USD USD/JPY
```

Generate a paper portfolio report:

```powershell
python scripts/paper_report.py --db data/forex_scanner.sqlite --out reports/paper
```

Export the trading journal and event trail:

```powershell
python scripts/journal_export.py --db data/forex_scanner.sqlite --out reports/journal
```

Run a broker preflight without submitting any order:

```powershell
python scripts/broker_check.py --mode broker_sandbox
```

## Install

Python 3.11+ is required. The current project was verified with Python 3.12.

`pyproject.toml` is the canonical package/dependency definition. `requirements.txt` is a convenience bridge that installs the project in editable mode with the test extra.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configure

The app loads settings in this order:

1. `FOREX_SCANNER_CONFIG`
2. `config/settings.json`
3. `app/config/default_settings.json`

To create an editable local config:

```powershell
Copy-Item config/settings.example.json config/settings.json
```

You can also save validated settings from the Streamlit Settings screen.

The main configurable groups are:

- `styles`: timeframe mapping, minimum RR, ATR stop/target multipliers, swing buffer, lookback bars, max holding bars, transaction cost pips
- `weights`: scoring weights
- `layer_weights`: technical/execution/context/empirical blend for final score
- `context`: data-quality and session penalty settings
- `empirical`: neutral score, minimum sample size, condition minimum, shrinkage strength, and maximum empirical adjustment
- `approval`: minimum execution/context/empirical/data-quality/activation/invalidation gates for approved status plus stricter premium thresholds
- `execution`: paper mode, default paper quantity, slippage estimate, spread-aware fills, partial-exit fractions, breakeven stop movement, gap-through-entry policy, pre-entry invalidation cancellation, and activation timeout
- `execution_capabilities`: explicit operator capability gates for paper, broker sandbox, and broker live paths
- `broker`: broker provider, sandbox/live gates, MT5 env var names, volume caps, MT5 magic number, order deviation, and comment prefix
- `broker_safety`: broker-specific caps for notional, risk per trade, daily submissions, repeated rejects, open positions, reconciliation anomaly blocking, account/connectivity requirements, and duplicate-symbol prevention
- `broker_retry`: bounded retry behavior for account state, order status, position sync, reconciliation refresh, and intentionally disabled blind order-send retry by default
- `monitoring`: local operational metric persistence, alert suppression window, stale-state thresholds, and alert escalation thresholds
- `monitoring.metrics_export_*`: optional Prometheus textfile-style metrics output, enabled by default to `reports/broker/forex_scanner.prom`
- `monitoring.alert_*`: alert rule enablement, dedup/suppression windows, local JSONL sink, optional webhook endpoint env var, bounded webhook retry, and resolved-alert notification behavior
- `monitoring.dashboard_output_dir`: repo-contained dashboard artifacts, defaulting to `docs/dashboards`
- `soak`: default soak duration, polling interval, allowed modes, output path, readiness thresholds, anomaly thresholds, campaign duration, campaign readiness thresholds, and safe live-check gating
- `portfolio_risk`: optional paper-entry guardrails for net/gross currency exposure, correlated symbols, symbol/setup/session concentration, simultaneous trades, daily loss, cooldown, spread/ATR, minimum data quality, and off-hours blocking
- `pre_live_validation`: explicit pre-execution checks for signal freshness, required levels, data quality, setup invalidation, and session policy
- `operator_workflow`: checklist requirements, warning/fail thresholds, authorization expiry, handover acceptance/expiry policy, minimum campaign readiness, and acknowledgement/dual-confirmation policy
- `operator_auth`: local operator identities, role metadata, auth-session expiry, approval-signature expiry, re-auth window, and comment/reauth requirements for sensitive approvals
- `audit_integrity`: protected record scope, verification strictness, seal triggers, report/export locations, and sensitive-action blocking behavior after failed verification
- `retention_archive`: retention windows, archive/restore paths, file-size rotation threshold, rotation safety defaults, archive verification behavior, and destructive cleanup gates
- `backup_recovery`: local backup destinations, restore-review paths, compression, verification strictness, backup scope toggles, active-restore gates, and post-restore sensitive-action blocking
- `setups`: enable/disable flags, minimum score gates, pullback/breakout/range thresholds
- `risk`: conservative stop behavior, target profile, and spread/ATR guardrails
- `provider`: data provider choice, environment, synthetic fallback behavior, and production synthetic guard

Current default calibration:

- trend_continuation minimum score: 52
- breakout_confirmation minimum score: 55
- mean_reversion minimum score: 50
- pullback_ema_tolerance_atr: 0.95
- breakout_buffer_atr: 0.15
- range_rsi_low / high: 42 / 58
- level_tolerance_atr: 0.75
- confidence medium / high: 55 / 72
- reject_if_nearest_level_blocks_min_rr: false
- max_spread_to_atr_ratio: 0.22

## Initialize The Database

```powershell
python scripts/init_db.py
```

By default this creates `data/forex_scanner.sqlite`.

## Run The App

```powershell
streamlit run streamlit_app.py
```

Then open the local URL printed by Streamlit, usually `http://localhost:8501`.

## Streamlit UI

The Streamlit app has three workspaces:

- Scanner:
  - trading style selector
  - symbol universe selector
  - scan button
  - ranked opportunities table
  - filters by status, score, RR, and data quality
  - filters by technical, execution, context, empirical, and activation quality
  - explicit status, reason, missing-condition, and data-quality columns
  - raw setup, setup subtype, session, technical score, execution score, context score, empirical score, final score, grade, failed gates, rejection category, and required RR diagnostics
  - opportunity detail panel with regime, direction, score, entry, stop, TP1/TP2/TP3, RR, explanation, and score components
  - pass/fail gate breakdown for rejected candidates
  - Plotly candlestick chart with EMA20/50/200, Bollinger Bands, entry/SL/TP annotations, and support/resistance levels

- Backtest:
  - setup/style/symbol/date filters
  - metrics summary
  - equity curve
  - trade log
  - limitations note

- Settings:
  - provider choice and fallback behavior
  - scoring weights
  - minimum RR by style
  - style timeframe mapping
  - ATR stop/target multipliers
  - swing buffer, lookback bars, max hold bars, transaction cost pips
  - setup enable/disable flags
  - minimum score gates and setup thresholds

UI errors are caught at scan, backtest, settings-save, and chart-loading boundaries so a bad provider response or invalid config is shown to the user instead of crashing the page.

## Run Tests

```powershell
python -m pytest
```

Run the deterministic integration smoke check:

```powershell
python scripts/smoke_check.py
```

Run a small calibration comparison:

```powershell
python scripts/calibration_backtest.py --synthetic --start 2025-01-10 --end 2025-01-14
```

Generate persisted calibration reports:

```powershell
python scripts/calibration_report.py --db data/forex_scanner.sqlite --out reports/calibration
```

Key report outputs:

- `score_buckets.csv`: win rate, expectancy, false-positive rate, TP hit rates, MAE/MFE, and monotonicity columns by final-score bucket
- `layer_score_buckets.csv`: the same bucket diagnostics separately for technical, execution, context, empirical, and final scores
- `layer_predictiveness.csv`: Spearman correlations between each score layer and realized R / win flag
- `by_status.csv`: premium vs approved vs watchlist performance
- `status_separation.csv`: expectancy, win-rate, and false-positive deltas for premium vs approved and approved vs watchlist
- `conditional_combinations.csv`: subtype-symbol, subtype-session, subtype-regime, and symbol-style performance for empirical adjustment review
- `paper_lifecycle.csv`: count and realized R by paper lifecycle status
- `paper_execution_summary.csv`: paper orders, realized paper trades, blocked count, expectancy, precision, and false-positive rate
- `paper_blocks.csv`: guardrail block reasons by symbol
- `best_worst_combinations.csv`: best and worst subtype, symbol, session, and regime groups where enough samples exist
- `empirical_lift.csv`: top-K ranking comparison with and without the empirical layer
- `suggested_layer_weights.json`: bounded layer-weight suggestions from observed score/outcome correlations, or an insufficient-sample warning
- `summary.json`: compact machine-readable report summary for later calibration tooling

## Paper Trading

Paper trading is local simulation only. It does not route orders to a broker.

The execution abstraction lives behind a small adapter contract:

- `create_order_intent`
- `place_order`
- `modify_order`
- `close_order`
- `partial_close_order`
- `cancel_order`
- `sync_positions`
- `query_order_status`
- `reconcile`

The included paper executor starts approved/premium opportunities as pending orders, activates them when completed bars touch the entry, applies configured spread/slippage assumptions, tracks TP1/TP2/TP3 partial exits, can move the stop to breakeven after TP1, records bars/time in trade, tracks MAE/MFE, and computes realized paper R / paper PnL. Before a paper order is created, pre-live validation and portfolio guardrails can block weak operational conditions such as stale signals, missing levels, too many open trades, excessive net/gross currency exposure, correlated-symbol concentration, symbol/setup/session concentration, poor data quality, abnormal spread/ATR, off-hours entries, daily loss limit, or a loss-streak cooldown.

Paper lifecycle states:

- `pending_opportunity`: approved/premium signal accepted by guardrails but not yet activated
- `open_trade`: entry activated and position is live in paper mode
- `partially_closed_trade`: one or more configured TP levels have closed a fraction of the trade
- `fully_closed_trade`: remaining position closed by TP, SL, or manual close
- `missed_trade`: price gapped through the entry condition under the configured miss policy
- `expired_trade`: entry was not activated before the configured timeout
- `cancelled_trade`: opportunity was manually cancelled or invalidated before entry
- `rejected`: request could not be represented as an executable paper order

Tracked paper fields include signal timestamp, activation/entry timestamp, simulated entry, SL, TP1/TP2/TP3, partial exit prices, realized R, paper PnL, bars/time in trade, MAE/MFE, slippage/spread adjustments, execution assumptions, portfolio snapshot, and cancellation/expiration/invalidation reason when applicable.

Every paper order and blocked candidate also emits auditable lifecycle events such as `signal_approved`, `signal_premium`, `trade_entered`, `trade_partially_closed`, `stop_moved`, `trade_closed`, `trade_blocked`, and `guardrail_triggered`. The journal layer reconstructs status transitions, rationale, execution assumptions, stop movement history, partial close history, realized outcome, and block reasons for operator review.

Create paper orders:

```powershell
python scripts/paper_trade.py --style day_trading --symbols EUR/USD GBP/USD USD/JPY
```

Paper orders are stored in the local SQLite database table `paper_orders`.
Blocked approved/premium opportunities are stored in `paper_blocks` with guardrail reasons and portfolio snapshots.
Lifecycle events are stored in `trade_events`, and rebuilt operator journal rows are stored in `trading_journal`.

Generate paper reports:

```powershell
python scripts/paper_report.py --db data/forex_scanner.sqlite --out reports/paper
```

Key paper report outputs:

- `summary.md` and `summary.json`: open, pending, closed, blocked, realized R/PnL, win rate, expectancy, and drawdown
- `orders.csv`: lifecycle, levels, partial exits, MAE/MFE, realized R, and paper PnL
- `blocked.csv`: blocked opportunities and guardrail reasons
- `guardrail_triggers.csv`: one row per structured block reason
- `daily_summary.csv` and `weekly_summary.csv`: closed-trade summaries by period
- `score_vs_realized.csv` and `status_realized.csv`: score/status versus realized paper outcomes
- `exposure_by_currency.csv`, `exposure_by_symbol.csv`, `exposure_by_subtype.csv`, `exposure_by_session.csv`: current open/pending exposure

Export the journal and event trail:

```powershell
python scripts/journal_export.py --db data/forex_scanner.sqlite --out reports/journal
```

Key journal outputs:

- `journal.csv` / `journal.json`: signal, trade, status transitions, rationale, execution assumptions, stop movements, partial closes, realized PnL/R, MAE/MFE, and block/cancel/expiry reasons
- `events.csv` / `events.json`: reconstructable event stream for every paper trade or block
- `summary.md`: compact operator review summary

## Broker Sandbox And Live-Gated Path

Broker execution is opt-in. The default `execution.mode` remains `paper`.

Supported execution modes:

- `paper`: local simulation only; default and safest workflow
- `broker_sandbox`: broker adapter path intended for demo/sandbox accounts and operator-supervised checks
- `broker_live`: blocked unless `execution_capabilities.broker_live_enabled=true`, `broker.live_enabled=true`, and the live confirmation environment variable is present

The current concrete broker adapter is MetaTrader 5 (`broker.provider=mt5`). The adapter is optional and fails safely if the `MetaTrader5` Python package, local terminal, or required account state is unavailable. A `mock` provider exists for deterministic tests and local sandbox preflight only; it is rejected for `broker_live`.

`MetaTrader5` is intentionally not installed by default requirements. Install the optional broker extra only on a machine where the local terminal/account setup is intended for supervised sandbox or gated live evaluation:

```powershell
python -m pip install -e ".[broker]"
```

Safe broker preflight, no order submission:

```powershell
python scripts/broker_check.py --mode broker_sandbox
```

Mock sandbox preflight for local development:

```powershell
python scripts/broker_check.py --mode broker_sandbox --provider mock
```

Broker submission script defaults to dry-run validation only:

```powershell
python scripts/broker_submit.py --mode broker_sandbox --symbols EUR/USD GBP/USD
```

Sandbox submission requires an explicit flag:

```powershell
python scripts/broker_submit.py --mode broker_sandbox --symbols EUR/USD --submit
```

Live submission additionally requires `--allow-live`, `broker.live_enabled=true`, and the live confirmation environment variable. The script still runs all broker validation and live-safety gates before any adapter call.

MT5 environment variables:

- `FOREX_SCANNER_MT5_LOGIN`
- `FOREX_SCANNER_MT5_PASSWORD`
- `FOREX_SCANNER_MT5_SERVER`
- `FOREX_SCANNER_MT5_PATH` optional terminal path

Use `.env.example` as a template for local secret names. Do not commit a populated `.env` file.

Live mode is intentionally gated:

- set `execution.mode` to `broker_live`
- set `execution_capabilities.broker_live_enabled` to `true`
- set `broker.live_enabled` to `true`
- set `FOREX_SCANNER_BROKER_LIVE_CONFIRM=ENABLE_LIVE_TRADING`
- keep `FOREX_SCANNER_BROKER_KILL_SWITCH` unset or false
- pass account, connectivity, spread, data-quality, stale-signal, portfolio, duplicate-symbol, notional, risk, daily-submission, repeated-reject, and reconciliation checks

Broker submission uses the same validation concepts as paper trading, then adds broker-specific checks:

- broker connectivity healthy
- account state retrieved successfully
- account state recent enough for configured safety threshold
- account tradable and margin available
- position sizing resolved and capped
- live confirmation present when applicable
- no active kill switch
- no blocking reconciliation anomalies
- no duplicate symbol when configured
- daily submission and daily risk budgets not exhausted
- repeated broker reject and connectivity-failure caps not exceeded
- no open high/critical operational incidents
- no degraded health flags that require manual intervention

Broker order states are persisted as transitions and events: `intent_created`, `pretrade_validated`, `validation_failed`, `submit_requested`, `submitted`, `acknowledged`, `partially_filled`, `filled`, `rejected`, `cancel_requested`, `cancelled`, `modify_requested`, `modified`, `close_requested`, `closed`, `reconciliation_mismatch`, `broker_unreachable`, `retry_exhausted`, and `manual_intervention_required`.

Operational broker events are also journaled: health degradation, startup re-sync, recovery actions, incidents opened/closed, and execution blocks caused by operational safety rules.

Retry behavior is bounded by `broker_retry`:

- account state, order status, position sync, and reconciliation refresh can retry up to `max_attempts`
- retries use `backoff_seconds`
- order submission is not blindly retried by default, because duplicated orders are more dangerous than a missed acknowledgement
- if an acknowledgement is missing, the order is marked for manual intervention and the terminal/broker account must be checked before another submission

Common safe-failure modes:

- missing `MetaTrader5` package: broker account health returns unavailable and non-tradable
- terminal initialization failure: execution blocks with a connectivity/configuration reason
- missing account info: execution blocks with account-unavailable reason
- stale account state: broker validation blocks new orders
- repeated rejects or reconciliation anomalies: broker validation blocks new orders until reviewed
- broker health degraded or high/critical incidents open: broker validation blocks new submissions
- restart finds orphaned broker/local state: recovery report records the mismatch and requires review
- kill switch active: live execution blocks immediately

Run startup/restart recovery before supervised broker work, especially after an interrupted process:

```powershell
python scripts/broker_recovery.py --mode broker_sandbox --provider mock
python scripts/broker_recovery.py --mode broker_sandbox
```

Recovery performs a non-submitting account health check, broker order/position refresh, reconciliation, health snapshot persistence, incident classification, event journaling, and report generation. In `broker_live`, it still requires `--allow-live` plus the same config/env gates.

Run repeated local monitoring samples during supervised sandbox validation:

```powershell
python scripts/broker_monitor.py --mode broker_sandbox --provider mock --iterations 3 --interval-seconds 5
python scripts/broker_monitor.py --mode broker_sandbox --iterations 3 --interval-seconds 60
```

Monitoring persists health snapshots, operational metrics, alerts, incidents, reconciliation status, and daily summaries. It never submits broker orders.

Evaluate actionable alert rules and write operator alert summaries:

```powershell
python scripts/alert_rules.py --db data/forex_scanner.sqlite --out reports/alerts
python scripts/alert_rules.py --db data/forex_scanner.sqlite --out reports/alerts --route
```

`--route` writes to the local JSONL sink by default. Webhook routing remains disabled unless `monitoring.alert_webhook_enabled=true` and the environment variable named by `monitoring.alert_webhook_url_env` is set.

Run a non-submitting soak validation:

```powershell
python scripts/soak_test.py --mode paper --duration-minutes 5 --interval-seconds 30
python scripts/soak_test.py --mode broker_sandbox --provider mock --duration-minutes 15 --interval-seconds 60
python scripts/soak_test.py --mode broker_sandbox --duration-minutes 60 --interval-seconds 60
```

The soak runner repeatedly invokes safe observation/recovery checks, persists samples to SQLite, polls alerts/incidents, computes reliability metrics, detects repeated stale or degraded states, and emits a conservative readiness recommendation. It does not submit orders. `broker_live` soak checks are disabled by default and require `--allow-live`, `soak.allow_broker_live_checks=true`, the existing live config gates, and the live confirmation environment variable.

Run a multi-session soak campaign for weekly-style supervised validation:

```powershell
python scripts/soak_campaign.py start --name weekly-sandbox --mode broker_sandbox --provider mock --target-hours 168
python scripts/soak_campaign.py run-session --name weekly-sandbox --provider mock --duration-minutes 30 --interval-seconds 60
python scripts/soak_campaign.py status --name weekly-sandbox
python scripts/soak_campaign.py finalize --campaign-id <campaign_id> --out reports/soak_campaigns
```

Campaign readiness is deliberately stricter than a single soak run:

- `not_ready`: blocking operational issues or insufficient evidence remain
- `limited_ready`: continue supervised validation with reduced scope
- `supervised_ready`: campaign stayed within configured thresholds and is ready for manual operator review only

Campaign reports live under `reports/soak_campaigns/<campaign_id>/` and include `campaign_summary.md`, `weekly_reliability.md`, `readiness.md`, `recurring_anomalies.md`, `campaign_timeline.csv`, `restart_recovery_events.csv`, `alert_incident_burden.csv`, and `unresolved_issues.csv`.

Inspect or change operator controls:

```powershell
python scripts/broker_control.py
python scripts/broker_control.py --maintenance on --reason "operator review"
python scripts/broker_control.py --broker-submissions off --reason "pause after incident"
python scripts/broker_control.py --degraded on --reason "reduced-risk monitoring"
```

`broker_control.py` persists a local control record in SQLite. Broker submissions are blocked during maintenance mode or when broker submissions are disabled. `broker_live` additionally requires the live submission control to be enabled, on top of the existing config and environment gates. Recovery, monitor, report, and submit flows compute a resume-readiness status of `safe_to_resume`, `degraded_but_safe`, or `blocked_pending_manual_review`.

Run the operator workflow surface:

```powershell
python scripts/operator_session.py --operator-id operator sign-in --secret operator-pass
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> checklist --acknowledge
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> open --acknowledge-checklist --confirm --comment "London supervised session"
python scripts/operator_session.py --mode broker_live --operator-id supervisor sign-in --secret supervisor-pass
python scripts/operator_session.py --mode broker_live --operator-id supervisor --auth-session-id <auth_session_id> authorize-live --acknowledge-checklist --confirm --comment "manual review complete" --reauth-secret supervisor-pass
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> close --handoff-required --comment "carry-over review needed"
python scripts/operator_session.py --operator-id supervisor --auth-session-id <auth_session_id> handover-accept --acknowledge --comment "carry-over reviewed" --reauth-secret supervisor-pass
python scripts/operator_session.py --operator-id operator --auth-session-id <auth_session_id> open --acknowledge-checklist --confirm --comment "Next supervised session"
python scripts/operator_session.py --operator-id operator status --out reports/operator
python scripts/operator_session.py --operator-id supervisor --auth-session-id <auth_session_id> sign-out
```

The operator workflow now adds explicit local operator sign-in/sign-out, identity-linked audit records, approval signatures tied to an authenticated operator session, and explicit re-auth for higher-risk actions such as pre-live authorization. It still does not bypass broker validation, reconciliation, incidents, alerts, or broker/live capability gates.

Verify and export audit evidence:

```powershell
python scripts/audit_integrity.py --db data/forex_scanner.sqlite verify --out reports/audit_integrity
python scripts/audit_integrity.py --db data/forex_scanner.sqlite seal --trigger manual --notes "operator checkpoint"
python scripts/audit_integrity.py --db data/forex_scanner.sqlite export --out reports/audit_evidence
```

Archive old operational evidence and restore it for review:

```powershell
python scripts/archive_records.py candidates --db data/forex_scanner.sqlite --out reports/archives
python scripts/archive_records.py create --db data/forex_scanner.sqlite --out archives/operational --report-out reports/archives
python scripts/archive_records.py verify --archive archives/operational/<archive_id>.zip --out reports/archives
python scripts/archive_records.py restore-review --archive archives/operational/<archive_id>.zip --restore-dir archives/restore_review --out reports/archives
```

`archive_records.py rotation-plan` reports what exceeds retention policy and what remains blocked. Active database purge and file movement are disabled by default so retention maintenance cannot silently weaken audit integrity.

Create and verify a local disaster-recovery backup:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite create --out backups/local --report-out reports/backup_recovery --label operator-checkpoint --reason "manual checkpoint"
python scripts/backup_recovery.py --db data/forex_scanner.sqlite list --out backups/local --report-out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite verify --backup backups/local/<backup_id>.zip --out reports/backup_recovery
```

Restore a backup into a non-destructive review/staging area:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite restore-review --backup backups/local/<backup_id>.zip --restore-dir backups/restore_review --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite post-restore-check --out reports/backup_recovery
```

Active restore is disabled by default. Use it only after verifying the package, reviewing the extracted state, and intentionally enabling the one-shot confirmation:

```powershell
python scripts/backup_recovery.py --db data/forex_scanner.sqlite restore-active --backup backups/local/<backup_id>.zip --target-db data/forex_scanner.sqlite --allow-active-restore --confirm-active-restore --out reports/backup_recovery
python scripts/backup_recovery.py --db data/forex_scanner.sqlite post-restore-check --out reports/backup_recovery
```

After an active restore, sensitive broker-live workflows remain blocked until post-restore validation passes and the operator reviews `reports/backup_recovery/recovery_validation_status.md`.

Bootstrap identities are defined in config for local first-run convenience:

- `viewer` with passphrase `viewer-pass`
- `operator` with passphrase `operator-pass`
- `supervisor` with passphrase `supervisor-pass`
- `admin` with passphrase `admin-pass`

These bootstrap passphrases are intentionally lightweight and should be replaced in local settings before any serious supervised usage.

Emergency disable:

```powershell
$env:FOREX_SCANNER_BROKER_KILL_SWITCH="true"
```

Clear it only after reviewing broker state and reports:

```powershell
Remove-Item Env:\FOREX_SCANNER_BROKER_KILL_SWITCH
```

Generate broker reports:

```powershell
python scripts/broker_report.py --db data/forex_scanner.sqlite --out reports/broker
```

Key broker report outputs:

- `summary.md` / `summary.json`: submitted, acknowledged, filled, rejected, closed, and reconciliation anomaly counts
- `broker_orders.csv`: broker ids, mode, state, status, symbol, fills, and reconciliation status
- `reconciliation_anomalies.csv`: broker/internal mismatches for operator review
- `incident_report.csv` / `incident_report.json` / `incident_report.md`: operational incidents, severity, blocking status, and recovery recommendations
- `broker_health_history.csv`: health snapshots, degraded flags, and last successful sync timestamps
- `operational_metrics.csv`: metric history for connectivity, syncs, retries, guardrails, stale detections, incidents, and recovery events
- `alert_summary.csv` / `alert_summary.json` / `alert_summary.md`: local alerts, severities, active/resolved state, dedupe keys, and recommendations
- `live_guardrails.csv`: broker validation failures, live guardrail triggers, and reconciliation blocks
- `broker_health.json` / `broker_health.md`: connectivity, tradability, account-state freshness, and last-error summary
- `order_lifecycle.csv`: every broker state transition in chronological form
- `rejections.csv`: broker validation failures, retries exhausted, and broker rejects
- `manual_intervention.csv`: events/anomalies that require human review before further broker use
- `restart_recovery.csv` / `restart_recovery.md`: startup/recovery status, open local orders, anomalies, incidents, and degraded flags
- `broker_reliability.csv` / `broker_reliability.md`: connected/healthy sample rates and alert totals
- `long_term_reliability.json` / `long_term_reliability.md`: uptime proxy, sync reliability, reconciliation reliability, order success/reject rates, incident/guardrail rates, and recovery success
- `alert_aging.csv`: unresolved alert ages and escalation state
- `unresolved_incidents.csv`: open incidents requiring review
- `operator_controls.csv`: persisted maintenance/degraded/submission-gate state
- `resume_readiness.csv` / `resume_readiness.md`: operator-facing resume-live eligibility and required actions
- `reports/operator/latest_checklist.md`: latest checklist with blockers and warnings
- `reports/operator/current_session.md`: current supervised session state or clean no-session notice
- `reports/operator/latest_authorization.md`: latest live-authorization decision and expiry
- `reports/operator/latest_handover.md`: latest handover package, blocked items, and next steps
- `reports/operator/continuity_summary.md`: carry-over blockers, warnings, and continuity state
- `reports/operator/outstanding_live_blockers.md`: explicit blockers still preventing broker-live submission
- `reports/operator/session_history.csv`: session open/close history
- `reports/operator/handover_history.csv`: handover creation, acceptance, and refusal history
- `reports/operator/pending_handovers.csv`: handovers that still require acceptance or remediation
- `reports/operator/carry_over_items.csv`: unresolved incidents, alerts, anomalies, and open exposures across handovers
- `reports/operator/open_risk_items.csv`: current carry-over blockers, manual actions, and continuity warnings
- `reports/operator/operator_actions.csv`: queryable operator action history
- `reports/operator/unresolved_handoffs.csv`: unresolved session-close carry-over items
- `reports/operator/operator_identities.csv`: configured local operator identities with role/team/shift metadata
- `reports/operator/active_operator_sessions.csv`: current and recent authenticated operator sessions
- `reports/operator/approval_history.csv`: signed approvals for sensitive actions with auth-session linkage
- `reports/operator/approval_signatures_by_action.csv`: grouped signature counts by sensitive action type
- `reports/operator/expired_approvals.csv`: approvals whose validity window has elapsed
- `reports/operator/denied_privileged_actions.csv`: denied privileged action attempts and reasons
- `reports/operator/reauth_events.csv`: re-auth required/completed audit trail
- `reports/operator/identity_audit_summary.md`: compact identity/auth/signature review summary
- `reports/audit_integrity/summary.md`: latest audit-chain verification status, suspicious records, seal history, and export history
- `reports/audit_evidence/<export_id>/manifest.json`: immutable-style audit evidence export manifest
- `reports/archives/summary.md`: retention, archive inventory, rotation, verification, and restore-for-review summary
- `reports/archives/pending_archival_candidates.csv`: records/files exceeding configured retention windows
- `reports/archives/archive_inventory.csv`: local archive packages, package hashes, record counts, file counts, and integrity status
- `archives/operational/<archive_id>.zip`: local archive package containing retained records, integrity metadata, reports/files, and manifest hashes
- `archives/restore_review/<archive_id>/`: non-destructive extraction area for archive review
- `reports/backup_recovery/summary.md`: backup inventory, coverage, verification, restore, and recovery-validation summary
- `reports/backup_recovery/backup_inventory.csv`: local backup packages, hashes, timestamps, scope, and verification state
- `reports/backup_recovery/backup_coverage.md`: what the latest backup scope includes and what is missing or at risk
- `reports/backup_recovery/recovery_validation_status.md`: post-restore continuity mode, blockers, warnings, and sensitive-action block status
- `backups/local/<backup_id>.zip`: local disaster-recovery backup package with SQLite state, integrity metadata, archive manifest inventory, config snapshot, manifest, and hashes
- `backups/restore_review/<backup_id>/`: non-destructive restore area for backup review
- `forex_scanner.prom`: Prometheus textfile-style metrics when `monitoring.metrics_export_enabled=true`
- `daily_operational_summary.csv` / `daily_operational_summary.md`: daily counts of metrics, alerts, and incidents
- `daily_execution.csv` and `per_symbol.csv`: supervised broker execution summaries
- `reports/alerts/summary.md`, `rule_evaluations.csv`, `active_alerts.csv`, `suppressed_alerts.csv`, `resolved_alerts.csv`, and `routing_failures.csv`: metric-driven alert rule outputs and delivery state
- `docs/dashboards/forex_scanner_ops_grafana.json`: Grafana-ready dashboard pack for the local Prometheus metrics

Export metrics manually:

```powershell
python scripts/metrics_export.py --check
python scripts/metrics_export.py --stdout --check
```

The exporter writes Prometheus-compatible textfile metrics to `reports/broker/forex_scanner.prom` by default. It is local-only and does not open a network listener. A node-exporter textfile collector or similar local agent can pick up the file later.

Core metric families include:

- `forex_scanner_execution_mode`
- `forex_scanner_broker_connected`
- `forex_scanner_broker_connectivity_failures_total`
- `forex_scanner_account_sync_fresh`
- `forex_scanner_position_sync_fresh`
- `forex_scanner_reconciliation_fresh`
- `forex_scanner_reconciliation_anomalies_active`
- `forex_scanner_operational_alerts_active`
- `forex_scanner_operational_incidents_active`
- `forex_scanner_broker_rejects_total`
- `forex_scanner_broker_retry_attempts_total`
- `forex_scanner_broker_retries_exhausted_total`
- `forex_scanner_stale_state_detections_total`
- `forex_scanner_live_guardrail_triggers_total`
- `forex_scanner_manual_intervention_required`
- `forex_scanner_kill_switch_active`
- `forex_scanner_operator_degraded_mode`
- `forex_scanner_live_submission_attempts_total`
- `forex_scanner_live_submission_failures_total`
- `forex_scanner_recovery_actions_total`
- `forex_scanner_last_successful_broker_action_timestamp_seconds`
- `forex_scanner_last_failed_broker_action_timestamp_seconds`

Labels are intentionally low cardinality: `execution_mode`, `broker_adapter`, `severity`, `category`, and `state`.

Dashboard and alert routing notes:

- Import `docs/dashboards/forex_scanner_ops_grafana.json` into Grafana with a Prometheus datasource named by `${DS_PROMETHEUS}`.
- Alert rules are evaluated locally by `scripts/alert_rules.py`; they are not broker actions and do not submit orders.
- Local alert routing appends structured records to `reports/alerts/alert_events.jsonl`.
- Optional webhook routing is off by default and fails safely when the endpoint env var is missing.
- Default webhook env var: `FOREX_SCANNER_ALERT_WEBHOOK_URL`.

Soak report outputs live under `reports/soak/<run_id>/`:

- `summary.md` / `summary.json`: run metadata and headline reliability
- `readiness.md` / `readiness.json`: `pass`, `pass_with_warnings`, or `fail` with reasons
- `reliability.md` / `reliability.json`: connectivity, account/position sync, reconciliation, degraded-time, retry, reject, stale-state, health-flap, and unresolved-incident metrics
- `samples.csv`: every polling sample collected during the run
- `anomalies.csv` / `anomalies.md`: repeated flaps, stale state, disconnects, retry exhaustion, manual intervention, unresolved alerts/incidents, and degraded periods
- `health_timeline.csv` and `reconciliation_timeline.csv`: operator timeline views
- `degraded_periods.csv` and `unresolved_issues.csv`: focused review queues

Campaign report outputs live under `reports/soak_campaigns/<campaign_id>/`:

- `campaign_summary.md` / `campaign_summary.json`: campaign metadata, target vs observed duration, runs, samples, and headline reliability
- `weekly_reliability.md` / `weekly_reliability.json`: weekly-style health, sync, reconciliation, retry/reject, recovery, alert, and incident metrics
- `readiness.md` / `readiness.json`: `not_ready`, `limited_ready`, or `supervised_ready` with reasons, blockers, warnings, next actions, and suggested rerun duration
- `recurring_anomalies.csv` / `recurring_anomalies.md`: recurring, worsening, or clustered operational failure modes
- `campaign_timeline.csv`: sample timeline across all attached sessions
- `restart_recovery_events.csv`: recovery/check samples during the campaign
- `alert_incident_burden.csv`: alert and incident counts by severity/category
- `unresolved_issues.csv`: active alerts, open incidents, soak anomalies, and recurring issues
- `operator_notes.md`: placeholder for manual operator observations

Operator runbooks live in `docs/runbooks/broker_operations.md`. The operational model is summarized in `docs/operations.md`.

## Data Providers And Limitations

The provider layer is swappable:

- `auto`: tries MT5 first, Yahoo second, then deterministic synthetic only if development/test fallback is allowed
- `mt5`: primary real-data path when the local terminal and Python package are installed
- `yahoo`: fallback real historical FX candles through `yfinance`
- `synthetic`: deterministic development/test candles for local demos and tests

Important limitations:

- Synthetic data is blocked in `production` environment unless `allow_synthetic_in_production` is explicitly true.
- Yahoo Finance intraday depth and availability vary by interval and symbol.
- The deterministic fallback is not broker data. It exists so the app, tests, UI, and backtester work locally when real intraday data is unavailable.
- MT5 requires a configured local terminal; it is not installed by `requirements.txt`.
- Missing MT5 or `yfinance` support is reported as a provider error; `auto` mode can fall back to deterministic synthetic candles only when fallback is enabled and the environment permits it.
- Data-quality warnings are diagnostics, not a substitute for broker data validation.
- Synthetic/demo mode does not force trade ideas. It may return no-trade rows when the setup, score, or risk/reward gates are not met.
- Rejected demo rows may still show nonzero pre-gate scores. That means technical conditions had partial quality but failed one or more hard approval gates.
- Synthetic/demo mode includes deterministic scenario shapes so the UI is illustrative:
  - EUR/USD: trend-continuation pullback context, which is expected to produce an occasional valid long setup under default day-trading settings
  - GBP/USD: breakout pressure context, which may still fail score or risk gates
  - USD/CHF: ranging market context for range/no-trade behavior
- Backtests evaluate completed candle closes. If SL and TP are both touched within one candle, V1 assumes SL first.
- Transaction costs are modeled as fixed round-trip pips from settings, not live spread/slippage.
- Paper trading is a local simulation with deterministic spread/slippage assumptions. It is useful for workflow, portfolio discipline, and calibration rehearsal, not broker-grade execution validation.
- Broker sandbox/live mode is not an unattended auto-trading system. It requires explicit configuration, preflight validation, journaled events, operator review, and clean recovery validation after any restore.
- V1 is technical-analysis only and intentionally excludes news, fundamentals, positioning, and order-book data.

## Production Readiness

Ready for V1 local research/demo use:

- deterministic local demo mode
- modular provider abstraction
- scanner and backtester integration tests
- SQLite persistence that can be recreated locally
- explicit no-trade and provider-fallback messaging
- clearer live-market observability through detected/watchlist/approved statuses

Not production-ready yet:

- not broker-certified data
- no unattended live order execution
- broker live mode exists only as a gated, supervised integration path; it is disabled by default and not a production operations stack
- no bid/ask intrabar execution simulator
- paper portfolio guardrails are still coarse approximations; they now include net/gross currency and correlated-symbol limits, but they are not broker margin, margin-call, fill-quality, or full correlation-risk controls
- no walk-forward/out-of-sample validation report

## V2 Roadmap

- Broker-quality historical data adapters with cached local parquet storage
- Walk-forward parameter validation and out-of-sample reports
- More detailed execution simulation using bid/ask candles where available
- Broker adapters behind the execution interface after paper validation
- Correlated-pair clustering and margin-aware portfolio controls
- Richer support/resistance clustering and market-session filters
- Exportable backtest reports
- Alerting for scanner results without order execution
