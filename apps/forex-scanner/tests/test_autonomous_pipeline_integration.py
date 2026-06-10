import pytest
import subprocess
import sys
from pathlib import Path

def test_cli_help_options_do_not_drift() -> None:
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/run_autonomous_supervisor.py", "--help"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True
    )
    help_text = result.stdout
    assert "--export-recovery-json" in help_text
    assert "--plan-recovery-on-block" in help_text

def test_diagnostic_pipeline_does_not_mutate_env_and_respects_paper_mode(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    env_file = repo / ".env"
    original = env_file.read_text(encoding="utf-8") if env_file.exists() else None

    result = subprocess.run(
        [
            sys.executable, "scripts/run_autonomous_supervisor.py",
            "--once", "--symbols", "EUR/USD",
            "--dry-run",
            "--build-evidence-first",
            "--evidence-mode", "read-only",
            "--readiness-only",
            "--plan-recovery-on-block",
            "--export-json",
            "--export-txt"
        ],
        cwd=repo,
        capture_output=True,
        text=True
    )

    after = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    assert original == after
    assert "live_execution_allowed=false" in result.stdout
