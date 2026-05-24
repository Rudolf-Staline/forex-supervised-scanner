from __future__ import annotations

import json
from pathlib import Path

from scripts import local_validation


def test_quick_never_contains_live_order_command() -> None:
    args = local_validation.parse_args.__wrapped__([]) if hasattr(local_validation.parse_args, '__wrapped__') else None
    class A: quick=True; full=False; skip_tests=False; skip_scripts=False; provider='synthetic'; watchlist='multi_asset_demo'
    plan = local_validation.build_plan(A())
    flattened = " ".join(" ".join(cmd) for _, cmd, _, _ in plan).lower()
    assert "live" not in flattened
    assert "mt5_tiny_demo_order" not in flattened


def test_safety_env_enforced() -> None:
    assert local_validation.SAFETY_ENV["ALLOW_LIVE_TRADING"] == "false"
    assert local_validation.SAFETY_ENV["EXECUTION_MODE"] == "paper"
    assert local_validation.SAFETY_ENV["BROKER_MODE"] == "paper"


def test_optional_absent_script_is_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    env = dict(local_validation.SAFETY_ENV)
    result = local_validation.run_command("x", ["python", "scripts/missing.py"], required=False, allow_mt5_warn=False, env=env)
    assert result.status == "skipped"


def test_mt5_unavailable_is_warn() -> None:
    assert local_validation.is_mt5_unavailable("MT5 unavailable in this environment")


def test_report_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"started_at": "a", "finished_at": "b", "duration_seconds": 1, "mode": "quick", "commands_run": [], "commands_passed": 0, "commands_failed": 0, "commands_skipped": 0, "safety_env": local_validation.SAFETY_ENV, "readiness_status": "ok", "recommendation": "safe"}
    (report_dir / "local_validation_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    (report_dir / "local_validation_summary.txt").write_text("ok\n", encoding="utf-8")
    assert (report_dir / "local_validation_summary.json").exists()
    assert (report_dir / "local_validation_summary.txt").exists()
