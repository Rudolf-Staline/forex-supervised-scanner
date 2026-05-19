# Forex Supervisor

Forex Supervisor est une application locale d'aide à l'analyse Forex avec scanner multi-timeframe, scoring, paper trading, bot demo, journal de trading, backtest simplifié et audit/logs locaux.

Le projet se lance depuis `apps/forex-scanner` et reste centré sur une démonstration locale en mode paper/demo.

## Avertissement

- Forex Supervisor est un outil éducatif et de recherche.
- Ce projet ne fournit pas de conseil financier.
- Le mode par défaut est `paper/demo`.
- Aucun ordre réel n'est envoyé dans le mode actuel.
- Le trading Forex est risqué et peut entraîner des pertes importantes.
- Toute logique broker/live doit rester désactivée tant que des garde-fous explicites et supervisés ne sont pas activés.

## Fonctionnalités

- Scanner Forex multi-timeframe.
- Détection de régimes de marché.
- Détection de setups techniques.
- Scoring des opportunités.
- Calcul risk/reward avec entry, stop loss, TP1, TP2 et TP3.
- Opportunités classées par statut : `rejected`, `detected`, `watchlist`, `approved`, `premium`.
- Paper trading manuel depuis l'interface Streamlit.
- Bot demo automatique, désactivé par défaut et lancé uniquement par action utilisateur.
- Journal de trading pour annoter les trades paper/demo.
- Backtest simplifié accessible depuis Streamlit.
- Rapports, audit events et logs SQLite.
- Verrou de sécurité centralisé pour bloquer le live trading.

## Installation

Créer un environnement virtuel :

```powershell
python -m venv .venv
```

Activer l'environnement sur Windows PowerShell :

```powershell
.\.venv\Scripts\Activate.ps1
```

Activer l'environnement sur Linux ou macOS :

```bash
source .venv/bin/activate
```

Installer les dépendances :

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Python 3.11 ou 3.12 est recommandé pour éviter les incompatibilités de dépendances avec Python 3.13.

## Configuration Demo

Avant de lancer l'application ou les scripts sensibles, verrouiller explicitement le mode paper/demo.

Windows PowerShell :

```powershell
$env:EXECUTION_MODE="paper"
$env:ALLOW_LIVE_TRADING="false"
$env:BROKER_MODE="paper"
$env:AUTO_BOT_ENABLED="false"
```

Linux ou macOS :

```bash
export EXECUTION_MODE=paper
export ALLOW_LIVE_TRADING=false
export BROKER_MODE=paper
export AUTO_BOT_ENABLED=false
```

Configuration optionnelle du bot demo :

```powershell
$env:AUTO_BOT_INTERVAL_SECONDS="300"
$env:AUTO_BOT_MIN_SCORE="75"
$env:AUTO_BOT_ALLOWED_STATUSES="approved,premium"
$env:AUTO_BOT_MAX_OPEN_TRADES="3"
$env:AUTO_BOT_MAX_TRADES_PER_DAY="5"
$env:AUTO_BOT_COOLDOWN_MINUTES="30"
$env:AUTO_BOT_MIN_RR="1.5"
```

## Initialisation

Initialiser la base SQLite locale :

```powershell
python scripts/init_db.py
```

Vérifier rapidement le scanner, la configuration et un backtest minimal :

```powershell
python scripts/smoke_check.py
```

## Lancement

Lancer l'application Streamlit :

```powershell
streamlit run streamlit_app.py
```

L'application affiche un état système avec :

- database OK ;
- data provider OK ou fallback ;
- paper mode actif ;
- bot demo `STOPPED` ou `RUNNING` ;
- live trading disabled.

## Parcours Utilisateur

1. Lancer l'application avec `streamlit run streamlit_app.py`.
2. Ouvrir l'onglet `Scanner`.
3. Choisir un style : scalping, day trading ou swing trading.
4. Sélectionner une ou plusieurs paires Forex.
5. Cliquer sur `Lancer le scan`.
6. Ouvrir l'onglet `Opportunités`.
7. Lire l'explication d'une opportunité, son score, son régime de marché, son risk/reward et ses niveaux.
8. Si l'opportunité est `approved` ou `premium`, cliquer sur `Ajouter en paper trading`.
9. Ouvrir `Paper Trading` pour voir les positions ouvertes, les positions fermées, les notes et les événements.
10. Ouvrir `Bot Demo` et cliquer sur `Run one cycle` pour exécuter un cycle demo paper uniquement.
11. Ouvrir `Journal` pour ajouter tags, émotion, leçon et notes.
12. Ouvrir `Backtest` pour lancer un backtest simplifié sur une paire, un style et une période.
13. Ouvrir `Rapports / Audit` pour consulter le verrou paper/demo et exporter les rapports disponibles.

## Bot Demo

Le bot demo est désactivé par défaut.

Il ne démarre jamais automatiquement au lancement de Streamlit. L'utilisateur doit cliquer sur `Start Demo Bot` ou `Run one cycle`.

Le bot exécute uniquement des trades paper/demo. Il bloque les signaux `rejected`, `detected` et `watchlist`, ainsi que les signaux qui échouent aux garde-fous de score, risk/reward, data quality, stop loss, take profit, cooldown, limites journalières ou limites de positions ouvertes.

## Journal

Le journal permet de relire les trades paper/demo et d'ajouter :

- source : `manual` ou `demo_bot` ;
- résultat : open, win, loss ou breakeven ;
- PnL en R ;
- tags d'erreur ou de bonne exécution ;
- leçon ;
- émotion ;
- notes.

Tags disponibles :

- entrée trop tôt ;
- mauvais contexte ;
- stop mal placé ;
- bon setup ;
- signal faible ;
- non-respect du plan ;
- trade impulsif ;
- bonne patience.

## Backtest

Le backtest existant est accessible depuis Streamlit.

Il permet de choisir :

- paire ;
- style ;
- période ;
- score minimum ;
- capital initial fictif ;
- risque par trade fictif.

Les résultats affichent notamment :

- nombre de trades ;
- win rate ;
- profit factor ;
- expectancy ;
- max drawdown ;
- average R ;
- setup families les plus performantes ;
- meilleurs et pires trades simulés.

Le backtest est simplifié. Les résultats passés ne garantissent aucune performance future.

## Commandes De Test

Lancer toute la suite de tests :

```powershell
python -m pytest
```

Lancer le smoke test :

```powershell
python scripts/smoke_check.py
```

Commandes utiles supplémentaires :

```powershell
python scripts/journal_export.py --db data/forex_scanner.sqlite --out reports/journal
python scripts/paper_report.py --db data/forex_scanner.sqlite --out reports/paper
python scripts/calibration_report.py --db data/forex_scanner.sqlite --out reports/calibration
```

## Sécurité

Le verrou de sécurité central bloque le live trading si les variables et la configuration ne confirment pas explicitement le mode paper/demo.

Valeurs attendues pour la démonstration :

```text
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
BROKER_MODE=paper
AUTO_BOT_ENABLED=false
```

Dans l'état actuel du projet, aucun broker live ne doit être connecté et aucun ordre réel ne doit être envoyé.

## Roadmap

- Amélioration de la calibration.
- Meilleur dashboard Streamlit.
- Meilleure gestion portfolio paper/demo.
- Sandbox broker supervisé plus tard.
- Jamais de live trading sans garde-fous explicites, tests de sécurité et validation opérateur.
