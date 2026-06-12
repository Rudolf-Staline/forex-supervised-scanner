#!/usr/bin/env python
"""Export an auditable paper session bundle from local report artifacts.

Read-only and paper/demo only: no trading logic, no MT5, no order_send call,
no .env mutation. Packages existing reports into a zip with a checksummed
manifest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.reporting.session_bundle import build_paper_session_bundle, render_manifest_txt

SAFETY_BANNER = "SAFETY: paper session bundle export is read-only and paper/demo only; no trading logic, no MT5, no order_send, no .env mutation."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package local paper/demo reports into an auditable bundle (read-only)")
    parser.add_argument("--reports-dir", default="reports", help="Directory containing report artifacts")
    parser.add_argument("--output-dir", default="reports/bundles", help="Directory for the bundle zip and manifests")
    parser.add_argument("--session-name", required=True, help="Bundle name (letters, digits, '.', '_', '-')")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when the bundle would be empty")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(SAFETY_BANNER)

    try:
        manifest = build_paper_session_bundle(Path(args.reports_dir), Path(args.output_dir), args.session_name)
    except ValueError as error:
        print(f"error: {error}")
        return 2

    print(render_manifest_txt(manifest))
    for label, path in manifest["output_paths"].items():
        print(f"{label}={path}")

    if args.strict and not manifest["included_files"]:
        print("strict mode: failing because the bundle is empty")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
