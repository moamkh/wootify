"""
Module Overview
---------------
Purpose: Application logging formatters and logging setup helpers.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

from app.config import settings


def _parse_log_level(level: str) -> int:
    """Parse log level."""
    if not level:
        return logging.INFO
    parsed = logging.getLevelName(level.strip().upper())
    return parsed if isinstance(parsed, int) else logging.INFO


class _AnsiColor:
    """Represents ansi color."""
    RESET = "\x1b[0m"
    CYAN = "\x1b[36m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    RED = "\x1b[31m"
    CRITICAL = "\x1b[41m\x1b[97m"


class ColorFormatter(logging.Formatter):
    """Represents color formatter."""
    def __init__(self, *args, use_color: bool = False, **kwargs):
        """Initialize the instance."""
        super().__init__(*args, **kwargs)
        self.use_color = bool(use_color)

    def format(self, record: logging.LogRecord) -> str:
        """Format."""
        msg = super().format(record)
        if not self.use_color:
            return msg

        if record.levelno >= logging.CRITICAL:
            color = _AnsiColor.CRITICAL
        elif record.levelno >= logging.ERROR:
            color = _AnsiColor.RED
        elif record.levelno >= logging.WARNING:
            color = _AnsiColor.YELLOW
        elif record.levelno >= logging.INFO:
            color = _AnsiColor.GREEN
        else:
            color = _AnsiColor.CYAN

        return f"{color}{msg}{_AnsiColor.RESET}"


def _stream_supports_color(stream: Optional[object]) -> bool:
    """Stream supports color."""
    if not stream:
        return False
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


def configure_logging() -> None:
    """Configure logging."""
    level = _parse_log_level(settings.LOG_LEVEL)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    # If an external runner didn't configure handlers (e.g., `python app/main.py`),
    # add a basic handler so logs are visible.
    if not root.handlers:
        root.addHandler(logging.StreamHandler(stream=sys.stderr))

    # Apply our formatter to existing stream handlers.
    for handler in root.handlers:
        if not isinstance(handler, logging.StreamHandler):
            continue

        use_color = bool(settings.LOG_COLOR) and (
            bool(settings.LOG_COLOR_FORCE) or _stream_supports_color(getattr(handler, "stream", None))
        )
        handler.setFormatter(ColorFormatter(fmt=fmt, datefmt=datefmt, use_color=use_color))

    # Keep noisy deps quieter. When redacting secrets, never enable verbose HTTP/SQL
    # logs (they may include full URLs with tokens or SQL params).
    if settings.LOG_REDACT_SECRETS:
        dep_level = logging.WARNING
    else:
        dep_level = logging.INFO if level <= logging.DEBUG else logging.WARNING

    logging.getLogger("httpx").setLevel(dep_level)
    logging.getLogger("httpcore").setLevel(dep_level)
    logging.getLogger("sqlalchemy.engine").setLevel(dep_level)

    # Make sure our app namespace follows the configured level.
    logging.getLogger("app").setLevel(level)

