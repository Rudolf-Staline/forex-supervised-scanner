from __future__ import annotations

import os

from app.safety.env_doctor import evaluate_environment, export_report


def _base_env() -> dict[str, str]:
    return {
        "EXECUTION_MODE": "paper",
        "BROKER_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "MT5_DEMO_ONLY": "true",
        "ENABLE_DEMO_EXECUTION": "false",
        "AUTO_BOT_ENABLED": "false",
        "ALLOW_MULTI_ASSET_DEMO_TRADING": "false",
        "NOTIFICATIONS_ENABLED": "true",
        "MT5_SERVER": "demo-server",
        "MAX_DEMO_ORDER_VOLUME": "0.1",
        "MAX_DEMO_ORDERS_PER_DAY": "10",
        "FOREX_SCANNER_MAGIC_NUMBER": "12345",
    }


def test_paper_environment_safe() -> None:
    report = evaluate_environment(mode="paper", env=_base_env())
    assert report.status.value == "SAFE_PAPER"
    assert not report.dangerous_variables


def test_live_trading_true_is_dangerous() -> None:
    env = _base_env()
    env["ALLOW_LIVE_TRADING"] = "true"
    report = evaluate_environment(mode="paper", env=env)
    assert report.status.value == "DANGEROUS"


def test_demo_execution_true_is_blocked_outside_precheck() -> None:
    env = _base_env()
    env["ENABLE_DEMO_EXECUTION"] = "true"
    report = evaluate_environment(mode="paper", env=env)
    assert report.status.value == "BLOCKED"


def test_mt5_demo_only_false_blocks_mt5_modes() -> None:
    env = _base_env()
    env["BROKER_MODE"] = "mt5"
    env["EXECUTION_MODE"] = "readonly"
    env["MT5_DEMO_ONLY"] = "false"
    report = evaluate_environment(mode="mt5-readonly", env=env)
    assert report.status.value == "BLOCKED"


def test_export_json_and_txt(tmp_path) -> None:
    report = evaluate_environment(mode="paper", env=_base_env())
    written = export_report(report, export_json=True, export_txt=True, output_dir=tmp_path)
    assert (tmp_path / "safety_env_doctor.json") in written
    assert (tmp_path / "safety_env_doctor.txt") in written


def test_no_mutation_of_os_environ() -> None:
    snapshot = dict(os.environ)
    _ = evaluate_environment(mode="paper")
    assert snapshot == dict(os.environ)
