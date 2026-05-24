from __future__ import annotations

import os
from pathlib import Path

from app.ops.daily_checklist import (
    DailyChecklistOptions,
    build_daily_checklist,
    export_daily_checklist_json,
    export_daily_checklist_md,
    export_daily_checklist_txt,
)


def test_generation_json_md_txt(tmp_path: Path) -> None:
    checklist = build_daily_checklist(DailyChecklistOptions(mode="paper"))
    json_path = export_daily_checklist_json(checklist, tmp_path)
    md_path = export_daily_checklist_md(checklist, tmp_path)
    txt_path = export_daily_checklist_txt(checklist, tmp_path)

    assert json_path.exists()
    assert md_path.exists()
    assert txt_path.exists()


def test_contains_safety_variables_and_forbidden_steps() -> None:
    checklist = build_daily_checklist(DailyChecklistOptions(mode="analysis-only"))
    constraints = checklist["safety_constraints"]
    assert constraints["allow_live_trading"] == "false"
    assert constraints["enable_demo_execution"] == "false"
    assert constraints["broker_mode"] == "paper"
    forbidden = " ".join(checklist["forbidden_steps"])
    assert "ALLOW_LIVE_TRADING" in forbidden
    assert "ENABLE_DEMO_EXECUTION" in forbidden


def test_no_live_trading_commands() -> None:
    checklist = build_daily_checklist(DailyChecklistOptions(mode="paper"))
    all_commands = checklist["quick_validation_commands"] + checklist["recommended_reporting_commands"]
    full_text = " ".join(all_commands).lower()
    assert "live" not in full_text
    assert "order_send" not in full_text
    assert "mt5.order" not in full_text


def test_no_subprocess_no_mt5_no_env_mutation() -> None:
    before = dict(os.environ)
    checklist = build_daily_checklist(DailyChecklistOptions(mode="mt5-readonly"))
    after = dict(os.environ)

    assert checklist["subprocess_used"] is False
    assert checklist["mt5_called"] is False
    assert checklist["env_mutation_performed"] is False
    assert before == after
