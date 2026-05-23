from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from threshold_optimizer_report import (  # noqa: E402
    ALERT,
    MAX_SPREAD_ATR_GRID,
    MIN_RISK_REWARD_GRID,
    MIN_SCORE_GRID,
    build_candidate_thresholds,
    build_threshold_scenarios,
    export_scenarios_csv,
    export_summary_json,
)


def test_threshold_grid_size() -> None:
    scenarios = build_threshold_scenarios(_rows(), _journal(), min_sample_size=30)
    assert len(scenarios) == len(_rows()) * len(MIN_SCORE_GRID) * len(MIN_RISK_REWARD_GRID) * len(MAX_SPREAD_ATR_GRID)


def test_candidate_thresholds_informational_only() -> None:
    scenarios = build_threshold_scenarios(_rows(), _journal(), min_sample_size=1)
    candidates = build_candidate_thresholds(scenarios)
    assert candidates["forex"]["status"] == "informational_only"


def test_exports(tmp_path: Path) -> None:
    scenarios = build_threshold_scenarios(_rows(), _journal(), min_sample_size=1)
    candidates = build_candidate_thresholds(scenarios)
    csv_path = tmp_path / "threshold_optimizer_report.csv"
    json_path = tmp_path / "threshold_optimizer_summary.json"
    export_scenarios_csv(scenarios, csv_path)
    export_summary_json(candidates, json_path)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["alert"] == ALERT


def test_script_handles_missing_inputs(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, "scripts/threshold_optimizer_report.py", "--asset-class", "all", "--watchlist", "multi_asset_demo"]
    proc = subprocess.run(cmd, cwd=project, capture_output=True, text=True, check=True)
    assert "threshold_optimizer_report=skipped" in proc.stdout
    assert "run scripts/backtest_multi_asset.py or scripts/run_one_cycle.py first" in proc.stdout


def _rows() -> list[dict]:
    return [
        {"asset_class": "forex", "symbol": "EUR/USD", "setup": "ema50_pullback", "session": "london", "total_trades_simulated": "20", "rejected_count": "20", "win_rate": "55", "expectancy_R": "0.25", "profit_factor": "1.6", "max_drawdown_R": "2.0", "average_R": "0.25"},
        {"asset_class": "commodities", "symbol": "XAU/USD", "setup": "ema50_pullback", "session": "new_york", "total_trades_simulated": "3", "rejected_count": "3", "win_rate": "33", "expectancy_R": "-0.1", "profit_factor": "0.8", "max_drawdown_R": "1.0", "average_R": "-0.1"},
    ]


def _journal() -> list[dict]:
    return [
        {"asset_class": "forex", "symbol": "EUR/USD", "setup": "ema50_pullback", "session": "london", "score": 72.0, "risk_reward": 1.8, "spread_atr": 0.21},
        {"asset_class": "commodities", "symbol": "XAU/USD", "setup": "ema50_pullback", "session": "new_york", "score": 70.0, "risk_reward": 1.8, "spread_atr": 0.45},
    ]
