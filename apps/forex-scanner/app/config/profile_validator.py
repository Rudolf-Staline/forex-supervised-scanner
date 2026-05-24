from __future__ import annotations

import os
from dataclasses import asdict, dataclass

VALID_STATUSES = {"VALID", "WARN", "BLOCKED", "DANGEROUS"}

SUPPORTED_PROFILES = {
    "paper_safe",
    "cloud_safe",
    "mt5_readonly",
    "mt5_demo_precheck",
    "demo_execution_locked",
}

TRACKED_VARIABLES = [
    "EXECUTION_MODE",
    "BROKER_MODE",
    "ALLOW_LIVE_TRADING",
    "MT5_DEMO_ONLY",
    "ENABLE_DEMO_EXECUTION",
    "AUTO_BOT_ENABLED",
    "ALLOW_MULTI_ASSET_DEMO_TRADING",
    "NOTIFICATIONS_ENABLED",
    "MAX_DEMO_ORDER_VOLUME",
    "MAX_DEMO_ORDERS_PER_DAY",
]

PROFILE_EXPECTATIONS: dict[str, dict[str, str]] = {
    "paper_safe": {
        "EXECUTION_MODE": "paper",
        "BROKER_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "ENABLE_DEMO_EXECUTION": "false",
        "AUTO_BOT_ENABLED": "false",
    },
    "cloud_safe": {
        "EXECUTION_MODE": "paper",
        "BROKER_MODE": "paper",
        "ALLOW_LIVE_TRADING": "false",
        "ENABLE_DEMO_EXECUTION": "false",
    },
    "mt5_readonly": {
        "EXECUTION_MODE": "shadow",
        "BROKER_MODE": "mt5",
        "ALLOW_LIVE_TRADING": "false",
        "MT5_DEMO_ONLY": "true",
        "ENABLE_DEMO_EXECUTION": "false",
        "AUTO_BOT_ENABLED": "false",
    },
    "mt5_demo_precheck": {
        "EXECUTION_MODE": "shadow",
        "BROKER_MODE": "mt5",
        "ALLOW_LIVE_TRADING": "false",
        "MT5_DEMO_ONLY": "true",
    },
    "demo_execution_locked": {
        "EXECUTION_MODE": "paper",
        "BROKER_MODE": "mt5",
        "ALLOW_LIVE_TRADING": "false",
        "MT5_DEMO_ONLY": "true",
        "ENABLE_DEMO_EXECUTION": "false",
    },
}


@dataclass
class ProfileValidationReport:
    profile: str
    status: str
    variables_ok: dict[str, str]
    variables_missing: list[str]
    variables_wrong: dict[str, dict[str, str]]
    dangerous_flags: list[str]
    recommendations: list[str]
    safe_command_examples: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_profile(profile: str, env: dict[str, str] | None = None) -> ProfileValidationReport:
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"Unsupported profile: {profile}")

    source = dict(env) if env is not None else dict(os.environ)

    expected = PROFILE_EXPECTATIONS[profile]
    variables_ok: dict[str, str] = {}
    variables_missing: list[str] = []
    variables_wrong: dict[str, dict[str, str]] = {}
    dangerous_flags: list[str] = []
    recommendations: list[str] = []

    for key in TRACKED_VARIABLES:
        value = source.get(key)
        if value is None:
            variables_missing.append(key)
            continue
        expected_value = expected.get(key)
        normalized = value.strip()
        if expected_value is None:
            variables_ok[key] = normalized
            continue
        if normalized.lower() == expected_value.lower():
            variables_ok[key] = normalized
        else:
            variables_wrong[key] = {"expected": expected_value, "actual": normalized}

    allow_live = str(source.get("ALLOW_LIVE_TRADING", "")).strip().lower() == "true"
    demo_execution = str(source.get("ENABLE_DEMO_EXECUTION", "")).strip().lower() == "true"
    broker_mode = str(source.get("BROKER_MODE", "")).strip().lower()
    mt5_demo_only = str(source.get("MT5_DEMO_ONLY", "")).strip().lower()

    if allow_live:
        dangerous_flags.append("ALLOW_LIVE_TRADING=true")
    if profile in {"paper_safe", "cloud_safe"} and broker_mode and broker_mode != "paper":
        dangerous_flags.append("BROKER_MODE must be paper for paper_safe/cloud_safe")
    if profile.startswith("mt5") or profile == "demo_execution_locked":
        if mt5_demo_only and mt5_demo_only != "true":
            dangerous_flags.append("MT5_DEMO_ONLY must be true for MT5 profiles")

    status = "VALID"
    if allow_live:
        status = "DANGEROUS"
    elif demo_execution and profile != "mt5_demo_precheck":
        status = "BLOCKED"
        dangerous_flags.append("ENABLE_DEMO_EXECUTION=true is blocked for this profile")
    elif variables_wrong:
        status = "WARN"

    if variables_missing:
        recommendations.append("Add missing variables with safe defaults before execution.")
    if variables_wrong:
        recommendations.append("Align wrong variables with the selected profile baseline.")
    if status == "DANGEROUS":
        recommendations.append("Set ALLOW_LIVE_TRADING=false immediately and re-run validator.")
    if status == "BLOCKED":
        recommendations.append("Disable ENABLE_DEMO_EXECUTION or switch to mt5_demo_precheck profile.")
    if not recommendations:
        recommendations.append("Profile is safe; keep current controls unchanged.")

    safe_command_examples = [
        "python scripts/config_profile_validator.py --profile paper_safe --show-recommendations",
        "python scripts/config_profile_validator.py --profile mt5_readonly --export-json --export-txt",
    ]

    if status not in VALID_STATUSES:
        raise AssertionError("Invalid status generated")

    return ProfileValidationReport(
        profile=profile,
        status=status,
        variables_ok=variables_ok,
        variables_missing=variables_missing,
        variables_wrong=variables_wrong,
        dangerous_flags=dangerous_flags,
        recommendations=recommendations,
        safe_command_examples=safe_command_examples,
    )
