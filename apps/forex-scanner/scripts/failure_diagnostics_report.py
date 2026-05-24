"""CLI for failure diagnostics from existing reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.ops.failure_diagnostics import (
    FailureDiagnosticsOptions,
    build_failure_diagnostics_summary,
    export_failure_diagnostics_json,
    export_failure_diagnostics_txt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate failure diagnostics summary from existing report artifacts.")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-txt", action="store_true")
    parser.add_argument("--show-suggestions", action="store_true")
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    summary = build_failure_diagnostics_summary(
        FailureDiagnosticsOptions(reports_dir=reports_dir, show_suggestions=args.show_suggestions)
    )

    print(f"severity={summary['severity']}")
    print(summary["execution_authorization"])

    if args.export_json:
        json_path = export_failure_diagnostics_json(summary, reports_dir)
        print(f"json_export={json_path}")
    if args.export_txt:
        txt_path = export_failure_diagnostics_txt(summary, reports_dir)
        print(f"txt_export={txt_path}")


if __name__ == "__main__":
    main()
