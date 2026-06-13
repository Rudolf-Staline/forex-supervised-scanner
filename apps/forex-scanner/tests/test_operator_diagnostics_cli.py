"""CLI smoke tests for the operator diagnostic scripts (issue #120)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV = {
    "EXECUTION_MODE": "paper",
    "ALLOW_LIVE_TRADING": "false",
    "BROKER_MODE": "paper",
    "AUTO_BOT_ENABLED": "false",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _run(script: str, args: list[str]):
    return subprocess.run(
        [sys.executable, f"scripts/{script}", *args],
        cwd=ROOT, text=True, capture_output=True, timeout=120, env=ENV,
    )


@pytest.mark.parametrize("script", ["decision_doctor.py", "explain_last_block.py", "explain_last_decision.py"])
def test_export_scripts_smoke(script, tmp_path):
    result = _run(script, ["--reports-dir", str(tmp_path), "--export-json", "--export-txt"])
    assert result.returncode == 0, result.stderr
    assert "SAFETY:" in result.stdout
    # No stack traces for a normal empty reports directory.
    assert "Traceback" not in result.stderr


def test_next_safe_bot_command_smoke(tmp_path):
    result = _run("next_safe_bot_command.py", ["--reports-dir", str(tmp_path), "--export-json"])
    assert result.returncode == 0, result.stderr
    assert "next_safe_command:" in result.stdout
    payload = json.loads((tmp_path / "next_safe_bot_command.json").read_text(encoding="utf-8"))
    assert payload["next_safe_command"]
    lowered = payload["next_safe_command"].lower()
    assert "broker_live" not in lowered and "enable_live" not in lowered and "mt5_demo" not in lowered


def test_decision_doctor_exports_files(tmp_path):
    result = _run("decision_doctor.py", ["--reports-dir", str(tmp_path), "--export-json", "--export-txt"])
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "decision_doctor_summary.json").exists()
    assert (tmp_path / "decision_doctor_report.txt").exists()


def test_strict_mode_fails_on_hard_blocker(tmp_path):
    (tmp_path / "local_mt5_realtime_validation.json").write_text(
        json.dumps({"final_status": "BLOCKED_STALE_DATA", "blocking_reasons": ["stale"]}), encoding="utf-8"
    )
    result = _run("decision_doctor.py", ["--reports-dir", str(tmp_path), "--strict"])
    assert result.returncode == 1
    assert "BLOCKED" in result.stdout
