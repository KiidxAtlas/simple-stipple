"""Centralized logging configuration."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from src.paths import user_log_dir

_LOG_FILE_NAME = "simple-stipple.log"
_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str | int = "INFO") -> Path:
    """Install rotating-file + console handlers on the root logger.

    Returns the active log file path. Idempotent.
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on re-init (e.g. tests).
    if any(getattr(h, "_simple_stipple_handler", False) for h in root.handlers):
        for h in root.handlers:
            h.setLevel(level)
        return user_log_dir() / _LOG_FILE_NAME

    log_path = user_log_dir() / _LOG_FILE_NAME
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        file_handler._simple_stipple_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError:
        # Disk unavailable — fall back to stderr only.
        pass

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.setLevel(level)
    console._simple_stipple_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)

    # Quiet noisy third-party libs by default.
    for noisy in ("PIL", "matplotlib", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging initialized → %s", log_path)
    return log_path


__all__ = ["configure_logging"]
