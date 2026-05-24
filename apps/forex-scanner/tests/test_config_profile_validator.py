from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.config.profile_validator import validate_profile


BASE_ENV = {
    "EXECUTION_MODE": "paper",
    "BROKER_MODE": "paper",
    "ALLOW_LIVE_TRADING": "false",
    "MT5_DEMO_ONLY": "true",
    "ENABLE_DEMO_EXECUTION": "false",
    "AUTO_BOT_ENABLED": "false",
    "ALLOW_MULTI_ASSET_DEMO_TRADING": "false",
    "NOTIFICATIONS_ENABLED": "true",
    "MAX_DEMO_ORDER_VOLUME": "0.01",
    "MAX_DEMO_ORDERS_PER_DAY": "3",
}


def test_paper_safe_valid():
    report = validate_profile("paper_safe", BASE_ENV)
    assert report.status == "VALID"


def test_live_trading_true_is_dangerous():
    env = dict(BASE_ENV, ALLOW_LIVE_TRADING="true")
    report = validate_profile("paper_safe", env)
    assert report.status == "DANGEROUS"
    assert "ALLOW_LIVE_TRADING=true" in report.dangerous_flags


def test_demo_execution_true_is_blocked():
    env = dict(BASE_ENV, ENABLE_DEMO_EXECUTION="true")
    report = validate_profile("cloud_safe", env)
    assert report.status == "BLOCKED"


def test_missing_variables_detected():
    env = {"ALLOW_LIVE_TRADING": "false"}
    report = validate_profile("paper_safe", env)
    assert "BROKER_MODE" in report.variables_missing


def test_recommendations_generated():
    env = dict(BASE_ENV, BROKER_MODE="mt5")
    report = validate_profile("paper_safe", env)
    assert report.recommendations
    assert report.status in {"WARN", "BLOCKED", "DANGEROUS"}


def test_no_os_environ_mutation():
    before = dict(os.environ)
    validate_profile("paper_safe", BASE_ENV)
    after = dict(os.environ)
    assert before == after


def test_export_json_and_txt(tmp_path: Path):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "config_profile_validator.py"
    env = dict(os.environ)
    env.update(BASE_ENV)
    reports_dir = Path(__file__).resolve().parents[1] / "reports"
    json_path = reports_dir / "config_profile_validation.json"
    txt_path = reports_dir / "config_profile_validation.txt"
    if json_path.exists():
        json_path.unlink()
    if txt_path.exists():
        txt_path.unlink()

    completed = subprocess.run(
        [sys.executable, str(script_path), "--profile", "paper_safe", "--export-json", "--export-txt"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "status=VALID" in completed.stdout
    assert json_path.exists()
    assert txt_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["profile"] == "paper_safe"
