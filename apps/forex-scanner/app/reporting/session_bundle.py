"""Read-only paper session bundle exporter.

Packages existing paper/demo report artifacts into an auditable zip with a
manifest and sha256 checksums. The bundler never runs trading logic, never
imports the MT5 terminal API, never calls ``order_send``, never mutates
``.env``, and works fully offline from files in ``reports/``.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.reporting.operator_dashboard import (
    DEFAULT_OPERATOR_DASHBOARD_JSON,
    DEFAULT_OPERATOR_DASHBOARD_TXT,
    OPTIONAL_REPORTS,
    REQUIRED_REPORTS,
)

DEFAULT_BUNDLE_OUTPUT_DIRNAME = "bundles"

DEFAULT_BUNDLE_FILES: tuple[str, ...] = tuple(
    list(REQUIRED_REPORTS.values())
    + list(OPTIONAL_REPORTS.values())
    + [
        DEFAULT_OPERATOR_DASHBOARD_JSON,
        DEFAULT_OPERATOR_DASHBOARD_TXT,
        "local_mt5_realtime_validation.txt",
        "local_mt5_realtime_samples.csv",
        "realtime_command_center_report.txt",
        "realtime_paper_positions.txt",
        "autonomous_scenario_suite.txt",
        "autonomous_readiness_report.txt",
        "autonomous_evidence_report.txt",
        "autonomous_policy_report.txt",
        "autonomous_recovery_plan.txt",
    ]
)

SAFETY_FLAGS: dict[str, object] = {
    "read_only_bundle": True,
    "paper_demo_only": True,
    "live_trading_enabled": False,
    "live_execution_allowed": False,
    "broker_live_execution_allowed": False,
    "broker_order_submission_allowed": False,
    "order_send_called": False,
    "env_mutation_performed": False,
    "mt5_required": False,
}

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def build_paper_session_bundle(
    reports_dir: Path,
    output_dir: Path,
    session_name: str,
    *,
    filenames: tuple[str, ...] | list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create ``<session-name>.zip`` plus JSON/TXT manifests from local reports."""
    if not _SESSION_NAME_RE.match(session_name):
        raise ValueError("session name must contain only letters, digits, '.', '_' or '-' and not start with a separator")

    now = now or datetime.now(timezone.utc)
    reports_dir = Path(reports_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = list(filenames) if filenames is not None else list(DEFAULT_BUNDLE_FILES)

    included_files: list[dict[str, Any]] = []
    missing_files: list[str] = []
    warnings: list[str] = []

    zip_path = output_dir / f"{session_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in candidates:
            path = reports_dir / filename
            if not path.is_file():
                missing_files.append(filename)
                continue
            data = path.read_bytes()
            archive.writestr(f"{session_name}/{filename}", data)
            included_files.append(
                {
                    "name": filename,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )

    required_missing = [name for name in REQUIRED_REPORTS.values() if name in missing_files]
    if required_missing:
        warnings.append(f"required reports missing from bundle: {', '.join(required_missing)}")
    if not included_files:
        warnings.append("bundle is empty: no report files were found in the reports directory")

    dashboard = _read_json(reports_dir / DEFAULT_OPERATOR_DASHBOARD_JSON)
    final_operator_status = None
    dashboard_blocking: list[str] = []
    dashboard_warnings: list[str] = []
    if isinstance(dashboard, dict):
        final_operator_status = dashboard.get("final_operator_status")
        dashboard_blocking = [str(reason) for reason in dashboard.get("blocking_reasons") or []]
        dashboard_warnings = [str(warning) for warning in dashboard.get("warnings") or []]
    else:
        warnings.append("operator dashboard summary not found; final_operator_status unavailable (run scripts/operator_dashboard.py first)")

    manifest: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "session_name": session_name,
        "reports_dir": str(reports_dir),
        "included_files": included_files,
        "missing_files": missing_files,
        "zip_sha256": _sha256_file(zip_path),
        "final_operator_status": final_operator_status,
        "blocking_reasons": dashboard_blocking,
        "warnings": warnings + dashboard_warnings,
        "safety_flags": dict(SAFETY_FLAGS),
        "output_paths": {"zip": str(zip_path)},
    }

    manifest_json_path = output_dir / f"{session_name}_manifest.json"
    manifest_txt_path = output_dir / f"{session_name}_manifest.txt"
    manifest["output_paths"]["manifest_json"] = str(manifest_json_path)
    manifest["output_paths"]["manifest_txt"] = str(manifest_txt_path)
    manifest_json_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_txt_path.write_text(render_manifest_txt(manifest), encoding="utf-8")
    return manifest


def render_manifest_txt(manifest: dict[str, Any]) -> str:
    lines = [
        "PAPER SESSION BUNDLE MANIFEST (read-only, paper/demo only)",
        f"generated_at={manifest['generated_at']}",
        f"session_name={manifest['session_name']}",
        f"reports_dir={manifest['reports_dir']}",
        f"final_operator_status={manifest['final_operator_status'] or 'UNAVAILABLE'}",
        f"zip={manifest['output_paths']['zip']}",
        f"zip_sha256={manifest['zip_sha256']}",
        "",
        f"included files ({len(manifest['included_files'])}):",
    ]
    for entry in manifest["included_files"]:
        lines.append(f"  {entry['name']} sha256={entry['sha256']} size={entry['size_bytes']}")
    if not manifest["included_files"]:
        lines.append("  (none)")
    for label, key in (("missing files", "missing_files"), ("blocking reasons", "blocking_reasons"), ("warnings", "warnings")):
        lines.append("")
        lines.append(f"{label}:")
        values = manifest[key]
        if values:
            lines.extend(f"  - {value}" for value in values)
        else:
            lines.append("  (none)")
    lines.append("")
    lines.append("safety flags:")
    for name, value in sorted(manifest["safety_flags"].items()):
        lines.append(f"  {name}={str(value).lower()}")
    lines.append("")
    return "\n".join(lines)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
