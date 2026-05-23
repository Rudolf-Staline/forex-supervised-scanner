# Forex Supervisor

Forex Supervisor est une application locale d'aide à l'analyse Forex. Elle regroupe un scanner multi-timeframe, un scoring de setups, du paper trading, un bot demo, un journal, un backtest simplifié et des rapports d'audit locaux.

Le projet se lance depuis `apps/forex-scanner` et doit rester en mode `paper/demo`.

## Avertissement

- Forex Supervisor est un outil éducatif et de recherche.
- Le projet fonctionne en paper/demo uniquement par défaut.
- Il ne fournit pas de conseil financier.
- Aucun ordre réel n'est envoyé dans le mode actuel.
- Le trading Forex est risqué et peut entraîner des pertes importantes.
- Le broker live est désactivé et ne doit pas être utilisé pour cette version.
- Le mode live trading doit rester interdit: ne jamais activer `ALLOW_LIVE_TRADING=true` ni un broker live.

## Prérequis

Python 3.11 ou 3.12 est recommandé.

Ouvrir un terminal dans :

```text
apps/forex-scanner
```

## Installation Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Installation Linux / Mac

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Variables D'environnement Obligatoires

Ces variables verrouillent explicitement l'application en mode paper/demo. Elles doivent être définies avant de lancer l'application ou les scripts sensibles.

Windows PowerShell :

```powershell
$env:EXECUTION_MODE="paper"
$env:ALLOW_LIVE_TRADING="false"
$env:BROKER_MODE="paper"
$env:AUTO_BOT_ENABLED="false"
```

Linux / Mac :

```bash
export EXECUTION_MODE=paper
export ALLOW_LIVE_TRADING=false
export BROKER_MODE=paper
export AUTO_BOT_ENABLED=false
```

Valeurs attendues :

```text
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
BROKER_MODE=paper
AUTO_BOT_ENABLED=false
```

Pour tester un compte Deriv-Demo via MetaTrader 5, garder `EXECUTION_MODE=paper` et `ALLOW_LIVE_TRADING=false`, puis utiliser un fichier `.env` local non commité :

```text
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
BROKER_MODE=mt5_demo
AUTO_BOT_ENABLED=false
MT5_DEMO_ONLY=true
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=Deriv-Demo
MT5_PATH=
```

`MT5_PASSWORD` ne doit jamais être affiché ni commité. Le mode MT5 demo refuse de tourner si `MT5_DEMO_ONLY` n'est pas `true`.

## Initialisation

Créer ou mettre à jour la base SQLite locale :

```powershell
python scripts/init_db.py
```

Lancer le smoke test local :

```powershell
python scripts/smoke_check.py
```

Le smoke test vérifie la configuration, le scanner et un backtest minimal avec des données de démonstration déterministes.

## Lancement local securise

Ne jamais partager le fichier `.env`. Il peut contenir le login, le serveur et le mot de passe MT5 demo. Le fichier `.env.example` sert uniquement de modele sans identifiants reels.

Verifier la configuration locale sans envoyer d'ordre :

```powershell
python scripts/health_check.py
```

