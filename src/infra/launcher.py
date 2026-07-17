"""Application bootstrap: logging setup, single-instance locking, and the
packaged launcher entry point.

Three previously-separate modules merged here — ``logging_config.py`` and
``single_instance.py`` each had exactly one caller (this module), and all
three are genuinely "how the process starts up," not independently reusable
pieces.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile, QObject, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QComboBox

from src.infra.paths import user_log_dir, user_runtime_dir

_LOG = logging.getLogger(__name__)
_ACTIVATE_MESSAGE = b"activate\n"


class _ComboWheelGuard(QObject):
    """Prevent accidental selection changes while scrolling a form."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(watched, QComboBox):
            return True
        return super().eventFilter(watched, event)


# ══════════════════════════════════════════════════════════════════════════
# Logging configuration
# ══════════════════════════════════════════════════════════════════════════

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
    for noisy in ("PIL", "matplotlib", "urllib3", "ezdxf"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging initialized → %s", log_path)
    return log_path


# ════════════════════════════════════════════════════════════════════════════
# Single-instance guard (QLockFile + QLocalServer)
# ════════════════════════════════════════════════════════════════════════════


class SingleInstanceGuard(QObject):
    """Cooperative lock for ensuring only one app instance is active."""

    def __init__(self, name: str = "simple-stipple", parent: QObject | None = None):
        super().__init__(parent)
        self._socket_name = f"{name}.sock"
        runtime = user_runtime_dir()
        self._lock_path = runtime / f"{name}.lock"
        self._lockfile: QLockFile | None
        try:
            self._lockfile = QLockFile(str(self._lock_path))
            self._lockfile.setStaleLockTime(0)
        except ImportError:  # PySide6 always provides this
            self._lockfile = None
        self._server: QLocalServer | None = None
        self._on_activate: Callable[[], None] | None = None

    def acquire(self) -> bool:
        """Return True if this is the only running instance."""
        if self._lockfile is None:
            return True
        # Try to acquire the lock. If it fails, the lockfile may be stale
        # (previous crash) or a real running instance may exist. Try to
        # contact an existing server; if none responds, remove the stale
        # artefacts and retry acquiring the lock so we can become the
        # primary instance.
        if self._lockfile.tryLock(50):
            # Start a local server so existing instance can be signalled.
            QLocalServer.removeServer(self._socket_name)
            self._server = QLocalServer(self)
            self._server.newConnection.connect(self._handle_new_connection)
            if not self._server.listen(self._socket_name):
                _LOG.warning(
                    "Could not start single-instance server: %s",
                    self._server.errorString(),
                )
            return True

        # Could not lock: check whether a server is actually listening.
        sock = QLocalSocket()
        sock.connectToServer(self._socket_name)
        if sock.waitForConnected(250):
            # An instance is up and responding.
            try:
                sock.disconnectFromServer()
            except Exception:
                pass
            return False

        # No server responded. Treat the lock as stale: remove any
        # leftover server socket and stale lock file, then retry.
        _LOG.info("Single-instance lock present but no server responded; removing stale lock")
        try:
            QLocalServer.removeServer(self._socket_name)
        except Exception:
            pass
        try:
            if self._lock_path.exists():
                self._lock_path.unlink()
        except OSError:
            pass

        # Retry acquiring the lock once more.
        if self._lockfile.tryLock(50):
            QLocalServer.removeServer(self._socket_name)
            self._server = QLocalServer(self)
            self._server.newConnection.connect(self._handle_new_connection)
            if not self._server.listen(self._socket_name):
                _LOG.warning(
                    "Could not start single-instance server after removing stale lock: %s",
                    self._server.errorString(),
                )
            return True

        return False

    def signal_existing(self, timeout_ms: int = 500) -> bool:
        """Send an activation message to the running instance."""
        sock = QLocalSocket()
        sock.connectToServer(self._socket_name)
        if not sock.waitForConnected(timeout_ms):
            _LOG.warning("Failed to contact existing instance: %s", sock.errorString())
            return False
        sock.write(_ACTIVATE_MESSAGE)
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return True

    def set_activate_callback(self, callback: Callable[[], None]) -> None:
        self._on_activate = callback

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lockfile is not None:
            self._lockfile.unlock()

    def _handle_new_connection(self) -> None:
        if self._server is None:
            return
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda: self._read_and_activate(sock))
        sock.disconnected.connect(sock.deleteLater)

    def _read_and_activate(self, sock: QLocalSocket) -> None:
        try:
            sock.readAll()
        except Exception:  # noqa: BLE001
            pass
        if self._on_activate is not None:
            # Defer to event loop so we don't block readyRead handler.
            QTimer.singleShot(0, self._on_activate)


# ════════════════════════════════════════════════════════════════════════════
# Packaged launcher entry point
# ════════════════════════════════════════════════════════════════════════════


def _resolve_icon_path() -> Path | None:
    bundled_root = getattr(sys, "_MEIPASS", None)
    # Source checkout keeps assets at the repository root; frozen builds put
    # them beneath the PyInstaller bundle root.
    assets = Path(__file__).parents[2] / "assets"
    candidates = [assets / "icon.png", assets / "icon.ico"]
    if bundled_root:
        bundle_assets = Path(bundled_root) / "assets"
        candidates.extend((bundle_assets / "icon.png", bundle_assets / "icon.ico"))

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

    # Ensure third-party libraries write cache files into the app-controlled
    # cache directory rather than the user's generic ~/.cache which may be
    # restricted by OS privacy settings. Set `XDG_CACHE_HOME` early so libs
    # like ezdxf pick it up during import.
    from src.infra.paths import user_cache_dir

    os.environ.setdefault("XDG_CACHE_HOME", str(user_cache_dir()))

    # Defer importing app modules until after cache env is configured so any
    # library initialisation uses the new cache path.
    from src.app import App
    from src.infra.error_reporting import install_excepthook, install_toast
    from src.ui.style.theme import apply_dark_theme

    log_path = configure_logging(args.log_level)
    install_excepthook()
    _LOG.info("Starting Simple Stipple (log file: %s)", log_path)

    app = QApplication(sys.argv)
    combo_wheel_guard = _ComboWheelGuard(app)
    app.installEventFilter(combo_wheel_guard)
    apply_dark_theme(app)
    install_toast()

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


__all__ = ["SingleInstanceGuard", "configure_logging", "main"]
