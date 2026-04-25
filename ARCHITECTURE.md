# Repository Architecture

## Scope

This is a hybrid repository with two intentionally separate applications:

- `apps/forex-scanner`: Python/Streamlit Forex scanner and backtesting tool.
- `apps/aurora-player`: React/TypeScript Aurora Player web foundation.

The repository is not a shared runtime platform. It is a workspace that keeps two products under one Git history while preserving separate dependencies and development workflows.

## Production Readiness

Forex Scanner is the primary mature product at this point. It is suitable for local research/demo use and includes:

- rules-based scanner pipeline;
- Streamlit UI;
- synthetic, Yahoo, and optional MT5 data providers;
- SQLite persistence;
- backtesting;
- calibration reporting;
- pytest and smoke validation.

Aurora Player is experimental/foundation-stage. It has a maintainable React/TypeScript architecture, tests, and web tooling, but it is not yet a full production media player.

## Boundaries

Product code must stay inside its owning app:

- Python modules, pytest tests, Streamlit entrypoints, SQLite scripts, and Forex docs belong in `apps/forex-scanner`.
- React components, TypeScript domain/application/infrastructure code, Vite/Vitest/Playwright configs, and Aurora docs belong in `apps/aurora-player`.
- Root files are reserved for repository-level concerns: workspace overview, CI, ignore rules, and architecture notes.

## Dependency Ownership

- Forex scanner uses Python packaging from `apps/forex-scanner/pyproject.toml`.
- Aurora Player uses Node/npm packaging from `apps/aurora-player/package.json`.
- Generated artifacts such as `.venv`, `node_modules`, `dist`, `coverage`, `.pytest_cache`, and SQLite runtime databases are not repository architecture and should not be committed.

## Future Work Direction

When adding product features, start from the relevant product README and work inside that app directory. Cross-product changes should be rare and limited to documentation, CI, or repository governance.
