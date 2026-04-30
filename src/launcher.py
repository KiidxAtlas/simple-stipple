"""Packaged launcher entry point for Simple Stipple."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.app import App
from src.error_reporting import install_excepthook, install_toast
from src.logging_config import configure_logging
from src.single_instance import SingleInstanceGuard
from src.ui.style.theme import apply_dark_theme

_LOG = logging.getLogger(__name__)


def _resolve_icon_path() -> Path | None:
    bundled_root = getattr(sys, "_MEIPASS", None)
    candidates = [Path(__file__).parent.parent / "assets" / "icon.png"]
    if bundled_root:
        candidates.append(Path(bundled_root) / "assets" / "icon.png")

    for path in candidates:
        if path.exists():
            return path
    _LOG.warning("Application icon not found in any candidate path")
    return None


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="simple-stipple", add_help=True)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--allow-multi-instance",
        action="store_true",
        help="Skip single-instance lock (debugging).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv) if argv is not None else sys.argv[1:])

    log_path = configure_logging(args.log_level)
    install_excepthook()
    _LOG.info("Starting Simple Stipple (log file: %s)", log_path)

    app = QApplication(sys.argv)
    apply_dark_theme(app)
    install_toast()

    guard: SingleInstanceGuard | None = None
    if not args.allow_multi_instance:
        guard = SingleInstanceGuard("simple-stipple")
        if not guard.acquire():
            _LOG.info("Another instance is running — signalling activation.")
            guard.signal_existing()
            return 0

    icon_path = _resolve_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    window = App()

    if guard is not None:

        def _activate() -> None:
            window.show()
            window.raise_()
            window.activateWindow()

        guard.set_activate_callback(_activate)

    window.show()
    try:
        return app.exec()
    finally:
        if guard is not None:
            guard.release()
