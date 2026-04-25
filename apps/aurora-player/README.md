# Aurora Player

Aurora Player is the React/TypeScript web application in this repository. It is a browser multimedia-player foundation with separated domain, application, infrastructure, and UI layers.

## Purpose

The app is intended to become a premium local/web media player foundation covering playback, playlists, podcasts, Google Drive integration, IndexedDB persistence, and deferred sync. The current state is a foundation, not a finished consumer product.

## Entry Points

- React app entry: `src/main.tsx`
- Root component: `src/App.tsx`
- Domain types and contracts: `src/domain/`
- Application services: `src/application/`
- Infrastructure adapters: `src/infrastructure/`
- UI features/components: `src/features/`, `src/components/`
- Unit tests: `src/**/*.test.ts` and `src/**/*.test.tsx`
- E2E tests: `tests/e2e/`

## Install

Node 22+ and npm 10+ are expected.

```powershell
cd apps/aurora-player
npm install
```

## Run

```powershell
cd apps/aurora-player
npm run dev
```

Vite prints the local browser URL.

## Test

```powershell
cd apps/aurora-player
npm run test
npm run typecheck
npm run lint
npm run build
```

For Playwright:

```powershell
cd apps/aurora-player
npx playwright install chromium
npm run build
npm run e2e
```

## Useful Scripts

- `npm run dev`: Vite dev server
- `npm run build`: TypeScript check plus production build
- `npm run test`: Vitest unit tests
- `npm run typecheck`: TypeScript checks
- `npm run lint`: ESLint
- `npm run format:check`: Prettier check
- `npm run check`: combined local quality gate

## Boundary

Aurora does not depend on the Python Forex scanner. Keep Aurora source, tests, configs, and docs inside `apps/aurora-player`.

Historical Aurora planning docs live in `docs/`.
