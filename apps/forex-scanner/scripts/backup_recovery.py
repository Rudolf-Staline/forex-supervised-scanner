"""Local backup, restore, and service-continuity workflow."""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backup.recovery import (
    create_backup_package,
    inspect_backup_manifest,
    list_backup_manifests,
    load_recovery_state,
    restore_backup_to_active,
    restore_backup_to_review,
    validate_recovered_state,
    verify_backup_package,
)
from app.config.settings import load_settings
from app.execution.models import TradeEvent, TradeEventType
from app.reporting.backup import generate_backup_recovery_report
from app.storage.database import Database


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Local backup, restore, and disaster-recovery utilities.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a structured local backup package.")
    create.add_argument("--out", default=settings.backup_recovery.backup_output_dir)
    create.add_argument("--report-out", default=settings.backup_recovery.report_output_dir)
    create.add_argument("--label", default=None)
    create.add_argument("--reason", default=None)
    create.add_argument("--include-reports", action="store_true")
    create.add_argument("--pre-maintenance", action="store_true")

    inventory = subparsers.add_parser("list", help="List local backup packages.")
    inventory.add_argument("--backup-dir", default=settings.backup_recovery.backup_output_dir)
    inventory.add_argument("--out", default=settings.backup_recovery.report_output_dir)

    inspect = subparsers.add_parser("inspect", help="Inspect one backup manifest.")
    inspect.add_argument("--backup", required=True)

    verify = subparsers.add_parser("verify", help="Verify one backup package.")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--out", default=settings.backup_recovery.report_output_dir)

    restore_review = subparsers.add_parser("restore-review", help="Restore a backup into a review/staging directory.")
    restore_review.add_argument("--backup", required=True)
    restore_review.add_argument("--restore-dir", default=settings.backup_recovery.restore_review_dir)
    restore_review.add_argument("--out", default=settings.backup_recovery.report_output_dir)
    restore_review.add_argument("--overwrite", action="store_true")

    restore_active = subparsers.add_parser("restore-active", help="Explicitly restore the active SQLite state.")
    restore_active.add_argument("--backup", required=True)
    restore_active.add_argument("--target-db", default=str(settings.database_absolute_path))
    restore_active.add_argument("--out", default=settings.backup_recovery.report_output_dir)
    restore_active.add_argument("--confirm-active-restore", action="store_true")
    restore_active.add_argument("--allow-active-restore", action="store_true", help="Explicit one-shot operator override for local active restore.")

    post_restore = subparsers.add_parser("post-restore-check", help="Run post-restore/startup continuity validation.")
    post_restore.add_argument("--db", default=str(settings.database_absolute_path))
    post_restore.add_argument("--out", default=settings.backup_recovery.report_output_dir)

    report = subparsers.add_parser("report", help="Generate backup and recovery reports.")
    report.add_argument("--backup-dir", default=settings.backup_recovery.backup_output_dir)
    report.add_argument("--out", default=settings.backup_recovery.report_output_dir)

    args = parser.parse_args()

    if args.command == "create":
        database = Database(Path(args.db))
        manifest, backup_path = create_backup_package(
            database,
            settings,
            output_dir=Path(args.out),
            label=args.label,
            reason=args.reason,
            pre_maintenance=args.pre_maintenance,
            include_reports=args.include_reports or None,
            project_root=PROJECT_ROOT,
        )
        verification = verify_backup_package(backup_path) if settings.backup_recovery.verify_after_backup else None
        outputs = generate_backup_recovery_report(
            settings,
            list_backup_manifests(Path(args.out)),
            Path(args.report_out),
            latest_verification=verification,
            latest_recovery_validation=load_recovery_state(settings),
        )
        print(
            "backup=created "
            f"backup_id={manifest.backup_id} files={len(manifest.files)} "
            f"sqlite={manifest.sqlite_integrity_status} audit={manifest.audit_verification_status or 'not_run'} "
            f"package={backup_path}"
        )
        _print_outputs(outputs)
        if verification is not None and verification.status.value != "passed":
            raise SystemExit(1)
        return

    if args.command == "list":
        manifests = list_backup_manifests(Path(args.backup_dir))
        outputs = generate_backup_recovery_report(settings, manifests, Path(args.out), latest_recovery_validation=load_recovery_state(settings))
        print(f"backup=list count={len(manifests)} backup_dir={args.backup_dir}")
        _print_outputs(outputs)
        return

    if args.command == "inspect":
        manifest = inspect_backup_manifest(Path(args.backup))
        if manifest is None:
            print("backup=inspect_failed reason=manifest_unreadable")
            raise SystemExit(1)
        print(manifest.model_dump_json(indent=2))
        return

    if args.command == "verify":
        result = verify_backup_package(Path(args.backup))
        outputs = generate_backup_recovery_report(
            settings,
            list_backup_manifests(Path(args.backup).parent),
            Path(args.out),
            latest_verification=result,
            latest_recovery_validation=load_recovery_state(settings),
        )
        print(f"backup=verified status={result.status.value} checked_files={result.checked_files} issues={len(result.issues)}")
        _print_outputs(outputs)
        if result.status.value != "passed":
            raise SystemExit(1)
        return

    if args.command == "restore-review":
        result, validation = restore_backup_to_review(
            Path(args.backup),
            Path(args.restore_dir),
            settings,
            overwrite=args.overwrite or settings.backup_recovery.restore_overwrite_existing,
        )
        outputs = generate_backup_recovery_report(
            settings,
            list_backup_manifests(Path(args.backup).parent),
            Path(args.out),
            latest_restore=result,
            latest_recovery_validation=validation or load_recovery_state(settings),
        )
        print(
            "backup=restore_review "
            f"status={result.status} verification={result.verification_status.value} restore_path={result.restore_path}"
        )
        _print_outputs(outputs)
        if result.status != "restored_for_review":
            raise SystemExit(1)
        return

    if args.command == "restore-active":
        adjusted = settings.model_copy(deep=True)
        if args.allow_active_restore:
            adjusted.backup_recovery.allow_active_restore = True
        result, validation = restore_backup_to_active(
            Path(args.backup),
            Path(args.target_db),
            adjusted,
            confirm=args.confirm_active_restore,
            project_root=PROJECT_ROOT,
        )
        outputs = generate_backup_recovery_report(
            adjusted,
            list_backup_manifests(Path(args.backup).parent),
            Path(args.out),
            latest_restore=result,
            latest_recovery_validation=validation,
        )
        print(
            "backup=restore_active "
            f"status={result.status} mode={result.mode.value} safety_backup={result.safety_backup_path or 'none'}"
        )
        _print_outputs(outputs)
        if result.status not in {"active_restored_pending_validation"}:
            raise SystemExit(1)
        return

    if args.command == "post-restore-check":
        db_path = Path(args.db)
        _journal_recovery_event(db_path, "post_restore_validation_started")
        validation = validate_recovered_state(db_path, settings, save_state=True, project_root=PROJECT_ROOT)
        _journal_recovery_event(db_path, f"post_restore_validation_{validation.status.value}")
        outputs = generate_backup_recovery_report(
            settings,
            list_backup_manifests(_project_path(settings.backup_recovery.backup_output_dir)),
            Path(args.out),
            latest_recovery_validation=validation,
        )
        print(
            "backup=post_restore_check "
            f"status={validation.status.value} mode={validation.mode.value} blocked={validation.sensitive_actions_blocked}"
        )
        _print_outputs(outputs)
        if validation.status.value == "failed":
            raise SystemExit(1)
        return

    if args.command == "report":
        outputs = generate_backup_recovery_report(
            settings,
            list_backup_manifests(Path(args.backup_dir)),
            Path(args.out),
            latest_recovery_validation=load_recovery_state(settings),
        )
        print(f"backup=report summary={outputs['summary_markdown']}")
        _print_outputs(outputs)
        return


def _journal_recovery_event(database_path: Path, status: str) -> None:
    try:
        database = Database(database_path)
        database.save_trade_events(
            [
                TradeEvent(
                    event_id=str(uuid.uuid4()),
                    trade_id="backup-recovery",
                    event_type=TradeEventType.BROKER_RECOVERY_ACTION,
                    occurred_at=datetime.now(timezone.utc),
                    symbol="SYSTEM",
                    status=status,
                    reason="backup/recovery continuity validation event",
                    payload={"database_path": str(database_path)},
                )
            ]
        )
    except Exception:
        return


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _print_outputs(outputs: dict[str, Path]) -> None:
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()

