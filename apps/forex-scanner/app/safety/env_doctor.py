from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
from typing import Mapping


class SafetyStatus(str, Enum):
    SAFE_PAPER = "SAFE_PAPER"
    SAFE_READONLY_DEMO = "SAFE_READONLY_DEMO"
    BLOCKED = "BLOCKED"
    DANGEROUS = "DANGEROUS"


@dataclass(frozen=True)
class SafetyReport:
    mode: str
    status: SafetyStatus
    ok_variables: list[str]
    missing_variables: list[str]
    dangerous_variables: list[str]
    recommendations: list[str]
    reminder: str

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "status": self.status.value,
            "ok_variables": self.ok_variables,
            "missing_variables": self.missing_variables,
            "dangerous_variables": self.dangerous_variables,
            "recommendations": self.recommendations,
            "reminder": self.reminder,
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
    "MT5_SERVER",
    "MAX_DEMO_ORDER_VOLUME",
    "MAX_DEMO_ORDERS_PER_DAY",
    "FOREX_SCANNER_MAGIC_NUMBER",
]

ALLOWED_MODES = {"paper", "mt5-readonly", "mt5-demo-precheck"}


def _normalize(value: str | None) -> str:
    return "" if value is None else value.strip()


def _is_true(value: str | None) -> bool:
    return _normalize(value).lower() in {"1", "true", "yes", "on"}


def evaluate_environment(mode: str, env: Mapping[str, str] | None = None) -> SafetyReport:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"Unsupported mode: {mode}")

    current_env = os.environ if env is None else env
    values = {key: _normalize(current_env.get(key)) for key in TRACKED_VARIABLES}

    ok_variables: list[str] = []
    missing_variables: list[str] = []
    dangerous_variables: list[str] = []
    recommendations: list[str] = []

    for key in TRACKED_VARIABLES:
        if values[key]:
            ok_variables.append(key)
        else:
            missing_variables.append(key)

    if _is_true(values["ALLOW_LIVE_TRADING"]):
        dangerous_variables.append("ALLOW_LIVE_TRADING=true")
        recommendations.append("Set ALLOW_LIVE_TRADING=false immediately.")

    if _is_true(values["ENABLE_DEMO_EXECUTION"]) and mode != "mt5-demo-precheck":
        dangerous_variables.append("ENABLE_DEMO_EXECUTION=true")
        recommendations.append(
            "Use ENABLE_DEMO_EXECUTION=false outside mt5-demo-precheck mode."
        )

    if mode in {"mt5-readonly", "mt5-demo-precheck"} and values["MT5_DEMO_ONLY"].lower() == "false":
        dangerous_variables.append("MT5_DEMO_ONLY=false")
        recommendations.append("Set MT5_DEMO_ONLY=true before using any MT5 mode.")

    if mode == "paper":
        if values["BROKER_MODE"].lower() == "paper" and values["EXECUTION_MODE"].lower() == "paper" and not _is_true(values["ALLOW_LIVE_TRADING"]):
            status = SafetyStatus.SAFE_PAPER
        else:
            status = SafetyStatus.BLOCKED
            recommendations.append(
                "For paper mode, set BROKER_MODE=paper and EXECUTION_MODE=paper with ALLOW_LIVE_TRADING=false."
            )
    elif mode == "mt5-readonly":
        if values["BROKER_MODE"].lower() == "mt5" and values["EXECUTION_MODE"].lower() == "readonly":
            status = SafetyStatus.SAFE_READONLY_DEMO
        else:
            status = SafetyStatus.BLOCKED
            recommendations.append(
                "For mt5-readonly mode, use BROKER_MODE=mt5 and EXECUTION_MODE=readonly."
            )
    else:
        status = SafetyStatus.BLOCKED
        if values["BROKER_MODE"].lower() == "mt5" and values["EXECUTION_MODE"].lower() in {"demo", "paper"}:
            status = SafetyStatus.BLOCKED
        recommendations.append(
            "mt5-demo-precheck is only a safety pre-check and never sends orders."
        )

    if dangerous_variables:
        if any(item.startswith("ALLOW_LIVE_TRADING=true") for item in dangerous_variables):
            status = SafetyStatus.DANGEROUS
        elif status != SafetyStatus.DANGEROUS:
            status = SafetyStatus.BLOCKED

    reminder = "Diagnostic only: no MT5 loading and no order is ever sent by this tool."
    if not recommendations:
        recommendations.append("Environment looks safe for the selected diagnostic mode.")

    return SafetyReport(
        mode=mode,
        status=status,
        ok_variables=sorted(ok_variables),
        missing_variables=sorted(missing_variables),
        dangerous_variables=dangerous_variables,
        recommendations=recommendations,
        reminder=reminder,
    )


def report_to_text(report: SafetyReport) -> str:
    sections = [
        "Safety Environment Doctor",
        f"Mode: {report.mode}",
        f"Status: {report.status.value}",
        "",
        "OK variables:",
        *(f"- {name}" for name in report.ok_variables),
        "",
        "Missing variables:",
        *(f"- {name}" for name in report.missing_variables),
        "",
        "Dangerous variables:",
        *(f"- {name}" for name in report.dangerous_variables),
        "",
        "Recommendations:",
        *(f"- {item}" for item in report.recommendations),
        "",
        report.reminder,
    ]
    return "\n".join(sections) + "\n"


def export_report(report: SafetyReport, export_json: bool, export_txt: bool, output_dir: Path | str = "reports") -> list[Path]:
    written: list[Path] = []
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    if export_json:
        json_path = destination / "safety_env_doctor.json"
        json_path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
        written.append(json_path)

    if export_txt:
        txt_path = destination / "safety_env_doctor.txt"
        txt_path.write_text(report_to_text(report), encoding="utf-8")
        written.append(txt_path)

    return written
