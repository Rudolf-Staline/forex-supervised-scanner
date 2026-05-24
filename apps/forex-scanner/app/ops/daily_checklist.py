"""Build daily safe-operations checklist for paper/read-only usage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

GUIDANCE_BANNER = (
    "Daily checklist is operational guidance only; it does not authorize order execution."
)

SAFE_MODES = {"paper", "mt5-readonly", "analysis-only"}


@dataclass(frozen=True)
class DailyChecklistOptions:
    mode: str = "paper"


def build_daily_checklist(options: DailyChecklistOptions) -> dict[str, object]:
    if options.mode not in SAFE_MODES:
        raise ValueError(f"Unsupported mode '{options.mode}'. Expected one of: {sorted(SAFE_MODES)}")

    checklist = {
        "mode": options.mode,
        "guidance_banner": GUIDANCE_BANNER,
        "safety_constraints": {
            "allow_live_trading": "false",
            "enable_demo_execution": "false",
            "broker_mode": "paper",
        },
        "checks": [
            "Git status check (clean tree, correct branch).",
            "Python environment check (version + virtualenv active).",
            "Expected safety variables present and set to safe values.",
            "Validate ALLOW_LIVE_TRADING=false.",
            "Validate ENABLE_DEMO_EXECUTION=false.",
            "Validate BROKER_MODE=paper.",
            "Run quick validation commands.",
            "Run recommended reporting commands.",
            "Verify MT5 read-only constraints if mode requires MT5.",
        ],
        "quick_validation_commands": [
            "python -m pytest -q tests/test_safety.py tests/test_demo_bot.py --maxfail=1",
            "python -m pytest -q tests/test_daily_safe_ops_checklist.py --maxfail=1",
        ],
        "recommended_reporting_commands": [
            "python scripts/daily_safe_ops_checklist.py --mode paper --export-json --export-md --export-txt",
            "python scripts/report_index.py --export-json",
        ],
        "mt5_readonly_guidance": {
            "applicable": options.mode == "mt5-readonly",
            "check": "If using MT5 tooling, ensure read-only validation passes and no trade API is used.",
            "if_unavailable": "Switch to analysis-only mode, investigate connectivity later, and do not bypass safeguards.",
        },
        "incident_playbooks": {
            "if_tests_fail": "Stop operations, fix failing tests, rerun validations, and keep execution disabled.",
            "if_github_actions_fail": "Review failed workflow logs, reproduce locally, and block any execution attempts.",
            "if_signal_interesting": "Record signal in journal/watchlist, run extra analysis, and never place orders from this checklist.",
        },
        "forbidden_steps": [
            "Do not enable ALLOW_LIVE_TRADING.",
            "Do not enable ENABLE_DEMO_EXECUTION.",
            "Do not place any broker or MT5 order.",
            "Do not modify .env as part of this checklist run.",
            "Do not bypass failing safety checks.",
        ],
        "final_confirmation": "No order is authorized by this checklist.",
        "subprocess_used": False,
        "mt5_called": False,
        "env_mutation_performed": False,
    }
    return checklist


def export_daily_checklist_json(checklist: dict[str, object], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    destination = reports_dir / "daily_safe_ops_checklist.json"
    destination.write_text(json.dumps(checklist, indent=2, sort_keys=True), encoding="utf-8")
    return destination


def export_daily_checklist_md(checklist: dict[str, object], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    destination = reports_dir / "daily_safe_ops_checklist.md"
    checks = "\n".join(f"- [ ] {item}" for item in checklist["checks"])
    quick = "\n".join(f"- `{item}`" for item in checklist["quick_validation_commands"])
    reports = "\n".join(f"- `{item}`" for item in checklist["recommended_reporting_commands"])
    forbidden = "\n".join(f"- {item}" for item in checklist["forbidden_steps"])

    md = f"""# Daily Safe Operations Checklist

**Mode:** `{checklist['mode']}`

> {checklist['guidance_banner']}

## Core Checks
{checks}

## Expected Safety Variables
- `ALLOW_LIVE_TRADING=false`
- `ENABLE_DEMO_EXECUTION=false`
- `BROKER_MODE=paper`

## Quick Validation Commands
{quick}

## Recommended Reporting Commands
{reports}

## MT5 Read-Only Guidance
- Applicable: `{checklist['mt5_readonly_guidance']['applicable']}`
- Check: {checklist['mt5_readonly_guidance']['check']}
- If MT5 unavailable: {checklist['mt5_readonly_guidance']['if_unavailable']}

## Incident Playbooks
- If tests fail: {checklist['incident_playbooks']['if_tests_fail']}
- If GitHub Actions fail: {checklist['incident_playbooks']['if_github_actions_fail']}
- If a signal seems interesting: {checklist['incident_playbooks']['if_signal_interesting']}

## Forbidden Steps
{forbidden}

## Final Confirmation
{checklist['final_confirmation']}
"""
    destination.write_text(md, encoding="utf-8")
    return destination


def export_daily_checklist_txt(checklist: dict[str, object], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    destination = reports_dir / "daily_safe_ops_checklist.txt"
    lines = [
        "Daily Safe Operations Checklist",
        f"Mode: {checklist['mode']}",
        checklist["guidance_banner"],
        "",
        "Expected safety variables:",
        "- ALLOW_LIVE_TRADING=false",
        "- ENABLE_DEMO_EXECUTION=false",
        "- BROKER_MODE=paper",
        "",
        "Forbidden steps:",
    ]
    lines.extend(f"- {item}" for item in checklist["forbidden_steps"])
    lines.extend(["", f"Final confirmation: {checklist['final_confirmation']}"])
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination
