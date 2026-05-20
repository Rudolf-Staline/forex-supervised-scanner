"""Small .env loader for local CLI scripts.

The project keeps secrets out of source control; this helper only copies
key/value pairs from a local .env file into process environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> Path | None:
    """Load simple KEY=VALUE entries from a local .env file.

    The parser intentionally supports only the syntax needed by this project:
    comments, blank lines, optional ``export`` prefixes, and quoted values.
    """

    env_path = Path(path)
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        clean_value = _strip_inline_comment(value.strip())
        if len(clean_value) >= 2 and clean_value[0] == clean_value[-1] and clean_value[0] in {"'", '"'}:
            clean_value = clean_value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = clean_value
    return env_path


def _strip_inline_comment(value: str) -> str:
    in_quote: str | None = None
    for idx, char in enumerate(value):
        if char in {"'", '"'}:
            in_quote = None if in_quote == char else char
        if char == "#" and in_quote is None:
            return value[:idx].strip()
    return value
