#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SAFETY_ENV = {
    "EXECUTION_MODE": "paper",
    "BROKER_MODE": "paper",
    "ALLOW_LIVE_TRADING": "false",
    "MT5_DEMO_ONLY": "true",
    "ENABLE_DEMO_EXECUTION": "false",
    "AUTO_BOT_ENABLED": "false",
    "NOTIFICATIONS_ENABLED": "false",
}


@dataclass
class CommandResult:
    name: str
    status: str
    command: list[str]
    reason: str = ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Safe local validation runner")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true")
    mode.add_argument("--full", action="store_true")
    p.add_argument("--skip-tests", action="store_true")
    p.add_argument("--skip-scripts", action="store_true")
    p.add_argument("--export-report", action="store_true")
    p.add_argument("--provider", choices=["synthetic", "mt5"], default="synthetic")
    p.add_argument("--watchlist", default="multi_asset_demo")
    return p.parse_args()


def is_mt5_unavailable(output: str) -> bool:
    lower = output.lower()
    markers = ["mt5", "metatrader", "terminal", "initialize", "connection", "unavailable"]
    return any(m in lower for m in ["mt5 unavailable", "metatrader", "initialize() failed", "terminal"])


def run_command(name: str, command: list[str], *, required: bool, allow_mt5_warn: bool, env: dict[str, str]) -> CommandResult:
    if not required:
        return CommandResult(name=name, status="skipped", command=command, reason="optional script absent")
    completed = subprocess.run(command, capture_output=True, text=True, env=env)
    if completed.returncode == 0:
        return CommandResult(name=name, status="passed", command=command)
    merged = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if allow_mt5_warn and is_mt5_unavailable(merged):
        return CommandResult(name=name, status="warn", command=command, reason="MT5 unavailable in cloud environment")
    return CommandResult(name=name, status="failed", command=command, reason=f"exit_code={completed.returncode}")


def build_plan(args: argparse.Namespace) -> list[tuple[str, list[str], bool, bool]]:
    has = lambda p: Path(p).exists()
    plan: list[tuple[str, list[str], bool, bool]] = []

    if not args.skip_scripts:
        plan.append((
            "readiness_report",
            ["python", "scripts/readiness_report.py", "--skip-tests"],
            has("scripts/readiness_report.py"),
            False,
        ))

    if not args.skip_tests:
        plan.append((
            "cloud_safe_tests",
            ["python", "-m", "pytest", "-q", "tests/test_safety.py", "tests/test_readiness_report.py", "--maxfail=1"],
            True,
            False,
        ))

    if not args.skip_scripts:
        plan.extend([
            (
                "forward_test_paper",
                ["python", "scripts/forward_test_paper.py", "--provider", "synthetic", "--max-cycles", "1", "--watchlist", args.watchlist],
                has("scripts/forward_test_paper.py"),
                False,
            ),
            (
                "autonomous_supervisor_dry_run",
                [
                    "python",
                    "scripts/run_autonomous_supervisor.py",
                    "--provider",
                    "synthetic",
                    "--once",
                    "--symbols",
                    "EUR/USD",
                    "--dry-run",
                    "--no-sleep",
                ],
                has("scripts/run_autonomous_supervisor.py"),
                False,
            ),
            (
                "multi_asset_signal_report",
                ["python", "scripts/multi_asset_signal_report.py", "--watchlist", args.watchlist],
                has("scripts/multi_asset_signal_report.py"),
                False,
            ),
        ])

    if args.full:
        if not args.skip_scripts:
            plan.extend([
                ("threshold_optimizer_report", ["python", "scripts/threshold_optimizer_report.py"], has("scripts/threshold_optimizer_report.py"), False),
                ("paper_fill_report", ["python", "scripts/paper_fill_report.py"], has("scripts/paper_fill_report.py"), False),
                ("mt5_reconcile_demo", ["python", "scripts/mt5_reconcile_demo.py"], has("scripts/mt5_reconcile_demo.py"), True),
                ("backtest_multi_asset", ["python", "scripts/backtest_multi_asset.py", "--provider", "synthetic", "--hours", "24"], has("scripts/backtest_multi_asset.py"), False),
            ])
        if not args.skip_tests:
            plan.append(("dashboard_loader_tests", ["python", "-m", "pytest", "-q", "tests/test_dashboard_data_loading.py", "--maxfail=1"], Path("tests/test_dashboard_data_loading.py").exists(), False))
    return plan


def main() -> int:
    args = parse_args()
    mode = "full" if args.full else "quick"
    started = time.time()
    started_at = datetime.now(timezone.utc).isoformat()

    env = dict(os.environ)
    env.update(SAFETY_ENV)

    results = [run_command(n, c, required=r, allow_mt5_warn=w and args.provider != "mt5", env=env) for n, c, r, w in build_plan(args)]

    commands_run = [asdict(r) for r in results]
    passed = sum(r.status == "passed" for r in results)
    failed = sum(r.status == "failed" for r in results)
    skipped = sum(r.status == "skipped" for r in results)
    warn = sum(r.status == "warn" for r in results)

    recommendation = "safe_to_iterate" if failed == 0 else "investigate_failures"
    readiness_status = "ok" if failed == 0 else "needs_attention"

    summary = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started, 3),
        "mode": mode,
        "commands_run": commands_run,
        "commands_passed": passed,
        "commands_failed": failed,
        "commands_skipped": skipped,
        "safety_env": SAFETY_ENV,
        "readiness_status": readiness_status,
        "recommendation": recommendation,
        "warnings": warn,
    }

    if args.export_report:
        out_dir = Path("reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "local_validation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        lines = [
            f"mode: {summary['mode']}",
            f"passed: {passed}",
            f"failed: {failed}",
            f"skipped: {skipped}",
            f"warnings: {warn}",
            f"readiness_status: {readiness_status}",
            f"recommendation: {recommendation}",
        ]
        (out_dir / "local_validation_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
