from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_score_decomposition_cli_smoke_exports_reports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        "scripts/score_decomposition.py",
        "--provider",
        "synthetic",
        "--symbols",
        "EUR/USD",
        "--style",
        "day_trading",
        "--reports-dir",
        str(tmp_path),
        "--export-json",
        "--export-txt",
    ]
    result = subprocess.run(cmd, cwd=root, text=True, capture_output=True, check=True, timeout=60)

    assert "Decision Trace Report" in result.stdout
    assert "symbol=EUR/USD" in result.stdout
    trace_path = tmp_path / "decision_trace.json"
    policy_path = tmp_path / "min_score_policy_report.json"
    assert trace_path.exists()
    assert policy_path.exists()
    traces = json.loads(trace_path.read_text(encoding="utf-8"))
    assert traces
    assert traces[0]["score_components"] is not None
