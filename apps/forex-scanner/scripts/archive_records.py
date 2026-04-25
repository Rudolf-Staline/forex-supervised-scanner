"""Archive, verify, and restore operational evidence packages locally."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.archive.retention import (
    build_rotation_plan,
    collect_retention_candidates,
    create_archive_package,
    inspect_archive_manifest,
    list_archive_manifests,
    restore_archive_for_review,
    verify_archive_package,
    write_archive_verification_result,
    write_restore_result,
)
from app.audit.integrity import AuditSealTrigger
from app.config.settings import load_settings
from app.reporting.archive import generate_archive_report
from app.storage.database import Database


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Retention, archive, rotation, and restore-for-review workflow.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidates = subparsers.add_parser("candidates", help="List records/files that exceed retention windows.")
    candidates.add_argument("--out", default=settings.retention_archive.report_output_dir)
    candidates.add_argument("--no-files", action="store_true", help="Skip filesystem report/export candidates.")

    create = subparsers.add_parser("create", help="Create a local archive package for retention candidates.")
    create.add_argument("--out", default=settings.retention_archive.archive_output_dir)
    create.add_argument("--report-out", default=settings.retention_archive.report_output_dir)
    create.add_argument("--label", default=None)
    create.add_argument("--no-files", action="store_true", help="Skip filesystem report/export candidates.")

    daily = subparsers.add_parser("daily", help="Create a daily audit seal and archive package.")
    daily.add_argument("--out", default=settings.retention_archive.archive_output_dir)
    daily.add_argument("--report-out", default=settings.retention_archive.report_output_dir)
    daily.add_argument("--label", default="daily")
    daily.add_argument("--no-files", action="store_true", help="Skip filesystem report/export candidates.")

    inventory = subparsers.add_parser("list", help="List available archive packages.")
    inventory.add_argument("--archive-dir", default=settings.retention_archive.archive_output_dir)
    inventory.add_argument("--out", default=settings.retention_archive.report_output_dir)

    inspect = subparsers.add_parser("inspect", help="Inspect one archive manifest.")
    inspect.add_argument("--archive", required=True)

    verify = subparsers.add_parser("verify", help="Verify one archive package.")
    verify.add_argument("--archive", required=True)
    verify.add_argument("--out", default=settings.retention_archive.report_output_dir)

    restore = subparsers.add_parser("restore-review", help="Restore an archive into an isolated review directory.")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--restore-dir", default=settings.retention_archive.restore_output_dir)
    restore.add_argument("--out", default=settings.retention_archive.report_output_dir)
    restore.add_argument("--overwrite", action="store_true")

    rotation = subparsers.add_parser("rotation-plan", help="Build a safe rotation plan without deleting protected evidence.")
    rotation.add_argument("--out", default=settings.retention_archive.report_output_dir)
    rotation.add_argument("--apply", action="store_true", help="Generate a non-dry-run plan. Active purge still requires config gates.")
    rotation.add_argument("--no-files", action="store_true", help="Skip filesystem report/export candidates.")

    args = parser.parse_args()
    database = Database(Path(args.db))

    if args.command == "candidates":
        evaluation = collect_retention_candidates(database, settings, include_files=not args.no_files)
        plan = build_rotation_plan(evaluation, settings.retention_archive)
        outputs = generate_archive_report(
            settings,
            evaluation,
            list_archive_manifests(_project_path(settings.retention_archive.archive_output_dir)),
            Path(args.out),
            rotation_plan=plan,
        )
        print(f"archive=candidates database={evaluation.total_database_candidates} files={evaluation.total_file_candidates}")
        _print_outputs(outputs)
        return

    if args.command in {"create", "daily"}:
        if args.command == "daily":
            seal = database.create_audit_seal(
                trigger_type=AuditSealTrigger.DAILY,
                trigger_id=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                notes="daily archive workflow seal",
                now=datetime.now(timezone.utc),
            )
            if seal is not None:
                print(f"audit_integrity=sealed seal_id={seal.seal_id} range={seal.start_sequence}-{seal.end_sequence}")
        manifest, archive_path = create_archive_package(
            database,
            settings,
            output_dir=Path(args.out),
            label=args.label,
            include_files=not args.no_files,
            project_root=PROJECT_ROOT,
        )
        verification = verify_archive_package(archive_path)
        evaluation = collect_retention_candidates(database, settings, include_files=not args.no_files)
        plan = build_rotation_plan(evaluation, settings.retention_archive)
        outputs = generate_archive_report(
            settings,
            evaluation,
            list_archive_manifests(Path(args.out)),
            Path(args.report_out),
            rotation_plan=plan,
            verification=verification,
        )
        write_archive_verification_result(verification, Path(args.report_out))
        print(
            "archive=created "
            f"archive_id={manifest.archive_id} records={len(manifest.records)} files={manifest.file_count} "
            f"verification={verification.status.value} package={archive_path}"
        )
        _print_outputs(outputs)
        if verification.status.value != "passed":
            raise SystemExit(1)
        return

    if args.command == "list":
        archive_dir = Path(args.archive_dir)
        manifests = list_archive_manifests(archive_dir)
        evaluation = collect_retention_candidates(database, settings)
        outputs = generate_archive_report(settings, evaluation, manifests, Path(args.out))
        print(f"archive=list count={len(manifests)} archive_dir={archive_dir}")
        _print_outputs(outputs)
        return

    if args.command == "inspect":
        manifest = inspect_archive_manifest(Path(args.archive))
        if manifest is None:
            print("archive=inspect_failed reason=manifest_unreadable")
            raise SystemExit(1)
        print(manifest.model_dump_json(indent=2))
        return

    if args.command == "verify":
        result = verify_archive_package(Path(args.archive))
        outputs = write_archive_verification_result(result, Path(args.out))
        print(f"archive=verified status={result.status.value} checked_records={result.checked_records} issues={len(result.issues)}")
        _print_outputs(outputs)
        if result.status.value != "passed":
            raise SystemExit(1)
        return

    if args.command == "restore-review":
        result = restore_archive_for_review(Path(args.archive), Path(args.restore_dir), overwrite=args.overwrite)
        outputs = write_restore_result(result, Path(args.out))
        print(
            "archive=restored_for_review "
            f"status={result.status} verification={result.verification_status.value} restore_path={result.restore_path}"
        )
        _print_outputs(outputs)
        if result.status not in {"restored_for_review"}:
            raise SystemExit(1)
        return

    if args.command == "rotation-plan":
        evaluation = collect_retention_candidates(database, settings, include_files=not args.no_files)
        plan = build_rotation_plan(evaluation, settings.retention_archive, dry_run=not args.apply)
        outputs = generate_archive_report(
            settings,
            evaluation,
            list_archive_manifests(_project_path(settings.retention_archive.archive_output_dir)),
            Path(args.out),
            rotation_plan=plan,
        )
        print(
            "archive=rotation_plan "
            f"dry_run={plan.dry_run} db_records={plan.database_records_to_archive} files={plan.files_to_rotate}"
        )
        _print_outputs(outputs)
        return


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _print_outputs(outputs: dict[str, Path]) -> None:
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()

