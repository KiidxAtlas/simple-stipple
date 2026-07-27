"""GUI bootstrap: constructs and runs the main application window.

Split out of ``core.launcher`` so ``core`` never imports ``app`` (see
plan.md Section 9.4 / Phase 3.4) — ``core.launcher.main()`` is generic
bootstrap (arg parsing, logging, cache-dir setup) and calls back into
``run_app`` here via dependency injection, keeping the composition root's
app-specific wiring in this layer instead.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QComboBox

from simple_stipple.app.window import App
from simple_stipple.platform.error_reporting import init_sentry_full, install_toast
from simple_stipple.platform.launcher import SingleInstanceGuard

_LOG = logging.getLogger(__name__)


class _ComboWheelGuard(QObject):
    """Prevent accidental selection changes while scrolling a form."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(watched, QComboBox):
            return True
        return super().eventFilter(watched, event)


def _resolve_icon_path() -> Path | None:
    bundled_root = getattr(sys, "_MEIPASS", None)
    # Source checkout keeps assets at the repository root; frozen builds put
    # them beneath the PyInstaller bundle root.
    assets = Path(__file__).parents[3] / "assets"
    candidates = [assets / "icon.png", assets / "icon.ico"]
    if bundled_root:
        bundle_assets = Path(bundled_root) / "assets"
        candidates.extend((bundle_assets / "icon.png", bundle_assets / "icon.ico"))

    for path in candidates:
        if path.exists():
            return path
    _LOG.warning("Application icon not found in any candidate path")
    return None


def run_app(args: argparse.Namespace) -> int:
    """Build the QApplication, main window, and run the event loop."""
    app = QApplication(sys.argv)
    combo_wheel_guard = _ComboWheelGuard(app)
    app.installEventFilter(combo_wheel_guard)
    App.apply_theme(app)
    install_toast()

    # Initialize Sentry crash reporting (opt-in, no-op when DSN not set).
    init_sentry_full()

    guard: SingleInstanceGuard | None = None
    if not args.allow_multi_instance:
        guard = SingleInstanceGuard("simple-stipple")
        if not guard.acquire():
            _LOG.info("Another instance is running — signalling activation.")
            # Try to signal the running instance; if that fails (stale lock or
            # socket issues), continue startup rather than aborting so the user
            # can still run the app.
            if guard.signal_existing():
                return 0
            _LOG.warning("Failed to contact existing instance; continuing startup")

    icon_path = _resolve_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    window = App()

    # Compile numeric kernels after the first paint opportunity, keeping the
    # visible startup path responsive while avoiding a pause on first use.
    from simple_stipple.engine.geometry.jit import prewarm

    QTimer.singleShot(0, lambda: threading.Thread(target=prewarm, daemon=True).start())

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


def main(argv: list[str] | None = None) -> int:
    """Packaging entry point (``pyproject.toml``'s ``[project.scripts]``).

    ``core.launcher.main()`` needs ``run_app`` injected (see its docstring
    for why) — a bare ``module:function`` entry point can't pass keyword
    arguments, so this composes the two exactly like ``main.py`` at the
    repo root does, just as an importable function instead of a script.
    """
    from simple_stipple.platform.launcher import main as _bootstrap

    return _bootstrap(argv, run_app=run_app)


__all__ = ["main", "run_app"]
