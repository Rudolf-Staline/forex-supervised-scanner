"""Read-only paper/demo post-session review orchestration.

The review service composes existing offline report tools: operator dashboard,
paper performance analytics, and optional paper session bundle export. It never
imports MT5, never submits broker orders, never mutates ``.env``, and never runs
trading logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.reporting.operator_dashboard import (
    STATUS_READY as OPERATOR_STATUS_READY,
    STATUS_WARN as OPERATOR_STATUS_WARN,
    build_operator_dashboard,
    export_operator_dashboard_json,
    export_operator_dashboard_txt,
)
from app.reporting.paper_performance import PaperPerformanceConfig, PaperPerformanceService
from app.reporting.session_bundle import PaperSessionBundleError, build_paper_session_bundle

DEFAULT_REVIEW_JSON = "paper_session_review_summary.json"
DEFAULT_REVIEW_TXT = "paper_session_review_report.txt"
DEFAULT_SESSION_NAME = "paper-session-review"

STATUS_READY = "PAPER_SESSION_REVIEW_READY"
STATUS_WARN = "PAPER_SESSION_REVIEW_WARN"
STATUS_INCOMPLETE = "PAPER_SESSION_REVIEW_INCOMPLETE"
STATUS_BLOCKED = "PAPER_SESSION_REVIEW_BLOCKED"

_OPERATOR_BLOCKED_STATUSES = {"OPERATOR_BLOCKED"}
_OPERATOR_INCOMPLETE_STATUSES = {"OPERATOR_REPORTS_MISSING", "OPERATOR_REPORTS_STALE"}
_PERFORMANCE_BLOCKED_STATUSES = {"PAPER_PERFORMANCE_BLOCKED_UNSAFE_FLAGS"}
_PERFORMANCE_INCOMPLETE_STATUSES = {"PAPER_PERFORMANCE_INCOMPLETE_DATA"}
_PERFORMANCE_WARN_STATUSES = {"PAPER_PERFORMANCE_WARN", "PAPER_PERFORMANCE_NO_TRADES"}

UNSAFE_FLAG_KEYS = {
    "live_trading_enabled",
    "live_execution_allowed",
    "broker_live_execution_allowed",
    "broker_order_submission_allowed",
    "mt5_order_execution_allowed",
    "order_send_called",
    "env_mutation_performed",
}

SAFETY_FLAGS: dict[str, object] = {
    "read_only_review": True,
    "paper_demo_only": True,
    "post_session_review_only": True,
    "live_trading_enabled": False,
    "live_execution_allowed": False,
    "broker_live_execution_allowed": False,
    "broker_order_submission_allowed": False,
    "order_send_called": False,
    "env_mutation_performed": False,
    "mt5_required": False,
    "daemon_started": False,
    "infinite_loop_started": False,
}


@dataclass(frozen=True)
class PaperSessionReviewConfig:
    """Configuration for the offline post-session review."""

    reports_dir: Path
    export_json: bool = False
    export_txt: bool = False
    export_bundle: bool = False
    bundle_output_dir: Path | None = None
    session_name: str = DEFAULT_SESSION_NAME
    strict: bool = False
    max_age_hours: float = 24.0
    now: datetime | None = None


@dataclass
class PaperSessionReviewSummary:
    """Compact operator handoff summary for one paper/demo session."""

    generated_at: str
    reports_dir: str
    final_review_status: str
    operator_status: str | None
    performance_status: str | None
    bundle_status: str
    missing_reports: list[str]
    stale_reports: list[str]
    blocking_reasons: list[str]
    warnings: list[str]
    recommended_next_actions: list[str]
    safety_flags: dict[str, Any]
    output_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class PaperSessionReviewService:
    """Build an offline paper/demo session review from existing report artifacts."""

    def __init__(self, config: PaperSessionReviewConfig) -> None:
        self.config = config
        self.reports_dir = Path(config.reports_dir)
        self.now = config.now or datetime.now(timezone.utc)

    def build_summary(self) -> PaperSessionReviewSummary:
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        dashboard = build_operator_dashboard(
            self.reports_dir,
            now=self.now,
            max_age_hours=self.config.max_age_hours,
        )
        write_component_artifacts = self.config.export_json or self.config.export_txt or self.config.export_bundle
        output_paths: dict[str, str] = {}

        if write_component_artifacts:
            dashboard_json = export_operator_dashboard_json(dashboard, self.reports_dir)
            dashboard_txt = export_operator_dashboard_txt(dashboard, self.reports_dir)
            output_paths["operator_dashboard_json"] = str(dashboard_json)
            output_paths["operator_dashboard_txt"] = str(dashboard_txt)

        performance = PaperPerformanceService(
            PaperPerformanceConfig(
                reports_dir=self.reports_dir,
                export_json=write_component_artifacts,
                export_txt=write_component_artifacts,
                strict=self.config.strict,
                now=self.now,
                stale_after_hours=self.config.max_age_hours,
            )
        ).build_summary()
        output_paths.update({f"paper_performance_{key}": value for key, value in performance.output_paths.items()})

        bundle_status = "NOT_REQUESTED"
        bundle_manifest: dict[str, Any] | None = None
        if self.config.export_bundle:
            try:
                bundle_manifest = build_paper_session_bundle(
                    self.reports_dir,
                    self.config.bundle_output_dir or (self.reports_dir / "bundles"),
                    self.config.session_name,
                    include_optional=True,
                    strict=self.config.strict,
                    now=self.now,
                )
                bundle_status = "EXPORTED"
                output_paths.update({f"bundle_{key}": value for key, value in bundle_manifest.get("output_paths", {}).items()})
            except PaperSessionBundleError as error:
                bundle_status = "BLOCKED"
                bundle_manifest = None
                output_paths["bundle_error"] = str(error)

        warnings = _dedupe(
            [
                *[str(item) for item in dashboard.get("warnings") or []],
                *[str(item) for item in performance.warnings],
                *([] if bundle_manifest is None else [str(item) for item in bundle_manifest.get("warnings") or []]),
            ]
        )
        blocking = _dedupe(
            [
                *[str(item) for item in dashboard.get("blocking_reasons") or []],
                *[str(item) for item in performance.blocking_reasons],
                *([] if bundle_manifest is None else [str(item) for item in bundle_manifest.get("blocking_reasons") or []]),
            ]
        )
        if bundle_status == "BLOCKED":
            blocking.append("paper session bundle export failed")

        safety_flags = _merge_safety_flags(
            dashboard.get("safety_flags"),
            performance.safety_flags,
            None if bundle_manifest is None else bundle_manifest.get("safety_flags"),
        )
        unsafe = [key for key, value in safety_flags.items() if key in UNSAFE_FLAG_KEYS and value is True]
        if unsafe:
            blocking.append("unsafe safety flags detected: " + ", ".join(sorted(unsafe)))

        final_status = _final_status(
            operator_status=_optional_str(dashboard.get("final_operator_status")),
            performance_status=performance.status,
            bundle_status=bundle_status,
            blocking=blocking,
            missing_reports=[str(item) for item in dashboard.get("missing_reports") or []],
            stale_reports=[str(item) for item in dashboard.get("stale_reports") or []],
            warnings=warnings,
        )

        recommended = _dedupe(
            [
                *[str(item) for item in dashboard.get("recommended_next_actions") or []],
                *_review_recommendations(final_status, bundle_status),
            ]
        )

        summary = PaperSessionReviewSummary(
            generated_at=self.now.isoformat(),
            reports_dir=str(self.reports_dir),
            final_review_status=final_status,
            operator_status=_optional_str(dashboard.get("final_operator_status")),
            performance_status=performance.status,
            bundle_status=bundle_status,
            missing_reports=[str(item) for item in dashboard.get("missing_reports") or []],
            stale_reports=[str(item) for item in dashboard.get("stale_reports") or []],
            blocking_reasons=_dedupe(blocking),
            warnings=warnings,
            recommended_next_actions=recommended,
            safety_flags=safety_flags,
            output_paths=output_paths,
        )

        if self.config.export_json or self.config.export_txt:
            self.export(summary)
        return summary

    def export(self, summary: PaperSessionReviewSummary) -> dict[str, Path]:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        if self.config.export_json:
            json_path = self.reports_dir / DEFAULT_REVIEW_JSON
            summary.output_paths["json"] = str(json_path)
            json_path.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            paths["json"] = json_path
        if self.config.export_txt:
            txt_path = self.reports_dir / DEFAULT_REVIEW_TXT
            summary.output_paths["txt"] = str(txt_path)
            txt_path.write_text(render_paper_session_review_txt(summary), encoding="utf-8")
            paths["txt"] = txt_path
        if self.config.export_json and "json" in paths:
            paths["json"].write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return paths


def build_paper_session_review(
    reports_dir: Path,
    *,
    export_json: bool = False,
    export_txt: bool = False,
    export_bundle: bool = False,
    bundle_output_dir: Path | None = None,
    session_name: str = DEFAULT_SESSION_NAME,
    strict: bool = False,
    max_age_hours: float = 24.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a read-only paper/demo post-session review summary."""
    summary = PaperSessionReviewService(
        PaperSessionReviewConfig(
            reports_dir=Path(reports_dir),
            export_json=export_json,
            export_txt=export_txt,
            export_bundle=export_bundle,
            bundle_output_dir=bundle_output_dir,
            session_name=session_name,
            strict=strict,
            max_age_hours=max_age_hours,
            now=now,
        )
    ).build_summary()
    return summary.to_dict()


