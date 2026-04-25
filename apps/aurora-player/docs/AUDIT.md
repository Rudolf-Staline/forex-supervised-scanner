# Audit Technique - Aurora Player

Note: this is a historical audit captured before the Aurora foundation was added and before the repository was split into `apps/forex-scanner` and `apps/aurora-player`.

Date: 2026-04-15

## Synthese

Le depot ne contient pas une base Aurora Player existante. Il contient actuellement une application Python/Streamlit nommee `Forex Technical-Analysis Scanner V1`, avec une architecture modulaire Python, SQLite, Plotly et Pytest. Aucun fichier React, Vite, TypeScript, Tailwind, Google Drive, moteur audio, podcast RSS, playlist multimedia ou IndexedDB n'est present.

La conclusion d'audit est donc double:

1. Le projet actuel est stable dans son perimetre Python: les tests existants passent (`42 passed`).
2. Il n'existe pas de base Aurora Player a refactoriser. La refonte doit demarrer par un socle web TypeScript isole, sans supprimer ni melanger l'application Python existante.

## Structure Observee

```text
app/
  backtest/
  config/
  core/
  data/
  indicators/
  market_regime/
  risk/
  scoring/
  setups/
  storage/
  ui/
  utils/
scripts/
tests/
README.md
pyproject.toml
streamlit_app.py
```

Cette structure correspond a un outil local de scan Forex:

- UI Streamlit dans `app/ui/streamlit_app.py`
- persistence SQLite dans `app/storage/database.py`
- fournisseurs de donnees Forex dans `app/data/providers.py`
- logique metier Python testee par Pytest

## Ecarts Majeurs Avec Aurora Player

| Domaine attendu | Etat observe | Risque |
| --- | --- | --- |
| React + Vite + TypeScript | Absent | Aucun socle frontend cible |
| TypeScript strict | Absent | Impossible de garantir les contraintes TS |
| Audio local/video/local queue | Absent | Fonction coeur inexistante |
| Google Drive | Absent | Pas d'auth, pas de retry, pas de sync state |
| Podcasts RSS | Absent | Pas de parsing/validation RSS |
| Playlists/favoris/historique multimedia | Absent | Donnees metier non modelisees |
| IndexedDB | Absent | Persistence web cible inexistante |
| Tailwind UI Aurora | Absent | Identite produit non presente |
| Vitest/Testing Library/Playwright | Absent | Tests web inexistants |

## Architecture Et Dette Technique De L'Existant

Points positifs:

- L'application Python separe deja plusieurs responsabilites: providers, scoring, risk, setup detection, storage, UI.
- Les modeles metier sont fortement types via Pydantic.
- La suite de tests couvre une bonne partie de la logique metier actuelle.
- Le fallback synthetique est explicitement bloque en production sauf opt-in.

Problemes pour Aurora Player:

- Le depot n'est pas structure autour d'une application web multimedia.
- Les modules Python n'ont aucune reutilisabilite directe pour React/TypeScript.
- Le README decrit un produit different du produit demande.
- Le point d'entree `streamlit_app.py` ne peut pas accueillir proprement une experience lecteur multimedia premium.
- La persistence SQLite locale ne repond pas au besoin browser/cross-device/IndexedDB.

## Risques Securite

Risques actuels pour Aurora Player:

- Aucune politique de stockage de token Google Drive n'est definie.
- Aucun cloisonnement entre auth Google, API Drive, repository et UI.
- Aucune strategie de refresh/expiration/retry OAuth n'existe.
- Aucune validation de contenu RSS ou de metadonnees multimedia n'existe.
- Aucune politique de migration IndexedDB n'existe.

Risques de l'application Python existante:

- Les donnees de settings sont sauvegardees localement en JSON valide par Pydantic, ce qui est sain pour l'usage local actuel.
- Les providers externes peuvent echouer; le code expose deja des erreurs/fallbacks. Aucun enjeu direct Aurora n'a ete trouve dans le code Python.

## Risques Performance

Pour Aurora Player:

- Les longues listes de bibliotheque, fichiers Drive, episodes podcast et historique necessitent de la virtualisation.
- `localStorage` serait inadapte aux historiques volumineux, blobs de metadonnees et caches de flux.
- La lecture audio/video doit etre isolee de React pour eviter les re-renders parasites.
- Le parsing RSS et les synchronisations Drive doivent etre bornes, cancellables ou retryables.

Pour l'existant:

- Streamlit reconstruit souvent l'UI; ce modele ne convient pas a un lecteur premium reactif.
- SQLite local est coherent pour l'outil Forex, mais pas pour une application web navigateur.

## Risques Qualite Et Testabilite

Pour Aurora Player:

- Aucun test TypeScript, composant React, Playwright ou Vitest n'existe.
- Aucune interface de repository multimedia n'est disponible.
- Aucun moteur audio testable hors UI n'existe.
- Aucune convention de commit ni pipeline CI web n'existe.

Pour l'existant:

- Les tests Python passent.
- Pytest signale un warning d'ecriture de `.pytest_cache` sous OneDrive/Windows: l'environnement bloque la creation du cache, sans casser les tests.

## Decision D'Audit

Le chemin robuste est de creer un socle Aurora Player TypeScript dans une structure `src/` conforme a la cible, en laissant l'application Python intacte. Toute migration fonctionnelle Aurora doit ensuite se faire module par module:

1. socle qualite et architecture web;
2. modeles metier multimedia;
3. persistence IndexedDB migrable;
4. moteur audio decouple;
5. couche Google Drive isolee;
6. UI React/Tailwind progressive;
7. tests unitaires, integration et e2e.

## Verification Effectuee

- `python -m pytest`: 42 tests passes, 1 warning de cache Pytest lie a l'environnement Windows/OneDrive.
- `node`, `npm`, `pnpm`, `bun`: indisponibles sur la machine, donc les checks TypeScript/Vitest/Playwright ne peuvent pas etre executes localement avant installation de Node.
