# Decisions Techniques

## 2026-04-15 - Creer un socle Aurora separe du code Python existant

Contexte: le depot contient une application Forex Streamlit, pas Aurora Player.

Decision: ajouter Aurora Player dans un socle React/TypeScript sous `src/`, sans supprimer ni renommer l'application Python existante.

Justification:

- evite de casser un projet Python dont les tests passent;
- respecte la cible technique Aurora;
- permet une migration incrementalement testable;
- limite les changements destructeurs dans un depot sans commit initial.

## 2026-04-15 - IndexedDB comme persistence structuree

Decision: utiliser IndexedDB pour medias, playlists, favoris, historique, podcasts, Drive et sync queue. `localStorage` reste limite aux preferences legeres.

Justification:

- IndexedDB accepte des volumes et transactions que localStorage ne gere pas correctement;
- l'historique et les caches ne bloquent pas le main thread via serialisation massive;
- la strategie de version permet des migrations explicites.

## 2026-04-15 - Moteur audio hors React

Decision: separer `PlaybackQueue` et `AudioEngineService` des composants React.

Justification:

- la logique queue/shuffle/repeat/seek se teste sans DOM;
- le transport audio peut etre remplace par un fake en test;
- React reste responsable de l'affichage, pas de l'orchestration metier.

## 2026-04-15 - Google Drive encapsule

Decision: l'UI ne depend que d'un service applicatif/infrastructure Drive. Auth, HTTP, retries, mapping et sync state restent dans `src/infrastructure/googleDrive`.

Justification:

- evite la fuite de logique OAuth dans les pages;
- permet de tester expiration et retry;
- rend possible une future sync cross-device sans reecrire l'UI.
