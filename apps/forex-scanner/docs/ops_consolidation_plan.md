# Ops consolidation plan — supervisors & command center

This document maps the overlap between the three operator/automation entry
points and defines a **low-risk** consolidation plan. It is the prerequisite,
per the project guardrails, before any merge is implemented.

> Safety is non-negotiable. Any change here must preserve `EXECUTION_MODE=paper`,
> `ALLOW_LIVE_TRADING=false`, the autonomous policy, `ensure_demo_bot_safe_mode`,
> and the readiness gate. No `order_send`, no daemon, no `.env` mutation.

## 1. The three components and their real relationship

Contrary to the impression of "three overlapping supervisors", these are not
redundant copies — they form a **layered stack**, each delegating downward:

| Layer | Module | Service | Role |
| --- | --- | --- | --- |
| L1 kernel | `app/execution/autonomous_supervisor.py` | `AutonomousSupervisorService` | Bounded paper/demo decision loop; delegates executable decisions to `DemoBotService` only after `ensure_demo_bot_safe_mode`. |
| L2 readiness | `app/execution/realtime_paper_supervisor.py` | `RealtimePaperSupervisorService` | Wraps L1 + readiness + evidence + recovery + data-health + position manager into a realtime readiness run. Imports `AutonomousSupervisorService`. |
| L3 orchestration | `app/execution/realtime_command_center.py` | `RealtimeCommandCenterService` | Operator-facing orchestration of the whole stack (readiness, evidence, recovery, scenarios, data health, positions, L2 supervisor). Imports `RealtimePaperSupervisorService`. |

CLI entry points (all documented, all must be preserved):

- `scripts/run_autonomous_supervisor.py` → L1
- `scripts/realtime_paper_supervisor.py` → L2
- `scripts/realtime_command_center.py` → L3

Because the dependency direction is strictly L3 → L2 → L1 (no cycles), the stack
is already reasonably cohesive. The duplication that exists is in **boilerplate
helpers**, not in the orchestration logic.

## 2. Genuine overlaps (the actual duplication)

1. **Config field validators.** Each `*Config` re-implements `normalize_symbols`
   and `normalize_timeframe` field validators with identical bodies.
2. **Environment parsing helpers.** `_env_bool` / `_env_int` / `_env_float`
   appear in L1 and L2 with *slightly different signatures* (L1 takes a
   `default`; L2's `_env_bool` does not). This signature drift is itself a smell.
3. **Symbol resolution.** `AutonomousSupervisorConfig.resolved_symbols`,
   `symbols_from_args` (L2), and `command_center_symbols_from_args` (L3) all turn
   `(symbols, watchlist)` into an upper-cased symbol list. L3 already delegates
   to L2; L1 and L2 still diverge slightly (L1 defaults to a watchlist, L2
   defaults to `["EUR/USD"]`).
4. **Report export boilerplate.** Six near-identical `export_*_json` /
   `export_*_txt` functions each `mkdir(parents=True)` and write into a
   `reports/` dir with a module-default filename.
5. **Safety-flag snapshots.** `_safety_flags` (L1) and `realtime_safety_flags`
   (L2) build overlapping dicts of the same `AppSettings` safety fields.

## 3. Consolidation principles

- **Preserve every public symbol and CLI surface.** `symbols_from_args`,
  `command_center_symbols_from_args`, `realtime_safety_flags`,
  `export_*_json/txt`, and all three scripts must keep working unchanged. Where a
  body moves to a shared module, the original name stays as a thin
  re-export/delegation shim.
- **Do not collapse the layers.** Merging L1/L2/L3 into one service would destroy
  three documented CLI surfaces and the clean delegation boundary; it is
  explicitly **out of scope** as high-risk.
- **Additive first.** Introduce a shared helper module and have existing call
  sites delegate to it; never change a public signature.

## 4. Phased plan (lowest risk first)

**Phase A — shared helpers (low risk, additive). _Implemented in this pass for
L2/L3 symbol resolution._**
Create `app/execution/supervisor_common.py` housing the shared, pure helper:

- `resolve_supervisor_symbols(symbols, watchlist, *, default)` — one
  implementation that **exactly** replicates the current L2 logic
  (explicit symbols first, then watchlist as-is, then a per-call default).

`symbols_from_args` (L2) becomes a one-line delegation to it; L3 already
delegates to `symbols_from_args`, so it picks up the shared helper transitively.
**L1's `resolved_symbols` is intentionally left untouched** because it uses a
*different* precedence (watchlist first) and upper-cases watchlist symbols;
unifying that precedence is a behaviour change and is deferred to Phase B.
Future Phase-A additions: `supervisor_env_bool/int/float`,
`write_report_json/txt`, `safety_flag_snapshot`.

Risk: minimal — a single pure function with byte-identical behaviour to today,
covered by existing tests plus a new focused unit test.

**Phase B — config validator mixin (low/medium risk).**
Extract the duplicated `normalize_symbols` / `normalize_timeframe` validators
into a shared Pydantic mixin or module-level validator functions reused by all
three `*Config` models. Requires re-running the full config/supervisor test
suites.

**Phase C — export boilerplate (low/medium risk).**
Replace the six `export_*` bodies with calls to shared
`write_report_json(payload, reports_dir, filename)` /
`write_report_txt(text, reports_dir, filename)` helpers, keeping the public
function names as shims.

**Phase D — safety-flag snapshot (medium risk — safety-critical).**
Unify `_safety_flags` and `realtime_safety_flags` onto a single
`safety_flag_snapshot(settings)` only after asserting byte-for-byte equality of
the emitted dicts in tests, because these flags feed the readiness gate.

## 5. What is implemented now vs deferred

- **Implemented now (Phase A, L2/L3 symbol resolution only):** the additive
  `supervisor_common.resolve_supervisor_symbols` helper. L2's `symbols_from_args`
  delegates to it and L3 inherits it transitively. L1 is left as-is (different
  precedence). Zero public-API change, byte-identical behaviour, full test suite
  green.
- **Deferred (Phases B–D):** carry non-trivial regression risk on
  safety-critical paths and are scheduled as separate, individually-reviewed
  changes. They are intentionally **not** bundled here, honouring the
  "merge only if low-risk" guardrail.
- **Rejected:** collapsing L1/L2/L3 into a single service. It would break three
  documented CLI commands and the readiness/delegation boundary for no safety or
  correctness benefit.
