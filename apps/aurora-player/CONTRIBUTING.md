# Contribution

## Commits

Le projet suit Conventional Commits:

```text
feat: add indexeddb media repository
fix: handle drive token expiration
test: cover repeat-one playback
docs: describe aurora architecture
chore: configure eslint
```

Types acceptes:

- `feat`
- `fix`
- `docs`
- `test`
- `refactor`
- `chore`
- `ci`

## Qualite Avant Commit

Executer:

```powershell
npm run check
```

La commande regroupe typecheck, lint, format check, tests unitaires et build.

## Regles De Code

- TypeScript strict obligatoire.
- Pas de `any`.
- Pas de logique metier critique dans les composants React.
- Toute logique de queue, sync, persistence ou parsing doit etre testable hors UI.
- `localStorage` est reserve aux preferences legeres.
- Les operations de persistence critiques doivent passer par des repositories.
