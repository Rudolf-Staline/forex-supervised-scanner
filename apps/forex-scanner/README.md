# Forex Supervisor (paper/demo only)

[![CI](https://github.com/forex-supervised-scanner/forex-supervised-scanner/actions/workflows/tests.yml/badge.svg)](https://github.com/forex-supervised-scanner/forex-supervised-scanner/actions/workflows/tests.yml)

Scanner/bot Python orienté sécurité pour Forex, commodities et indices, avec provider `mt5`, broker `paper` et `mt5_demo`, watchlist `multi_asset_demo`, résolution de symboles MT5 et scan aware des sessions.

## ⚠️ Avertissement sécurité

- **Aucun live trading autorisé** dans ce projet.
- Garder `EXECUTION_MODE=paper` et `ALLOW_LIVE_TRADING=false`.
- Le mode live trading doit rester interdit : ne jamais activer `ALLOW_LIVE_TRADING=true` ni sélectionner un broker live.
- Ne jamais commiter de secrets broker/MT5.

## Setup local Windows (validation MT5 réelle)

```powershell
cd apps/forex-scanner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
copy .env.example .env
```

Pour validation MT5 locale (terminal Windows requis), utiliser vos scripts `run_one_cycle.py` / `run_demo_bot.py` en `--broker mt5_demo` si nécessaire.

## Setup Codex Cloud

```bash
cd apps/forex-scanner
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

Le mode cloud doit rester en `paper`, sans dépendre d'un terminal MT5 local.

## Variables de sécurité minimales

Voir `.env.example` (valeurs fictives/sûres) :

```env
EXECUTION_MODE=paper
BROKER_MODE=paper
ALLOW_LIVE_TRADING=false
MT5_DEMO_ONLY=true
AUTO_BOT_ENABLED=false
ALLOW_MULTI_ASSET_DEMO_TRADING=false
ENABLE_DEMO_EXECUTION=false
NOTIFICATIONS_ENABLED=false
```

## Commandes de test (cloud-safe)

```bash
python -m pytest tests/test_safety.py
python -m pytest tests/test_demo_bot.py
python -m pytest tests/test_multi_asset_safety.py
python -m pytest tests/test_market_sessions.py
python -m pytest tests/test_session_aware_scanning.py
python -m pytest tests/test_session_wait_mode.py
```

## Cloud limitations

- Codex Cloud **ne contrôle pas** le terminal MT5 Windows local.
- Les tests MT5 réels doivent être validés en local.
- Les tests cloud utilisent mocks, stubs ou skips.
- Ne jamais stocker d'identifiants broker dans le repo.

## Validation MT5 locale uniquement

Si MetaTrader5 Python package ou terminal MT5 n'est pas disponible, les tests marqués MT5 doivent être skip proprement avec :

`MT5 terminal is not available in cloud environment.`

Cela évite de casser la CI cloud tout en conservant les validations MT5 sur machine locale Windows.
