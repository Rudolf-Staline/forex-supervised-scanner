"""Generate calibration CSV and Markdown reports from the local SQLite database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.reporting.calibration import generate_calibration_report


def main() -> None:
    """CLI entry point for calibration reporting."""

    settings = load_settings()
    parser = argparse.ArgumentParser(description="Generate scanner/backtest calibration reports.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    parser.add_argument("--out", default="reports/calibration", help="Output directory for CSV and Markdown reports.")
    parser.add_argument("--top-k", nargs="+", type=int, default=[5, 10, 20], help="Top-K cutoffs for precision/expectancy.")
    args = parser.parse_args()

    outputs = generate_calibration_report(Path(args.db), Path(args.out), args.top_k)
    print("calibration_report=ok")
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
