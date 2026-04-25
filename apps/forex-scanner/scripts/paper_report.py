"""Generate paper portfolio reports from the local SQLite database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.paper.reporting import generate_paper_portfolio_report
from app.storage.database import Database


def main() -> None:
    """CLI entry point for paper portfolio reporting."""

    settings = load_settings()
    parser = argparse.ArgumentParser(description="Generate local paper portfolio reports.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    parser.add_argument("--out", default="reports/paper", help="Output directory.")
    args = parser.parse_args()

    database = Database(Path(args.db))
    outputs = generate_paper_portfolio_report(database.load_paper_orders(), database.load_paper_blocks(), Path(args.out))
    print("paper_report=ok")
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
