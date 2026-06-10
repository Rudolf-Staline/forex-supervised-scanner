# Autonomous Pipeline

The full safe autonomy pipeline is:

`Evidence Builder -> Readiness Gate -> Recovery Planner -> Autonomous Supervisor -> Audit Reports`

## Commands

- **Full diagnostic mode:**
  `python scripts/run_autonomous_supervisor.py --once --symbols EUR/USD --dry-run --build-evidence-first --evidence-mode read-only --readiness-only --plan-recovery-on-block --export-json --export-txt`
- **Bounded paper/demo mode:**
  `python scripts/run_autonomous_supervisor.py --enabled --no-dry-run --once --symbols EUR/USD`

## Design constraints

- **Impossible by design:** Broker-live execution, direct bypassing of readiness by the recovery planner, infinite foreground loops, hidden daemon execution.
- **Why this does not authorize live trading:** Execution mode is strictly locked to `paper` and `ALLOW_LIVE_TRADING` is enforced as `false`. `order_send` is never called.
