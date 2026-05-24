"""CLI pour générer un audit de maintenance du dépôt (lecture seule)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ops.repository_audit import build_repository_audit, export_report_json, export_report_txt


def main() -> None:
    parser = argparse.ArgumentParser(description="Repository maintenance audit (static scan only).")
    parser.add_argument("--root", default=".", help="Racine du sous-projet forex-scanner")
    parser.add_argument("--export-json", action="store_true", help="Exporter reports/repository_maintenance_audit.json")
    parser.add_argument("--export-txt", action="store_true", help="Exporter reports/repository_maintenance_audit.txt")
    parser.add_argument("--show-suggestions", action="store_true", help="Afficher les suggestions dans stdout")
    args = parser.parse_args()

    root = (PROJECT_ROOT / args.root).resolve()
    report = build_repository_audit(root=root)

    if args.export_json:
        path = export_report_json(report, root / "reports")
        print(f"json_export={path}")
    if args.export_txt:
        path = export_report_txt(report, root / "reports")
        print(f"txt_export={path}")

    print(f"maintenance_status={report.maintenance_status}")
    print(f"scripts_count={report.scripts_count}")
    print(f"tests_count={report.tests_count}")
    print(f"docs_count={report.docs_count}")

    if args.show_suggestions:
        print("suggestions=")
        for item in report.suggestions:
            print(f"- {item}")


if __name__ == "__main__":
    main()
