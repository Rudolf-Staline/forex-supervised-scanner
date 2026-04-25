# Architecture Cible - Aurora Player

## Objectif

Aurora Player doit etre une application web multimedia premium, testable hors UI, avec une separation stricte entre domaine, services applicatifs, infrastructure et composants React.

## Arborescence Cible

```text
src/
  domain/
    common/
    media/
    playback/
    playlists/
    favorites/
    history/
    podcasts/
    sync/
    repositories.ts
  application/
    audio/
    library/
    playlists/
    podcasts/
    sync/
  infrastructure/
    googleDrive/
    persistence/
      indexedDb/
    preferences/
  features/
    drive/
    library/
    player/
    podcasts/
  components/
    layout/
    player/
    virtualized/
  shared/
  test/
```

## Justification Des Couches

### `src/domain`

Contient les types et invariants metier purs:

- medias audio/video/podcast;
- queue et etat de lecture;
- playlists, favoris, historique;
- etat de synchronisation differee;
- contrats de repositories.

Cette couche ne depend ni du navigateur, ni de React, ni de Google Drive, ni d'IndexedDB.

### `src/application`

Contient les cas d'usage:

- moteur audio decouple de l'UI;
- manipulation de queue, shuffle, repeat, seek et fin de piste;
- orchestration playlist/favori/historique;
- synchronisation differee;
- parsing et normalisation podcast.

Les services applicatifs dependent d'interfaces de repositories, jamais d'implementations concretes.

### `src/infrastructure`

Contient les adaptateurs externes:

- IndexedDB pour persistence locale structuree;
- localStorage limite aux preferences legeres;
- Google Drive auth/API/repository/sync;
- clients HTTP et strategies de retry.

Chaque adaptateur expose une API testable et typisee. Les erreurs externes sont converties en erreurs domaine ou infrastructure explicites.

### `src/features`

Regroupe les experiences produit par domaine fonctionnel:

- bibliotheque;
- lecteur;
- Google Drive;
- podcasts.

Les pages React composent des services, hooks et composants, mais n'embarquent pas de logique metier critique.

### `src/components`

Composants UI transverses:

- shell Aurora;
- lecteur;
- listes virtualisees;
- controles accessibles.

Les composants complexes recoivent des props typiees et restent decouples des repositories.

### `src/shared`

Utilitaires transverses sans dependance produit lourde:

- `Result`;
- horloge;
- helpers de validation;
- formatage pur.

### `src/test`

Fakes et helpers de test partageables:

- faux transport audio;
- faux repositories;
- setup Testing Library/Vitest.

## Persistence Locale

IndexedDB est la source locale pour:

- bibliotheque media;
- playlists;
- favoris;
- historique;
- podcasts;
- fichiers Drive indexes;
- file de synchronisation differee.

`localStorage` est reserve a des preferences legeres:

- theme;
- volume;
- dernier onglet ouvert;
- densite d'affichage.

## Strategie De Migration IndexedDB

La base `aurora-player` possede une version explicite. Chaque montee de schema est une fonction de migration:

```text
version 1
  metadata
  media
  playlists
  favorites
  history
  podcastFeeds
  podcastEpisodes
  driveFiles
  syncQueue
```

Regles:

- une migration ne supprime jamais de donnees sans sauvegarde explicite;
- chaque store a une cle primaire stable;
- les index sont crees uniquement pour les requetes necessaires;
- les donnees derivees ne sont pas dupliquees si elles peuvent etre reconstruites;
- les operations repository critiques utilisent une transaction atomique.

## Google Drive

La couche Drive est separee en quatre parties:

1. `auth`: fournit/rafraichit un access token.
2. `http`: execute les requetes Drive, retry les erreurs transitoires, convertit les erreurs.
3. `repository`: transforme les fichiers Drive en enregistrements persistables.
4. `syncService`: gere l'etat de synchronisation, les echecs et la reprise.

L'UI ne connait jamais les endpoints Drive.

## Audio Engine

Le moteur audio est compose de:

- `PlaybackQueue`: logique pure de queue, shuffle, repeat, next/previous/end-of-track;
- `AudioEngineService`: orchestration entre queue, resolver de source et transport audio;
- `AudioTransport`: abstraction de `HTMLAudioElement`.

Les tests unitaires ciblent `PlaybackQueue` et `AudioEngineService` sans navigateur audio reel.

## Playlists, Favoris, Historique

Les repositories dedies garantissent:

- operations atomiques;
- IDs stables;
- absence de duplication inutile des medias;
- enregistrement d'operations de sync differee quand necessaire.

Les playlists stockent des references de medias par ID, pas des copies completes.

## Podcasts

Les podcasts sont separes en:

- parsing RSS;
- validation et nettoyage des champs;
- repository de feeds/episodes;
- etat de lecture episode par episode.

Les flux invalides retournent des erreurs explicites au lieu de polluer la bibliotheque.

## UI Aurora

Principes:

- dark theme premium par defaut;
- navigation clavier visible;
- layout desktop-first responsive;
- composants de liste virtualises pour gros volumes;
- controles accessibles avec labels;
- pas de faux bouton: toute action visible doit faire quelque chose ou etre masquee/desactivee avec raison claire.

## Qualite

Commandes cible:

```powershell
npm run typecheck
npm run lint
npm run format:check
npm run test
npm run e2e
npm run check
```

La CI GitHub Actions execute les memes controles.
