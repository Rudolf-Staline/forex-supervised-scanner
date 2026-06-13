from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_min_score_policy_report_cli_smoke_exports_reports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        "scripts/min_score_policy_report.py",
        "--symbols",
        "EUR/USD",
        "--style",
        "day_trading",
        "--reports-dir",
        str(tmp_path),
        "--export-json",
        "--export-txt",
    ]
    env = {
        "EXECUTION_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "BROKER_MODE": "paper",
        "AUTO_BOT_ENABLED": "false",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    result = subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=True, timeout=120, env=env)

    assert "Min Score Policy Report" in result.stdout
    assert "symbol=EUR/USD" in result.stdout

    json_path = tmp_path / "min_score_policy_report.json"
    txt_path = tmp_path / "min_score_policy_report.txt"
    assert json_path.exists()
    assert txt_path.exists()

    policies = json.loads(json_path.read_text(encoding="utf-8"))
    assert policies
    entry = policies[0]
    for field in (
        "instrument_min_score",
        "adaptive_enabled",
        "adaptive_mode",
        "adaptive_base_min_score",
        "adaptive_recommended_min_score",
        "adaptive_effective_min_score",
        "demo_bot_min_score",
        "effective_scanner_threshold",
        "effective_bot_threshold",
        "threshold_source",
        "mismatch_warnings",
    ):
        assert field in entry

    text = txt_path.read_text(encoding="utf-8").lower()
    assert "password" not in text
    assert ".env" not in text
