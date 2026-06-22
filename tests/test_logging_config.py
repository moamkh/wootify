"""Tests for logging configuration."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from unittest.mock import patch

from app.logging_config import configure_logging


def test_configure_logging_adds_rotating_file_handler(tmp_path):
    """A running backend should append logs to a rotating file handler."""
    log_file = tmp_path / "backend.log"

    with patch("app.logging_config.settings.LOG_FILE_PATH", str(log_file)):
        with patch("app.logging_config.settings.LOG_FILE_MAX_BYTES", 1024):
            with patch("app.logging_config.settings.LOG_FILE_BACKUP_COUNT", 2):
                # Reset root handlers so configure_logging adds a fresh one.
                root = logging.getLogger()
                original_handlers = list(root.handlers)
                for handler in original_handlers:
                    root.removeHandler(handler)

                try:
                    configure_logging()

                    file_handlers = [
                        h for h in root.handlers
                        if isinstance(h, logging.handlers.RotatingFileHandler)
                    ]
                    assert len(file_handlers) == 1
                    handler = file_handlers[0]
                    assert handler.baseFilename == str(log_file)
                    assert handler.mode == "a"
                    assert handler.maxBytes == 1024
                    assert handler.backupCount == 2

                    # Verify a log record actually reaches the file.
                    root.info("test log entry from configure_logging")
                    assert log_file.exists()
                    content = log_file.read_text(encoding="utf-8")
                    assert "test log entry from configure_logging" in content
                finally:
                    for handler in list(root.handlers):
                        handler.close()
                        root.removeHandler(handler)
                    for handler in original_handlers:
                        root.addHandler(handler)


def test_configure_logging_relative_path_resolves_to_repo_root(tmp_path):
    """A relative LOG_FILE_PATH is resolved against the repo root."""
    from app.config import repo_root

    with patch("app.logging_config.settings.LOG_FILE_PATH", "sub_dir/test_backend.log"):
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        for handler in original_handlers:
            root.removeHandler(handler)

        try:
            configure_logging()

            file_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(file_handlers) == 1
            expected_path = str(repo_root / "sub_dir" / "test_backend.log")
            assert file_handlers[0].baseFilename == expected_path
        finally:
            for handler in list(root.handlers):
                handler.close()
                root.removeHandler(handler)
            for handler in original_handlers:
                root.addHandler(handler)
