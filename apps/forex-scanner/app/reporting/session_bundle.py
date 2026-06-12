"""Read-only paper/demo session bundle exporter.

This module packages existing report artifacts into an auditable archive. It
only reads report files and writes the requested bundle/manifest outputs; it
never imports broker terminals, never submits orders, never mutates ``.env``,
and never runs a daemon or trading loop.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.reporting.operator_dashboard import DEFAULT_OPERATOR_DASHBOARD_JSON, DEFAULT_OPERATOR_DASHBOARD_TXT

DEFAULT_BUNDLE_OUTPUT_DIRNAME = "bundles"

REQUIRED_PAPER_SESSION_REPORTS: tuple[str, ...] = (
    DEFAULT_OPERATOR_DASHBOARD_JSON,
    DEFAULT_OPERATOR_DASHBOARD_TXT,
    "local_mt5_realtime_validation.json",
    "local_mt5_realtime_validation.txt",
    "local_mt5_realtime_samples.csv",
    "realtime_command_center_summary.json",
    "realtime_command_center_report.txt",
    "realtime_paper_supervisor_summary.json",
    "realtime_paper_supervisor_report.txt",
    "realtime_paper_positions.json",
    "realtime_paper_positions.txt",
    "realtime_heartbeat.jsonl",
    "autonomous_scenario_suite.json",
    "autonomous_scenario_suite.txt",
)

OPTIONAL_PAPER_SESSION_REPORTS: tuple[str, ...] = (
    "autonomous_policy_report.json",
    "autonomous_policy_report.txt",
    "autonomous_readiness_report.json",
    "autonomous_readiness_report.txt",
    "autonomous_evidence_report.json",
    "autonomous_evidence_report.txt",
    "autonomous_recovery_plan.json",
    "autonomous_recovery_plan.txt",
)

DEFAULT_BUNDLE_FILES: tuple[str, ...] = REQUIRED_PAPER_SESSION_REPORTS + OPTIONAL_PAPER_SESSION_REPORTS

SAFETY_FLAGS: dict[str, object] = {
    "read_only_bundle": True,
    "paper_demo_only": True,
    "archive_export_only": True,
    "live_trading_enabled": False,
    "live_execution_allowed": False,
    "broker_live_execution_allowed": False,
    "broker_order_submission_allowed": False,
    "order_send_called": False,
    "env_mutation_performed": False,
    "terminal_api_required": False,
    "mt5_required": False,
    "daemon_started": False,
    "infinite_loop_started": False,
}

_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MANIFEST_JSON_MEMBER = "manifest.json"
_MANIFEST_TXT_MEMBER = "manifest.txt"


class PaperSessionBundleError(RuntimeError):
    """Base exception for paper session bundle export failures."""


class PaperSessionBundleStrictError(PaperSessionBundleError):
    """Raised when strict mode blocks export because required reports are missing."""


@dataclass(frozen=True)
class PaperSessionBundleConfig:
    """Configuration for a read-only paper/demo session bundle export."""

    reports_dir: Path
    output_dir: Path
    session_name: str
    include_optional: bool = True
    strict: bool = False
    required_files: tuple[str, ...] = REQUIRED_PAPER_SESSION_REPORTS
    optional_files: tuple[str, ...] = OPTIONAL_PAPER_SESSION_REPORTS
    generated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not _SESSION_NAME_RE.match(self.session_name):
            raise ValueError("session name must contain only letters, digits, '.', '_' or '-' and not start with a separator")
        _validate_expected_filenames((*self.required_files, *self.optional_files))

    @property
    def bundle_path(self) -> Path:
        return Path(self.output_dir) / f"{self.session_name}.zip"

    @property
    def manifest_json_path(self) -> Path:
        return Path(self.output_dir) / f"{self.session_name}_manifest.json"

    @property
    def manifest_txt_path(self) -> Path:
        return Path(self.output_dir) / f"{self.session_name}_manifest.txt"

    @property
    def expected_files(self) -> tuple[str, ...]:
        if self.include_optional:
            return (*self.required_files, *self.optional_files)
        return self.required_files


@dataclass(frozen=True)
class PaperSessionBundleFile:
    """Manifest entry for one included report artifact."""

    path: str
    archive_path: str
    sha256: str
    file_size_bytes: int
    modified_at: str
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.path,  # Backward-compatible alias for older callers.
            "archive_path": self.archive_path,
            "sha256": self.sha256,
            "file_size_bytes": self.file_size_bytes,
            "size_bytes": self.file_size_bytes,  # Backward-compatible alias for older callers.
            "modified_at": self.modified_at,
            "required": self.required,
        }


@dataclass(frozen=True)
class PaperSessionBundleManifest:
    """Auditable manifest for a paper/demo session bundle."""

    generated_at: str
    session_name: str
    reports_dir: str
    output_dir: str
    bundle_path: str
    manifest_json_path: str
    manifest_txt_path: str
    included_files: tuple[PaperSessionBundleFile, ...]
    missing_files: tuple[str, ...]
    optional_missing_files: tuple[str, ...]
    safety_flags: dict[str, object]
    final_operator_status: str | None
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "generated_at": self.generated_at,
            "session_name": self.session_name,
            "reports_dir": self.reports_dir,
            "output_dir": self.output_dir,
            "bundle_path": self.bundle_path,
            "manifest_json_path": self.manifest_json_path,
            "manifest_txt_path": self.manifest_txt_path,
            "included_files": [entry.to_dict() for entry in self.included_files],
            "missing_files": list(self.missing_files),
            "optional_missing_files": list(self.optional_missing_files),
            "safety_flags": dict(self.safety_flags),
            "final_operator_status": self.final_operator_status,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings": list(self.warnings),
        }
        payload["output_paths"] = {
            "zip": self.bundle_path,
            "manifest_json": self.manifest_json_path,
            "manifest_txt": self.manifest_txt_path,
        }
        return payload


class PaperSessionBundleService:
    """Build read-only paper/demo report bundles with JSON/TXT manifests."""

    def __init__(self, config: PaperSessionBundleConfig) -> None:
        self.config = config

    def export(self) -> PaperSessionBundleManifest:
        reports_dir = Path(self.config.reports_dir)
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_outputs_under_output_dir(output_dir)

        now = self.config.generated_at or datetime.now(timezone.utc)
        included: list[PaperSessionBundleFile] = []
        missing_required: list[str] = []
        missing_optional: list[str] = []
        warnings: list[str] = []

        for filename in self.config.expected_files:
            source_path = reports_dir / filename
            if not source_path.is_file():
                if filename in self.config.required_files:
                    missing_required.append(filename)
                else:
                    missing_optional.append(filename)
                continue
            included.append(self._build_file_entry(source_path, filename, required=filename in self.config.required_files))

        dashboard = _read_json(reports_dir / DEFAULT_OPERATOR_DASHBOARD_JSON)
        final_operator_status = None
        blocking_reasons: list[str] = []
        dashboard_warnings: list[str] = []
        dashboard_flags: dict[str, object] = {}
        if isinstance(dashboard, dict):
            final_operator_status = _optional_str(dashboard.get("final_operator_status"))
            blocking_reasons = [str(reason) for reason in dashboard.get("blocking_reasons") or []]
            dashboard_warnings = [str(warning) for warning in dashboard.get("warnings") or []]
            raw_flags = dashboard.get("safety_flags")
            if isinstance(raw_flags, dict):
                dashboard_flags = dict(raw_flags)
        else:
            warnings.append("operator dashboard summary not found; final_operator_status unavailable (run scripts/operator_dashboard.py first)")

        if missing_required:
            warnings.append(f"required reports missing from bundle: {', '.join(missing_required)}")
        if missing_optional:
            warnings.append(f"optional reports missing from bundle: {', '.join(missing_optional)}")
        if not included:
            warnings.append("bundle is empty: no report files were found in the reports directory")

        if self.config.strict and missing_required:
            raise PaperSessionBundleStrictError(f"strict mode blocked bundle export; missing required reports: {', '.join(missing_required)}")

        manifest = PaperSessionBundleManifest(
            generated_at=now.isoformat(),
            session_name=self.config.session_name,
            reports_dir=str(reports_dir),
            output_dir=str(output_dir),
            bundle_path=str(self.config.bundle_path),
            manifest_json_path=str(self.config.manifest_json_path),
            manifest_txt_path=str(self.config.manifest_txt_path),
            included_files=tuple(included),
            missing_files=tuple(missing_required),
            optional_missing_files=tuple(missing_optional),
            safety_flags={**SAFETY_FLAGS, "operator_dashboard_safety_flags": dashboard_flags},
            final_operator_status=final_operator_status,
            blocking_reasons=tuple(blocking_reasons),
            warnings=tuple(_dedupe([*warnings, *dashboard_warnings])),
        )

        manifest_dict = manifest.to_dict()
        manifest_json = json.dumps(manifest_dict, indent=2, sort_keys=True) + "\n"
        manifest_txt = render_manifest_txt(manifest_dict)
        self.config.manifest_json_path.write_text(manifest_json, encoding="utf-8")
        self.config.manifest_txt_path.write_text(manifest_txt, encoding="utf-8")
        self._write_zip(included, manifest_json, manifest_txt)
        return manifest

    def _build_file_entry(self, source_path: Path, filename: str, *, required: bool) -> PaperSessionBundleFile:
        data = source_path.read_bytes()
        return PaperSessionBundleFile(
            path=filename,
            archive_path=f"{self.config.session_name}/{filename}",
            sha256=hashlib.sha256(data).hexdigest(),
            file_size_bytes=len(data),
            modified_at=datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            required=required,
        )

    def _write_zip(self, included: Iterable[PaperSessionBundleFile], manifest_json: str, manifest_txt: str) -> None:
        reports_dir = Path(self.config.reports_dir)
        with zipfile.ZipFile(self.config.bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for entry in included:
                archive.write(reports_dir / entry.path, entry.archive_path)
            archive.writestr(f"{self.config.session_name}/{_MANIFEST_JSON_MEMBER}", manifest_json)
            archive.writestr(f"{self.config.session_name}/{_MANIFEST_TXT_MEMBER}", manifest_txt)

    def _ensure_outputs_under_output_dir(self, output_dir: Path) -> None:
        output_root = output_dir.resolve()
        for path in (self.config.bundle_path, self.config.manifest_json_path, self.config.manifest_txt_path):
            if not path.resolve().is_relative_to(output_root):
                raise ValueError(f"generated bundle path escapes output directory: {path}")


def build_paper_session_bundle(
    reports_dir: Path,
    output_dir: Path,
    session_name: str,
    *,
    filenames: tuple[str, ...] | list[str] | None = None,
    now: datetime | None = None,
    include_optional: bool = True,
    strict: bool = False,
) -> dict[str, Any]:
    """Create ``<session-name>.zip`` plus JSON/TXT manifests from local reports."""
    if filenames is None:
        required_files = REQUIRED_PAPER_SESSION_REPORTS
        optional_files = OPTIONAL_PAPER_SESSION_REPORTS
    else:
        required_files = tuple(filenames)
        optional_files = ()
    config = PaperSessionBundleConfig(
        reports_dir=Path(reports_dir),
        output_dir=Path(output_dir),
        session_name=session_name,
        include_optional=include_optional,
        strict=strict,
        required_files=required_files,
        optional_files=optional_files,
        generated_at=now,
    )
    return PaperSessionBundleService(config).export().to_dict()


def render_manifest_txt(manifest: PaperSessionBundleManifest | dict[str, Any]) -> str:
    payload = manifest.to_dict() if isinstance(manifest, PaperSessionBundleManifest) else manifest
    lines = [
        "PAPER SESSION BUNDLE MANIFEST (read-only, paper/demo only)",
        f"generated_at={payload['generated_at']}",
        f"session_name={payload['session_name']}",
        f"reports_dir={payload['reports_dir']}",
        f"output_dir={payload['output_dir']}",
        f"bundle_path={payload['bundle_path']}",
        f"manifest_json_path={payload['manifest_json_path']}",
        f"manifest_txt_path={payload['manifest_txt_path']}",
        f"final_operator_status={payload['final_operator_status'] or 'UNAVAILABLE'}",
        "",
        f"included files ({len(payload['included_files'])}):",
    ]
    for entry in payload["included_files"]:
        lines.append(f"  {entry['path']} sha256={entry['sha256']} size={entry['file_size_bytes']} archive_path={entry['archive_path']}")
    if not payload["included_files"]:
        lines.append("  (none)")

    for label, key in (
        ("missing required files", "missing_files"),
        ("optional missing files", "optional_missing_files"),
        ("blocking reasons", "blocking_reasons"),
        ("warnings", "warnings"),
    ):
        lines.append("")
        lines.append(f"{label}:")
        values = payload[key]
        if values:
            lines.extend(f"  - {value}" for value in values)
        else:
            lines.append("  (none)")
    lines.append("")
    lines.append("safety flags:")
    for name, value in sorted(payload["safety_flags"].items()):
        lines.append(f"  {name}={json.dumps(value, sort_keys=True)}")
    lines.append("")
    return "\n".join(lines)


def _validate_expected_filenames(filenames: Iterable[str]) -> None:
    for filename in filenames:
        path = Path(filename)
        if path.is_absolute() or ".." in path.parts or str(path) != filename:
            raise ValueError(f"expected report filename must be a safe relative path: {filename}")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