Tester le mode paper sans MT5 :

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/test_paper.ps1
```

Tester la connexion Deriv-Demo, sans ordre :

```powershell
python scripts/test_mt5_connection.py
powershell -ExecutionPolicy Bypass -File scripts/windows/test_mt5_connection.ps1
```

Tester les donnees MT5, sans ordre :

```powershell
python scripts/test_mt5_market_data.py
powershell -ExecutionPolicy Bypass -File scripts/windows/test_mt5_data.ps1
```

Tester un ordre demo minuscule, uniquement apres confirmation manuelle :

```powershell
python scripts/mt5_place_tiny_demo_order.py --symbol EURUSD
```

Le script demande exactement `DEMO_ORDER` avant d'appeler MT5. Il doit rester reserve aux tests demo Deriv-Demo.

Lancer un cycle unique avec donnees MT5 et broker paper :

```powershell
python scripts/run_one_cycle.py --provider mt5 --broker paper
```

Lancer un cycle unique avec donnees MT5 et broker MT5 demo explicite :

```powershell
python scripts/run_one_cycle.py --provider mt5 --broker mt5_demo
powershell -ExecutionPolicy Bypass -File scripts/windows/run_one_cycle_mt5_demo.ps1
```

Lancer le bot continu en MT5 demo explicite :

```powershell
python scripts/run_demo_bot.py --provider mt5 --broker mt5_demo
powershell -ExecutionPolicy Bypass -File scripts/windows/run_bot_mt5_demo.ps1
```

Arreter le bot continu avec `Ctrl+C`.

Les logs MT5 sont compacts par defaut. Pour diagnostiquer les donnees de marche :

```powershell
python scripts/run_one_cycle.py --provider mt5 --broker paper --debug-market-data
```

Le broker `paper` reste le choix par defaut. Le broker `mt5_demo` n'est utilise que si l'utilisateur le demande explicitement avec `--broker mt5_demo` et si les variables demo Deriv sont presentes.

## Lancement

```powershell
streamlit run streamlit_app.py
```

L'interface Streamlit affiche l'état du système :

- database OK ;
- data provider OK ou fallback ;
- paper mode actif ;
- bot demo stopped/running ;
- live trading disabled.

## Parcours De Démo

1. Ouvrir Streamlit avec `streamlit run streamlit_app.py`.
2. Aller dans `Scanner`.
3. Choisir un style : scalping, day trading ou swing trading.
4. Sélectionner une ou plusieurs paires Forex.
5. Cliquer sur `Lancer le scan`.
6. Aller dans `Opportunités`.
7. Lire le statut, le score, le régime de marché, le setup, le risk/reward, l'entry, le stop loss et les TP.
8. Envoyer une opportunité `approved` ou `premium` en paper trading.
9. Aller dans `Paper Trading` pour consulter les trades paper.
10. Aller dans `Bot Demo` et cliquer sur `Run one cycle`.
11. Consulter les logs et décisions du bot demo.
12. Aller dans `Journal` pour ajouter des notes, tags, émotion ou leçon.
13. Aller dans `Backtest` pour lancer un backtest simple.
14. Aller dans `Rapports / Audit` pour consulter les événements, exports et informations de sécurité.

## Tester Le Bot Demo

Depuis Streamlit :

1. Ouvrir l'onglet `Bot Demo`.
2. Vérifier que le statut est `STOPPED`.
3. Cliquer sur `Run one cycle` pour lancer un seul cycle paper/demo.
4. Lire les logs, décisions `ACCEPT` / `REJECT` et trades paper créés.

Depuis le terminal, lancer un seul cycle :

```powershell
python scripts/run_one_cycle.py --provider synthetic
```

Options utiles :

```powershell
python scripts/run_one_cycle.py --provider synthetic --style day_trading --symbols EUR/USD GBP/USD USD/CHF
python scripts/run_one_cycle.py --provider auto --style day_trading --symbols EUR/USD GBP/USD USD/CHF
```

Tester un cycle avec le broker paper par défaut :

```powershell
python scripts/run_one_cycle.py --provider synthetic --broker paper
```

Tester la connexion FTMO Free Trial / MT5 demo, sans passer d'ordre :

```powershell
python scripts/test_mt5_connection.py
```

Tester un cycle en mode MT5 demo explicite :

```powershell
python scripts/run_one_cycle.py --provider synthetic --broker mt5_demo
```

Lancer le bot local continu, uniquement après action explicite de l'utilisateur :

```powershell
python scripts/run_demo_bot.py --provider synthetic --broker paper
python scripts/run_demo_bot.py --provider synthetic --broker mt5_demo
```

Le script respecte `AUTO_BOT_INTERVAL_SECONDS` et s'arrête proprement avec `Ctrl+C`. Le provider `synthetic` évite les tentatives MT5/Yahoo pour une démo offline propre. Le provider `auto` reste disponible si vous voulez tester la chaîne MT5, Yahoo puis fallback synthétique.

Le broker `paper` reste le défaut. Le broker `mt5_demo` n'est utilisé que si l'utilisateur le demande explicitement avec `--broker mt5_demo`, si `.env` contient `BROKER_MODE=mt5_demo` et si `MT5_DEMO_ONLY=true`.

Tester la création d'un ordre paper avec une fixture contrôlée :

```powershell
python scripts/run_approved_fixture_cycle.py
```

Ce script affiche `TEST FIXTURE — données synthétiques — aucun marché réel`, utilise `ensure_demo_safe_mode()` et vérifie qu'un ordre paper est créé dans une base temporaire de test. Il ne doit jamais être utilisé comme trading réel.

Si `python scripts/run_one_cycle.py --provider synthetic` crée `0` trade, ce n'est pas forcément une erreur. Les garde-fous peuvent rejeter les signaux `rejected`, `detected`, `watchlist`, les scores insuffisants, le risk/reward insuffisant, les niveaux incomplets, les doublons, le cooldown ou les limites de positions.

## Onglets Disponibles

- `Scanner` : scan Forex multi-timeframe.
- `Opportunités` : setups classés et explications des statuts.
- `Paper Trading` : trades simulés, positions ouvertes/fermées et événements.
- `Bot Demo` : bot paper/demo, désactivé par défaut, lancé uniquement par action utilisateur.
- `Journal` : notes, tags, leçons, émotions et suivi des résultats paper.
- `Backtest` : simulation historique simplifiée.
- `Rapports / Audit` : sécurité, événements et exports locaux.

## Données Synthétiques De Démo

Le provider `synthetic` permet une démonstration locale reproductible sans broker externe. Ces données sont déterministes et servent uniquement à tester le scanner, le bot demo et le backtest.

Elles ne sont pas des données de marché réelles et ne doivent jamais être présentées comme telles.

Le provider `auto` reste disponible : en développement, si MT5 puis Yahoo sont indisponibles et si le fallback est autorisé, l'application peut utiliser les données synthétiques de démonstration. Yahoo et MT5 ne sont pas désactivés.

Scénario reproductible :

```powershell
python scripts/smoke_check.py --symbols EUR/USD GBP/USD USD/CHF
```

La sortie doit indiquer `deterministic_provider=synthetic`, un scan avec des opportunités diagnostiquées, puis `backtest=ok`.

## Tests

Lancer toute la suite :

```powershell
python -m pytest
```

Commandes de vérification rapides :

```powershell
python scripts/init_db.py
python scripts/smoke_check.py
python -m pytest
```

## Limites

- Le backtest est simplifié.
- Des données synthétiques peuvent être utilisées en démo.
- Les résultats passés ou simulés ne garantissent aucune performance future.
- Le broker live est désactivé.
- Aucun ordre réel ne doit être envoyé dans l'état actuel du projet.
- Toute future intégration broker devra nécessiter des garde-fous explicites, des tests de sécurité et une validation opérateur.
