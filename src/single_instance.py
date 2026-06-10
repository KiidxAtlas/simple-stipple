"""Single-instance guard via QLockFile + QLocalServer.

Usage::

    guard = SingleInstanceGuard("simple-stipple")
    if not guard.acquire():
        guard.signal_existing()
        sys.exit(0)
    guard.set_activate_callback(lambda: window.activateWindow())

The lock + socket live under :func:`src.paths.user_runtime_dir` so they survive
across restarts and are scoped per-user.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from src.paths import user_runtime_dir

_LOG = logging.getLogger(__name__)
_ACTIVATE_MESSAGE = b"activate\n"


class SingleInstanceGuard(QObject):
    """Cooperative lock for ensuring only one app instance is active."""

    def __init__(self, name: str = "simple-stipple", parent: QObject | None = None):
        super().__init__(parent)
        self._socket_name = f"{name}.sock"
        runtime = user_runtime_dir()
        self._lock_path = runtime / f"{name}.lock"
        try:
            from PySide6.QtCore import QLockFile

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
        _LOG.info(
            "Single-instance lock present but no server responded; removing stale lock"
        )
        try:
            QLocalServer.removeServer(self._socket_name)
        except Exception:
            pass
        try:
            if self._lock_path.exists():
                self._lock_path.unlink()
        except Exception:
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


__all__ = ["SingleInstanceGuard"]
