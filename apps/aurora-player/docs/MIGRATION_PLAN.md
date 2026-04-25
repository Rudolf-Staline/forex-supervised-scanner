# Plan De Refonte Aurora Player

## Phase 0 - Cadrage

- Conserver l'application Python existante sans suppression.
- Documenter le decalage entre le depot actuel et Aurora Player.
- Creer le socle TypeScript dans `src/`.

## Phase 1 - Socle Qualite

- Ajouter Vite, React, TypeScript strict.
- Configurer ESLint, Prettier, Vitest, Testing Library et Playwright.
- Ajouter les scripts `npm` et une CI GitHub Actions.
- Definir les conventions de commit Conventional Commits.

## Phase 2 - Domaine Et Persistence

- Modeliser medias, playback, playlists, favoris, historique, podcasts et sync.
- Creer les interfaces repository dans le domaine.
- Implementer IndexedDB avec migrations versionnees.
- Reserver localStorage aux preferences legeres.

## Phase 3 - Audio Engine

- Implementer une queue pure et testable.
- Implementer un service audio decouple de `HTMLAudioElement`.
- Tester queue, shuffle, repeat, seek et fin de piste.

## Phase 4 - Google Drive

- Isoler auth, client HTTP, repository et sync service.
- Gerer expiration token, retries, erreurs reseau et rate limit.
- Tester les transitions de sync et les scenarios d'erreur.

## Phase 5 - UI Aurora

- Construire shell, navigation et pages de base.
- Ajouter une liste virtualisee pour bibliotheques longues.
- Brancher progressivement les services reels.

## Phase 6 - Podcasts Et Synchronisation Differee

- Ajouter parsing RSS robuste.
- Nettoyer/valider les episodes.
- Persister et synchroniser les etats de lecture.

## Phase 7 - Durcissement

- Ajouter tests d'integration repositories.
- Ajouter tests Playwright de parcours critique.
- Completer documentation technique et dettes restantes.

## Compromis Actuels

- Node/npm ne sont pas installes sur la machine d'audit. Le socle est prepare, mais les checks web devront etre executes apres installation de Node.
- Aucun code Aurora preexistant n'a ete trouve; la migration localeStorage vers IndexedDB est donc implementee comme architecture cible et API initiale, pas comme transformation de fichiers existants.
