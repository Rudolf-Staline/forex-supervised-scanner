"""Structured JSON logging for the local app and scripts."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Mapping


class JsonFormatter(logging.Formatter):
    """Small JSON formatter that keeps logs machine-readable without dependencies."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extra = _extract_extra(record)
        if extra:
            payload["extra"] = extra
        return json.dumps(payload, sort_keys=True)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once for CLI scripts and Streamlit callbacks."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _extract_extra(record: logging.LogRecord) -> Mapping[str, object]:
    standard = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in standard and isinstance(value, (str, int, float, bool, type(None)))
    }

