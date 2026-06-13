# Forex Supervisor (paper/demo only)

[![CI](https://github.com/forex-supervised-scanner/forex-supervised-scanner/actions/workflows/tests.yml/badge.svg)](https://github.com/forex-supervised-scanner/forex-supervised-scanner/actions/workflows/tests.yml)

Scanner/bot Python orienté sécurité pour Forex, commodities et indices, avec provider `mt5`, broker `paper` et `mt5_demo`, watchlist `multi_asset_demo`, résolution de symboles MT5 et scan aware des sessions.

## ⚠️ Avertissement sécurité

- **Aucun live trading autorisé** dans ce projet.
- Garder `EXECUTION_MODE=paper` et `ALLOW_LIVE_TRADING=false`.
- Le mode live trading doit rester interdit : ne jamais activer `ALLOW_LIVE_TRADING=true` ni sélectionner un broker live.
- Ne jamais commiter de secrets broker/MT5.

## Setup local Windows (validation MT5 réelle)

```powershell
cd apps/forex-scanner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
copy .env.example .env
```

Pour validation MT5 locale (terminal Windows requis), utiliser vos scripts `run_one_cycle.py` / `run_demo_bot.py` en `--broker mt5_demo` si nécessaire.

## Setup Codex Cloud

```bash
cd apps/forex-scanner
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

Le mode cloud doit rester en `paper`, sans dépendre d'un terminal MT5 local.

## Variables de sécurité minimales

Voir `.env.example` (valeurs fictives/sûres) :

```env
EXECUTION_MODE=paper
BROKER_MODE=paper
ALLOW_LIVE_TRADING=false
MT5_DEMO_ONLY=true
AUTO_BOT_ENABLED=false
ALLOW_MULTI_ASSET_DEMO_TRADING=false
ENABLE_DEMO_EXECUTION=false
NOTIFICATIONS_ENABLED=false
```

## Commandes de test (cloud-safe)

```bash
python -m pytest tests/test_safety.py
python -m pytest tests/test_demo_bot.py
python -m pytest tests/test_multi_asset_safety.py
python -m pytest tests/test_market_sessions.py
python -m pytest tests/test_session_aware_scanning.py
python -m pytest tests/test_session_wait_mode.py
```

## Cloud limitations

- Codex Cloud **ne contrôle pas** le terminal MT5 Windows local.
- Les tests MT5 réels doivent être validés en local.
- Les tests cloud utilisent mocks, stubs ou skips.
- Ne jamais stocker d'identifiants broker dans le repo.

## Documentation Index

For a central map of the paper/demo operator stack, safety layers, realtime workflow, reporting tools, analytics, audits, and smoke commands, see [`docs/index.md`](docs/index.md).

## Local MT5 realtime validation runbook

Local Windows operators can validate MetaTrader 5 realtime market-data readiness without trading:

```bash
python scripts/local_mt5_realtime_validation.py --symbols EUR/USD GBP/USD --timeframes M1 M5 --duration-minutes 15 --interval-seconds 30 --export-json --export-txt
```

This command is local-only and Windows/MT5-dependent. It performs read-only checks for MT5 import, terminal/account/terminal-info reads, symbol resolution and selection, latest candles and ticks, timestamp-normalized candle age, spread, spread/ATR, missing or duplicate bars, provider-data latency (`provider_latency_ms`) separately from MT5 request latency (`latency_ms`), and bounded repeated polling. It validates market-data readiness only; it does not authorize live trading, does not call `order_send`, does not submit broker orders, does not mutate `.env`, does not run as a daemon, and has no infinite loop. CI uses mocks/stubs only and does not require real MT5; without `--strict`, missing MT5 writes a blocked report instead of failing the process.

Expected exports when enabled:

- `reports/local_mt5_realtime_validation.json`
- `reports/local_mt5_realtime_validation.txt`
- `reports/local_mt5_realtime_samples.csv`

See [`docs/local_mt5_realtime_validation.md`](docs/local_mt5_realtime_validation.md).

## Validation MT5 locale uniquement

Si MetaTrader5 Python package ou terminal MT5 n'est pas disponible, les tests marqués MT5 doivent être skip proprement avec :

`MT5 terminal is not available in cloud environment.`

Cela évite de casser la CI cloud tout en conservant les validations MT5 sur machine locale Windows.
## Autonomous Supervisor v0

Autonomous Supervisor v0 is a bounded foreground runner for paper/demo operation only. The canonical implementation is `app/execution/autonomous_supervisor.py`; `app/supervisor/autonomous.py` is only a compatibility re-export. It enforces `ensure_demo_bot_safe_mode(...)`, runs the existing demo bot for simulated paper orders, writes auditable reports, never starts a hidden daemon or unbounded loop, and never submits broker/MT5 orders. See [`docs/autonomous_supervisor.md`](docs/autonomous_supervisor.md).

Safe dry-run validation with synthetic data:

```bash
python scripts/run_autonomous_supervisor.py --provider synthetic --once --symbols EUR/USD GBP/USD --dry-run --export-json --export-txt
```

Bounded paper/demo mode remains paper-only and still does not authorize live trading:

```bash
python scripts/run_autonomous_supervisor.py --provider synthetic --enabled --no-dry-run --max-cycles 1 --interval-seconds 0 --symbols EUR/USD
```

## Autonomous Readiness Gate

The Autonomous Supervisor is protected by a read-only readiness gate before cycles start. The gate inspects paper/demo safety settings, operator maintenance/degraded controls, paper risk, and fresh local evidence reports. Missing or stale evidence blocks non-dry-run paper autonomy; dry-run diagnostics can receive `WARN_READY` under conservative defaults.

Readiness-only check:

```bash
python scripts/autonomous_readiness_report.py --export-json --export-txt
```

Supervisor readiness-only check:

```bash
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --readiness-only --export-readiness-json --export-readiness-txt
```

This remains paper/demo only and does not authorize live trading. See [`docs/autonomous_readiness_gate.md`](docs/autonomous_readiness_gate.md).

### Autonomous Evidence Builder

The paper/demo autonomy pipeline is now:

```text
Evidence Builder -> Readiness Gate -> Autonomous Supervisor -> Reports/Audit
```

Run the evidence builder before readiness checks when you need reproducible local readiness inputs:

```bash
python scripts/autonomous_evidence_builder.py --mode read-only --include-readiness --export-json --export-txt
```

Supported modes are `dry-run`, `read-only`, and `refresh`. The default is conservative read-only operation against local report artifacts. Generated evidence includes session health, data health, failure diagnostics, signal anomaly detection, and a static/no-MT5 symbol mapping audit. Missing required evidence blocks paper autonomy through the readiness gate; optional evidence produces warnings or skips.

This remains diagnostic-only. It does not authorize live trading, does not enable broker-live execution, does not require MT5 in CI, and does not call `order_send`.

## Autonomous Recovery Planner

The paper/demo autonomy pipeline now includes a recovery planning layer:

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> Autonomous Supervisor -> Audit Reports
```

The Recovery Planner explains blocked or degraded evidence/readiness/supervisor states and recommends safe next steps. It does not bypass readiness, enable live trading, mutate `.env`, or run broker-live actions.

Plan-only recovery report:

```bash
python scripts/autonomous_recovery_planner.py --export-json --export-txt
```

Generate recovery guidance when supervisor readiness blocks:

```bash
python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --build-evidence-first --evidence-mode read-only --readiness-only --plan-recovery-on-block --export-recovery-json --export-recovery-txt
```

See [`docs/autonomous_recovery_planner.md`](docs/autonomous_recovery_planner.md).

## Autonomous Policy Engine

The Autonomous Policy Engine centralizes autonomy permissions and safety decisions. It answers whether an action is allowed under the current mode, evidence, readiness, recovery, and operator state, returning `ALLOW`, `WARN_ALLOW`, or `DENY` with full rule results and safety flags. Every pipeline component consults the engine before proceeding.

Updated pipeline:

```text
Evidence Builder -> Readiness Gate -> Recovery Planner -> [Policy Engine] -> Autonomous Supervisor -> Audit Reports
```

Policy report:

```bash
python scripts/autonomous_policy_report.py --action run_supervisor --mode dry_run --export-json --export-txt
```

The policy engine enforces 11 safety invariants on every check. It does not enable live trading, does not call MT5, and does not submit orders. See [`docs/autonomous_policy_engine.md`](docs/autonomous_policy_engine.md).

### Autonomous Scenario Runner

The forex scanner includes an Autonomous Scenario Runner for cloud-safe end-to-end policy/readiness simulations. It uses synthetic local reports to validate how the Evidence Builder posture, Readiness Gate state, Recovery Planner, Policy Engine, and Autonomous Supervisor simulation behave together.

```bash
python scripts/autonomous_scenario_runner.py --list
python scripts/autonomous_scenario_runner.py --all --export-json --export-txt --strict
```

Exports are written to `reports/autonomous_scenario_suite.json` and `reports/autonomous_scenario_suite.txt` by default, or to a test/temp path with `--reports-dir`. The runner is paper/demo/read-only and does not require MT5, network access, broker credentials, `.env` mutation, daemon creation, live trading, broker-live execution, or order submission. See `docs/autonomous_scenario_runner.md` for details.

## Realtime paper/demo readiness layer

Run a one-shot realtime data health check before any realtime paper/demo session:

```bash
python scripts/realtime_data_check.py --provider mt5 --symbols EUR/USD GBP/USD --timeframe M1 --export-json --export-txt
```

Run the bounded foreground realtime paper supervisor in dry-run mode:

```bash
python scripts/realtime_paper_supervisor.py --provider mt5 --symbols EUR/USD --timeframe M1 --interval-seconds 60 --max-cycles 5 --dry-run --export-json --export-txt
```

Local MT5 validation requires a configured local MetaTrader 5 terminal. CI and cloud tests do not require MT5 and should use mocks or the synthetic CLI smoke path. Synthetic fallback is intentionally blocked for realtime paper mode, so an MT5 failure cannot be silently treated as live-quality market data.

Realtime paper/demo operation is not live trading: it validates real market data, safety gates, readiness, policy, and paper-only supervisor behavior without broker-live execution. This repository still does **not** authorize live trading, does not add broker-live execution, and the realtime paper layer does not call `order_send`.

### Realtime paper position manager

Local paper orders can be advanced through their realtime paper lifecycle without enabling live trading:

```bash
python scripts/realtime_paper_positions.py --provider synthetic --symbols EUR/USD --timeframe M1 --dry-run --export-json --export-txt
```

The manager updates pending/open local paper orders from fresh candles, records auditable activation/partial/stop/target/breakeven/cancel events, and exports `reports/realtime_paper_positions.json` plus `reports/realtime_paper_positions.txt`. Use `--dry-run` to preview lifecycle effects without persisting order changes. It remains strictly paper/demo: no broker-live execution, no `order_send`, no `.env` mutation, no daemon, and no MT5 requirement in CI.

The realtime paper supervisor can include lifecycle management after data health, safety heartbeat, evidence, readiness, and policy checks:

```bash
python scripts/realtime_paper_supervisor.py --provider synthetic --symbols EUR/USD --timeframe M1 --interval-seconds 0 --max-cycles 1 --dry-run --manage-positions --export-json --export-txt
```

Supervisor output includes a `position_lifecycle_summary` with position update, closure, and partial-exit counts when `--manage-positions` is enabled.

## Realtime Paper Command Center

Use the Realtime Paper Command Center as the single bounded paper/demo entrypoint for realtime operation checks:

```bash
cd apps/forex-scanner
python scripts/realtime_command_center.py --provider synthetic --symbols EUR/USD --timeframe M1 --dry-run --max-cycles 1 --export-json --export-txt
```

It coordinates safety heartbeat checks, realtime data health, evidence, readiness, policy, optional recovery planning, optional scenarios, realtime paper supervision, optional paper position management, and final reports:

- `reports/realtime_command_center_summary.json`
- `reports/realtime_command_center_report.txt`

Synthetic data is allowed for diagnostics but blocks realtime paper operation by design. The command center remains paper/demo only: no live trading, no broker-live execution, no `order_send`, no `.env` mutation, no daemon, no infinite loop, and no MT5 requirement in CI. See [`docs/realtime_command_center.md`](docs/realtime_command_center.md).

## Local Paper Operation Runbook

For the complete, step-by-step local operator workflow — chaining read-only MT5 realtime validation, the Realtime Paper Command Center, the paper supervisor, the paper position manager, and the runtime safety heartbeat into one procedure with a copy-paste checklist and report-interpretation guide — use the Local Paper Operation Runbook:

See [`docs/local_paper_operation_runbook.md`](docs/local_paper_operation_runbook.md).

Use it when you want a single human-readable procedure for running the project locally in safe paper/demo mode and interpreting the resulting reports. It remains paper/demo only: it does not enable or authorize live trading, keeps MT5 validation read-only, and is not a go-live approval.

## Operator Report Dashboard

Summarize the current paper/demo system state from existing report artifacts with the read-only operator dashboard:

```bash
python scripts/operator_dashboard.py --reports-dir reports --export-json --export-txt
python scripts/operator_dashboard.py --reports-dir reports --strict
```

It aggregates MT5 realtime validation, command center, realtime paper supervisor, position manager, runtime safety heartbeat, scenarios, and (when present) readiness/evidence/policy/recovery reports into one `final_operator_status`, with stale/missing report detection and recommended next actions. Exports go to `reports/operator_dashboard_summary.json` and `reports/operator_dashboard_report.txt`.

The dashboard is strictly read-only and works offline: no trading logic, no MT5, no `order_send`, no broker orders, no `.env` mutation, no daemon, and no MT5 requirement in CI. See [`docs/operator_dashboard.md`](docs/operator_dashboard.md).

## Paper Session Bundle Export

For an auditable paper/demo handoff, package the existing report artifacts into
a read-only archive:

```bash
python scripts/export_paper_session_bundle.py --reports-dir reports --output-dir reports/bundles --session-name paper-session-smoke
```

The exporter writes `reports/bundles/<session-name>.zip`,
`reports/bundles/<session-name>_manifest.json`, and
`reports/bundles/<session-name>_manifest.txt`. It only reads existing reports,
computes SHA-256 checksums, records missing required and optional reports
(unless `--no-include-optional` is used for a required-only bundle), and
propagates the operator dashboard status when `operator_dashboard_summary.json`
exists. It does **not** run trading logic, call MT5, call `order_send`, submit
broker orders, mutate `.env`, or authorize live trading. See
[`docs/paper_session_bundle.md`](docs/paper_session_bundle.md).

## Paper Performance Analytics

Paper Performance Analytics summarizes completed local paper/demo sessions from existing report artifacts only:

```bash
python scripts/paper_performance_report.py --reports-dir reports --export-json --export-txt
```

It reads local paper/demo reports and the existing local `paper_orders` store when present. It does not run strategies, does not call MT5, does not call `order_send`, does not submit broker orders, does not mutate `.env`, and does not authorize live trading. Outputs are diagnostic evidence only: `reports/paper_performance_summary.json` and `reports/paper_performance_report.txt`. See [`docs/paper_performance.md`](docs/paper_performance.md).

## Paper Session Review

Paper Session Review runs a post-session operator handoff by composing the existing offline dashboard, performance analytics, and optional bundle export:

```bash
python scripts/paper_session_review.py --reports-dir reports --export-json --export-txt --export-bundle --session-name paper-session-review
```

It writes `reports/paper_session_review_summary.json` and `reports/paper_session_review_report.txt`, refreshes the dashboard/performance artifacts when exports are enabled, and can create an auditable bundle under `reports/bundles/`. It remains read-only and paper/demo only: no trading logic, no MT5 import, no `order_send`, no broker orders, no `.env` mutation, no daemon, and no live-trading authorization. See [`docs/paper_session_review.md`](docs/paper_session_review.md).

## Paper Session History

The Paper Session History Ledger appends compact snapshots of completed paper/demo session reviews to a local JSONL ledger and aggregates them into JSON/TXT history reports:

```bash
python scripts/paper_session_history.py --reports-dir reports --append-latest --session-name paper-session-review --export-json --export-txt
```

It reads existing review/performance/dashboard artifacts, skips duplicate snapshots deterministically, and writes `reports/paper_session_history.jsonl`, `reports/paper_session_history_summary.json`, and `reports/paper_session_history_report.txt`. It is local paper/demo history only: no trading logic, no MT5 import, no `order_send`, no `.env` mutation, no daemon, and no live-trading authorization. See [`docs/paper_session_history.md`](docs/paper_session_history.md).

## Paper Session Trends

Paper Session Trends analyzes the existing paper/demo history ledger for multi-session insights without running strategies, Paper Session Review, Paper Session History, MT5, or broker order submission:

```bash
python scripts/paper_session_trends.py --reports-dir reports --window 10 --export-json --export-txt
```

It reads `reports/paper_session_history.jsonl` and writes `reports/paper_session_trends_summary.json` and `reports/paper_session_trends_report.txt` when exports are enabled. It computes status trends, recurring/new/resolved warnings and blocking reasons, win-rate/realized-R trends, aggregate paper metrics, symbol concentration, safety flag detections, and recommended next actions. Missing or empty history returns a safe `PAPER_SESSION_TRENDS_EMPTY` status in non-strict mode. See [`docs/paper_session_trends.md`](docs/paper_session_trends.md).

## Stale Issue Resolution Plan

Several older GitHub issues remain open even though their requested paper/demo features appear to have landed through later merged PRs. The stale issue resolution plan documents the implementation evidence and gives manual closure recommendations.

See [`docs/stale_issue_resolution_plan.md`](docs/stale_issue_resolution_plan.md).