def render_paper_session_review_txt(summary: PaperSessionReviewSummary | dict[str, Any]) -> str:
    payload = summary.to_dict() if isinstance(summary, PaperSessionReviewSummary) else summary
    lines = [
        "PAPER SESSION REVIEW (read-only, paper/demo only)",
        f"generated_at={payload['generated_at']}",
        f"reports_dir={payload['reports_dir']}",
        f"final_review_status={payload['final_review_status']}",
        f"operator_status={payload['operator_status'] or 'UNAVAILABLE'}",
        f"performance_status={payload['performance_status'] or 'UNAVAILABLE'}",
        f"bundle_status={payload['bundle_status']}",
    ]
    for label, key in (
        ("missing reports", "missing_reports"),
        ("stale reports", "stale_reports"),
        ("blocking reasons", "blocking_reasons"),
        ("warnings", "warnings"),
        ("recommended next actions", "recommended_next_actions"),
    ):
        lines.append("")
        lines.append(f"{label}:")
        values = payload.get(key) or []
        if values:
            lines.extend(f"  - {value}" for value in values)
        else:
            lines.append("  (none)")
    lines.append("")
    lines.append("output paths:")
    output_paths = payload.get("output_paths") or {}
    if output_paths:
        for key, value in sorted(output_paths.items()):
            lines.append(f"  {key}={value}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("safety flags:")
    for key, value in sorted((payload.get("safety_flags") or {}).items()):
        lines.append(f"  {key}={json.dumps(value, sort_keys=True)}")
    lines.append("")
    return "\n".join(lines)


def _final_status(
    *,
    operator_status: str | None,
    performance_status: str | None,
    bundle_status: str,
    blocking: list[str],
    missing_reports: list[str],
    stale_reports: list[str],
    warnings: list[str],
) -> str:
    if blocking or operator_status in _OPERATOR_BLOCKED_STATUSES or performance_status in _PERFORMANCE_BLOCKED_STATUSES or bundle_status == "BLOCKED":
        return STATUS_BLOCKED
    if operator_status in _OPERATOR_INCOMPLETE_STATUSES or performance_status in _PERFORMANCE_INCOMPLETE_STATUSES or missing_reports or stale_reports:
        return STATUS_INCOMPLETE
    if operator_status == OPERATOR_STATUS_WARN or performance_status in _PERFORMANCE_WARN_STATUSES or warnings:
        return STATUS_WARN
    if operator_status == OPERATOR_STATUS_READY:
        return STATUS_READY
    return STATUS_WARN


def _merge_safety_flags(*sources: object) -> dict[str, Any]:
    merged: dict[str, Any] = dict(SAFETY_FLAGS)
    for source in sources:
        if isinstance(source, dict):
            for key, value in source.items():
                if key.startswith("operator_dashboard_safety_flags"):
                    merged[key] = value
                elif key not in SAFETY_FLAGS:
                    merged[key] = value
                elif value is True:
                    merged[key] = True
    return merged


def _review_recommendations(final_status: str, bundle_status: str) -> list[str]:
    if final_status == STATUS_READY:
        return ["archive the review artifacts and perform manual operator review before any further action"]
    if final_status == STATUS_BLOCKED:
        return ["resolve blocking reasons before treating the session as review-ready"]
    if final_status == STATUS_INCOMPLETE:
        return ["regenerate missing or stale reports, then rerun paper_session_review.py"]
    if bundle_status == "NOT_REQUESTED":
        return ["rerun with --export-bundle to create an auditable session archive"]
    return ["review warnings before archiving the paper/demo session"]


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
