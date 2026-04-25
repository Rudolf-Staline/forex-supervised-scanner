"""Verify, seal, report, and export tamper-evident audit records."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.audit.integrity import AuditProtectedRecordType, AuditSealTrigger
from app.config.settings import load_settings
from app.reporting.audit import export_audit_evidence_package, generate_audit_integrity_report
from app.storage.database import Database


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Audit integrity verification and evidence export.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify", help="Run audit integrity verification.")
    verify.add_argument("--from", dest="from_timestamp", default=None, help="Inclusive ISO timestamp lower bound.")
    verify.add_argument("--to", dest="to_timestamp", default=None, help="Inclusive ISO timestamp upper bound.")
    verify.add_argument("--record-type", action="append", default=None, choices=[item.value for item in AuditProtectedRecordType])
    verify.add_argument("--out", default=settings.audit_integrity.report_output_dir)
    verify.add_argument("--strict", dest="strict", action="store_true", default=settings.audit_integrity.strict_verification)
    verify.add_argument("--no-strict", dest="strict", action="store_false")

    status = subparsers.add_parser("status", help="Generate the latest integrity status report.")
    status.add_argument("--out", default=settings.audit_integrity.report_output_dir)

    seal = subparsers.add_parser("seal", help="Create an audit integrity seal.")
    seal.add_argument("--trigger", choices=[item.value for item in AuditSealTrigger], default=AuditSealTrigger.MANUAL.value)
    seal.add_argument("--trigger-id", default=None)
    seal.add_argument("--notes", default=None)
    seal.add_argument("--start-sequence", type=int, default=None)
    seal.add_argument("--end-sequence", type=int, default=None)
    seal.add_argument("--out", default=settings.audit_integrity.report_output_dir)

    export = subparsers.add_parser("export", help="Create an immutable-style local evidence package.")
    export.add_argument("--from", dest="from_timestamp", default=None, help="Inclusive ISO timestamp lower bound.")
    export.add_argument("--to", dest="to_timestamp", default=None, help="Inclusive ISO timestamp upper bound.")
    export.add_argument("--record-type", action="append", default=None, choices=[item.value for item in AuditProtectedRecordType])
    export.add_argument("--out", default=settings.audit_integrity.export_output_dir)
    export.add_argument("--strict", dest="strict", action="store_true", default=settings.audit_integrity.strict_verification)
    export.add_argument("--no-strict", dest="strict", action="store_false")

    args = parser.parse_args()
    database = Database(Path(args.db))

    if args.command == "verify":
        start = _parse_timestamp(args.from_timestamp)
        end = _parse_timestamp(args.to_timestamp)
        record_types = _parse_record_types(args.record_type)
        verification = database.verify_audit_integrity(
            start=start,
            end=end,
            record_types=record_types,
            strict=args.strict,
            save_result=True,
        )
        outputs = _write_report(database, Path(args.out))
        print(
            "audit_integrity=verified "
            f"status={verification.status.value} checked_records={verification.checked_records} "
            f"issues={len(verification.issues)}"
        )
        for name, path in outputs.items():
            print(f"{name}={path}")
        if verification.status.value != "passed":
            raise SystemExit(1)
        return

    if args.command == "status":
        outputs = _write_report(database, Path(args.out))
        latest = database.load_latest_audit_verification()
        status_value = latest.status.value if latest is not None else "not_verified"
        print(f"audit_integrity=status status={status_value} records={len(database.load_audit_integrity_records())}")
        for name, path in outputs.items():
            print(f"{name}={path}")
        return

    if args.command == "seal":
        trigger = AuditSealTrigger(args.trigger)
        seal_record = database.create_audit_seal(
            trigger_type=trigger,
            trigger_id=args.trigger_id,
            notes=args.notes,
            start_sequence=args.start_sequence,
            end_sequence=args.end_sequence,
            now=datetime.now(timezone.utc),
        )
        outputs = _write_report(database, Path(args.out))
        if seal_record is None:
            print("audit_integrity=seal_noop")
        else:
            print(
                "audit_integrity=sealed "
                f"seal_id={seal_record.seal_id} trigger={seal_record.trigger_type.value} "
                f"range={seal_record.start_sequence}-{seal_record.end_sequence}"
            )
        for name, path in outputs.items():
            print(f"{name}={path}")
        return

    if args.command == "export":
        start = _parse_timestamp(args.from_timestamp)
        end = _parse_timestamp(args.to_timestamp)
        record_types = _parse_record_types(args.record_type)
        verification = database.verify_audit_integrity(
            start=start,
            end=end,
            record_types=record_types,
            strict=args.strict,
            save_result=True,
        )
        records = database.load_audit_integrity_records(start=start, end=end, record_types=record_types)
        seals = database.load_audit_seals()
        export_package, outputs = export_audit_evidence_package(
            records,
            _filter_seals_for_records(seals, records),
            verification,
            Path(args.out),
            scope_from=start,
            scope_to=end,
            record_types=record_types,
        )
        database.save_audit_export_package(export_package)
        print(
            "audit_integrity=exported "
            f"export_id={export_package.export_id} status={verification.status.value} "
            f"records={export_package.record_count} package_hash={export_package.package_hash}"
        )
        for name, path in outputs.items():
            print(f"{name}={path}")
        return


def _write_report(database: Database, output_dir: Path) -> dict[str, Path]:
    return generate_audit_integrity_report(
        database.load_audit_integrity_records(),
        database.load_audit_seals(),
        database.load_audit_verification_runs(),
        database.load_audit_export_packages(),
        output_dir,
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_record_types(values: list[str] | None) -> list[AuditProtectedRecordType] | None:
    if not values:
        return None
    return [AuditProtectedRecordType(value) for value in values]


def _filter_seals_for_records(seals: list, records: list) -> list:
    if not records:
        return []
    start_sequence = min(record.sequence_number for record in records)
    end_sequence = max(record.sequence_number for record in records)
    return [
        seal
        for seal in seals
        if seal.end_sequence >= start_sequence and seal.start_sequence <= end_sequence
    ]


if __name__ == "__main__":
    main()
