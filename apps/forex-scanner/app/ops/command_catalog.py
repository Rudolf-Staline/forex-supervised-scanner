"""Static command catalog builder for scripts/*.py."""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

CATEGORIES = {"all", "reports", "validation", "mt5", "paper", "ops"}


@dataclass
class CommandEntry:
    script_name: str
    path: str
    guessed_category: str
    safety_level: str
    description: str
    example_command: str
    requires_mt5: bool
    can_send_order: bool
    recommended_env: str
    warnings: list[str]


ORDER_KEYWORDS = ("order_send", "place_order", "send_order", "submit_order", "mt5_place")
MT5_KEYWORDS = ("metatrader5", "mt5", "meta trader")
REPORT_KEYWORDS = ("report", "summary", "export", "index")
VALIDATION_KEYWORDS = ("validation", "check", "test", "health", "audit", "doctor")
OPS_KEYWORDS = ("operator", "control", "monitor", "recovery", "session")


def _guess_category(script_name: str, content: str) -> str:
    text = f"{script_name} {content.lower()}"
    if "paper" in text:
        return "paper"
    if any(k in text for k in REPORT_KEYWORDS):
        return "reports"
    if any(k in text for k in VALIDATION_KEYWORDS):
        return "validation"
    if any(k in text for k in MT5_KEYWORDS):
        return "mt5"
    if any(k in text for k in OPS_KEYWORDS):
        return "ops"
    return "ops"


def _extract_description(script_name: str, content: str) -> str:
    try:
        module = ast.parse(content)
        doc = ast.get_docstring(module)
        if doc:
            return doc.strip().splitlines()[0][:140]
    except SyntaxError:
        pass
    return f"Utility command for {script_name}."


def _classify_safety(script_name: str, content: str) -> tuple[str, bool, bool, list[str], str]:
    text = f"{script_name.lower()}\n{content.lower()}"
    requires_mt5 = any(k in text for k in MT5_KEYWORDS)
    can_send_order = any(k in text for k in ORDER_KEYWORDS)
    warnings: list[str] = []

    if can_send_order:
        warnings.append("Potential order placement keywords detected; use demo-only safeguards.")

    if can_send_order:
        if "demo" in text:
            level = "DEMO_GATED"
            env = "DEMO_ACCOUNT"
        else:
            level = "UNKNOWN"
            env = "ISOLATED_REVIEW"
            warnings.append("Order-capable behavior unclear; manual review required.")
    elif requires_mt5:
        level = "MT5_READONLY"
        env = "MT5_READONLY"
    elif "paper" in text:
        level = "PAPER_ONLY"
        env = "PAPER"
    elif any(x in text for x in ("report", "summary", "validation", "check", "health", "audit")):
        level = "READ_ONLY"
        env = "LOCAL"
    else:
        level = "UNKNOWN"
        env = "LOCAL"
        warnings.append("Safety profile could not be confidently inferred from static scan.")

    return level, requires_mt5, can_send_order, warnings, env


def scan_commands(scripts_dir: Path) -> list[CommandEntry]:
    entries: list[CommandEntry] = []
    for path in sorted(scripts_dir.glob("*.py")):
        content = path.read_text(encoding="utf-8")
        script_name = path.name
        category = _guess_category(script_name, content)
        level, requires_mt5, can_send_order, warnings, env = _classify_safety(script_name, content)
        entries.append(
            CommandEntry(
                script_name=script_name,
                path=str(path).replace("\\", "/"),
                guessed_category=category,
                safety_level=level,
                description=_extract_description(script_name, content),
                example_command=f"python scripts/{script_name}",
                requires_mt5=requires_mt5,
                can_send_order=can_send_order,
                recommended_env=env,
                warnings=warnings,
            )
        )
    return entries


def filter_entries(entries: list[CommandEntry], category: str, show_unsafe: bool) -> list[CommandEntry]:
    if category not in CATEGORIES:
        raise ValueError(f"Unsupported category: {category}")
    filtered = entries if category == "all" else [e for e in entries if e.guessed_category == category]
    if not show_unsafe:
        filtered = [e for e in filtered if e.safety_level != "UNKNOWN"]
    return filtered


def export_catalog_json(entries: list[CommandEntry], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "command_catalog.json"
    payload = [asdict(e) for e in entries]
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def export_catalog_md(entries: list[CommandEntry], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "command_catalog.md"
    lines = [
        "# Command Catalog",
        "",
        "| Script | Category | Safety | MT5 | Order Send | Example |",
        "|---|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            f"| `{e.script_name}` | {e.guessed_category} | {e.safety_level} | {e.requires_mt5} | {e.can_send_order} | `{e.example_command}` |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
