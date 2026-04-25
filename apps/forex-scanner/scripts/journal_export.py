"""Export the local paper trading journal and audit event trail."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.paper.journal import export_trading_journal
from app.storage.database import Database


def main() -> None:
    """CLI entry point for journal and event exports."""

    settings = load_settings()
    parser = argparse.ArgumentParser(description="Export paper trading journal and lifecycle events.")
    parser.add_argument("--db", default=str(settings.database_absolute_path), help="SQLite database path.")
    parser.add_argument("--out", default="reports/journal", help="Output directory.")
    args = parser.parse_args()

    database = Database(Path(args.db))
    orders = [*database.load_paper_orders(), *database.load_broker_orders()]
    blocks = database.load_paper_blocks()
    database.rebuild_trading_journal()
    outputs = export_trading_journal(orders, blocks, Path(args.out))
    print("journal_export=ok")
    for name, path in outputs.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
