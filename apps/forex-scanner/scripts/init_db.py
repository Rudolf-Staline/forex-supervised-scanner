"""Initialize the local SQLite database for the Forex scanner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import load_settings
from app.storage.database import initialize_database
from app.utils.logging import configure_logging


def main() -> None:
    """Create SQLite tables at the configured or supplied path."""

    parser = argparse.ArgumentParser(description="Initialize the Forex scanner SQLite database.")
    parser.add_argument("--path", type=Path, default=None, help="Optional database path override.")
    args = parser.parse_args()

    configure_logging()
    settings = load_settings()
    path = args.path or settings.database_absolute_path
    initialize_database(path)
    print(f"Initialized database at {path}")


if __name__ == "__main__":
    main()
