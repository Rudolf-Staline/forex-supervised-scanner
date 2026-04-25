# Hybrid Apps Workspace

This repository intentionally contains two separate products. They share a Git repository, but they do not share runtime dependencies, build systems, or application code.

## Repository Layout

```text
apps/
  forex-scanner/    Python + Streamlit Forex technical-analysis scanner
  aurora-player/   React + TypeScript Aurora Player web application
.github/
  workflows/       CI definitions for both products
```

## Products

### Forex Scanner

Purpose: local decision-support tool for rules-based Forex technical scanning, backtesting, persistence, and calibration reporting.

Entry points:

- App: `apps/forex-scanner/streamlit_app.py`
- Python package: `apps/forex-scanner/app/`
- Tests: `apps/forex-scanner/tests/`
- Scripts: `apps/forex-scanner/scripts/`

Common commands:

```powershell
cd apps/forex-scanner
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
python -m pytest
python scripts/smoke_check.py
```

See [apps/forex-scanner/README.md](apps/forex-scanner/README.md).

### Aurora Player

Purpose: React/TypeScript foundation for a browser-based multimedia player with domain, application, infrastructure, and UI layers.

Entry points:

- App: `apps/aurora-player/src/main.tsx`
- Vite config: `apps/aurora-player/vite.config.ts`
- Unit tests: `apps/aurora-player/src/**/*.test.ts(x)`
- E2E tests: `apps/aurora-player/tests/e2e/`

Common commands:

```powershell
cd apps/aurora-player
npm install
npm run dev
npm run test
npm run build
```

See [apps/aurora-player/README.md](apps/aurora-player/README.md).

## Dependency Boundaries

- Python dependencies live in `apps/forex-scanner/pyproject.toml` and `apps/forex-scanner/requirements.txt`.
- Web dependencies live in `apps/aurora-player/package.json`.
- Do not import Forex Python modules into Aurora.
- Do not place React/Vite source in the Forex scanner app.
- Product-specific scripts stay inside the owning app directory.

## Architecture Ownership

The Forex scanner is the more complete V1 product: it has a working Streamlit UI, scanner pipeline, backtester, SQLite persistence, smoke check, and pytest suite.

Aurora Player is a web-app foundation: it has strict TypeScript structure, tests, and initial UI/application/infrastructure layers, but it is still experimental relative to the Forex scanner.

Future work should be directed inside the owning app unless it is truly repository-level work such as CI, root documentation, or shared governance.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the repository-level architecture note.
