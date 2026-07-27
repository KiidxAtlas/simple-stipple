"""Application-wide exception handler + error toast surface.

Installs Python `sys.excepthook`, `threading.excepthook`, and Qt message handler
hooks so unhandled errors are logged with full tracebacks instead of being
swallowed silently or crashing the app. Provides `safe_call` /
`safe_callback` helpers and a small toast widget for surfacing failures
without blocking the UI.
"""

from __future__ import annotations

import functools
import logging
import sys
import threading
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout

_LOG = logging.getLogger(__name__)

T = TypeVar("T")


class _ErrorBus(QObject):
    """Thread-safe error signal — callbacks may emit from worker threads."""

    error_raised = Signal(str, str)  # (title, detail)


_BUS = _ErrorBus()


def error_bus() -> _ErrorBus:
    return _BUS


def report_error(title: str, exc: BaseException) -> None:
    """Log and emit an error to the toast bus (safe from any thread)."""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    # Log full traceback and include exception info for better diagnostic output.
    try:
        _LOG.error("%s: %s", title, detail)
    except Exception:
        # Fallback to a simpler log if formatting fails.
        _LOG.error("%s: %s", title, str(exc))
    _LOG.debug("Emitting error toast: %s", title)
    # Emit a concise single-line message to the toast bus (avoid huge payloads).
    snippet = (str(exc) or "").splitlines()[0] if exc else ""
    if not snippet:
        # Fall back to the last non-empty line from the full detail.
        lines = [ln for ln in detail.splitlines() if ln.strip()]
        snippet = lines[-1] if lines else "Unexpected error"
    # Guard against empty/whitespace-only exception message producing "None"
    if snippet == "None":
        snippet = "Unexpected error"
    _BUS.error_raised.emit(title, snippet)


def _python_excepthook(exc_type, exc_value, exc_tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        # Preserve default behavior for Ctrl+C.
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _LOG.critical("Unhandled exception:\n%s", detail)
    try:
        _BUS.error_raised.emit(
            "Unexpected Error",
            f"{exc_type.__name__}: {exc_value}",
        )
    except Exception:  # defensive
        pass


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    detail = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    _LOG.critical(
        "Unhandled exception in thread %s:\n%s",
        getattr(args.thread, "name", "?"),
        detail,
    )
    try:
        _BUS.error_raised.emit(
            "Background Task Error",
            f"{args.exc_type.__name__}: {args.exc_value}",
        )
    except Exception:  # defensive
        pass


def install_excepthook() -> None:
    """Install Python and threading excepthooks. Idempotent."""
    if getattr(install_excepthook, "_installed", False):
        return
    sys.excepthook = _python_excepthook
    threading.excepthook = _thread_excepthook
    install_excepthook._installed = True  # type: ignore[attr-defined]
    _LOG.debug("Excepthooks installed")


def safe_call(fn: Callable[..., T], *args, default: T | None = None, **kwargs) -> T | None:
    """Invoke fn; log + report on exception, returning default."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - boundary
        report_error(f"{getattr(fn, '__qualname__', 'callback')} failed", exc)
        return default


def safe_callback(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator wrapping a Qt slot/callback so exceptions are reported."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            report_error(f"{getattr(fn, '__qualname__', 'callback')} failed", exc)
            return None

    return wrapper


# ── Toast widget ─────────────────────────────────────────────────────────────


class ErrorToast(QFrame):
    """Bottom-right transient toast for non-blocking error notification."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "QFrame { background: #2d1418; border: 1px solid #f85149;"
            " border-radius: 6px; }"
            "QLabel { color: #ffd7d3; font-size: 12px; }"
            "QLabel#title { color: #f85149; font-weight: 600; font-size: 13px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self._title = QLabel()
        self._title.setObjectName("title")
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addWidget(self._detail)
        self.setMaximumWidth(420)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_error(self, title: str, detail: str) -> None:
        self._title.setText(title)
        # Truncate very long detail lines.
        snippet = detail.strip().splitlines()[0] if detail else ""
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        self._detail.setText(snippet)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(6000)

    def _reposition(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        margin = 18
        x = geom.right() - self.width() - margin
        y = geom.bottom() - self.height() - margin
        self.move(x, y)


_TOAST: ErrorToast | None = None


def install_toast(parent=None) -> None:
    """Connect the error bus to a toast widget. Call after QApplication exists."""
    global _TOAST
    if _TOAST is not None:
        return
    if QApplication.instance() is None:
        return
    _TOAST = ErrorToast(parent)
    _BUS.error_raised.connect(_TOAST.show_error, Qt.ConnectionType.QueuedConnection)
    _LOG.debug("Error toast installed")


__all__ = [
    "capture_exception",
    "ErrorToast",
    "error_bus",
    "install_excepthook",
    "install_toast",
    "init_sentry",
    "init_sentry_full",
    "report_error",
    "safe_call",
    "safe_callback",
]

# Optional Sentry integration


def init_sentry(dsn: str | None = None, enabled: bool = True) -> None:
    """Initialize Sentry crash reporting.

    Parameters
    ----------
    dsn:
        Sentry Data Source Name. If *None*, reads ``SENTRY_DSN`` from the
        environment, then falls back to the ``sentry_dsn`` key in the user's
        settings file.
    enabled:
        When *False* the module becomes a no-op (useful for tests or when
        the user has disabled telemetry).
    """
    if not enabled:
        _LOG.debug("Sentry initialization skipped (disabled)")
        return

    if dsn is None:
        dsn = _resolve_dsn()

    if not dsn:
        _LOG.debug("Sentry initialization skipped (no DSN configured)")
        return

    try:
        import sentry_sdk  # type: ignore[import-untyped]
    except ImportError:
        _LOG.debug("sentry-sdk not installed — skipping crash reporting")
        return

    from simple_stipple.platform.settings import load_settings

    settings = load_settings()
    version = settings.get("_version", "0.0.0")

    sentry_sdk.init(
        dsn=dsn,
        release=f"simple-stipple@{version}",
        environment=_resolve_environment(dsn),
        traces_sample_rate=0.0,  # we don't track performance, only errors
        before_send=_before_send,
        integrations=[
            _QtIntegration(),
        ],
    )

    # Hook into the existing error bus so every reported error reaches Sentry.
    from simple_stipple.platform.error_reporting import error_bus

    try:
        error_bus().error_raised.connect(_on_error_bus_error)
        _LOG.info("Sentry crash reporting initialized (DSN: %s…)", dsn[:20])
    except Exception:
        _LOG.debug("Could not connect error bus to Sentry")


def capture_exception(exc: BaseException, **kwargs: Any) -> None:
    """Capture an exception with Sentry (safe to call even if not initialized)."""
    try:
        import sentry_sdk  # type: ignore[import-untyped]

        sentry_sdk.capture_exception(exc, **kwargs)
    except ImportError:
        _LOG.debug("sentry-sdk not installed — cannot capture exception")
    except Exception:
        # Never let Sentry failures crash the app.
        _LOG.debug("Sentry capture_exception failed", exc_info=True)


def flush(timeout: float = 2.0) -> None:
    """Flush pending events (call before exit)."""
    try:
        import sentry_sdk  # type: ignore[import-untyped]

        sentry_sdk.flush(timeout=timeout)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_dsn() -> str | None:
    """Return the Sentry DSN from env or settings, or None."""
    import os

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if dsn:
        return dsn

    try:
        from simple_stipple.platform.settings import load_settings

        settings = load_settings()
        dsn = (settings.get("sentry_dsn") or "").strip()
        if dsn:
            return dsn
    except Exception:
        pass

    return None


def _resolve_environment(dsn: str) -> str:
    """Decide 'production' vs 'development' based on DSN hostname."""
    if "sentry.io" in dsn:
        return "production"
    return "development"


def _before_send(event: dict, hint: dict) -> dict | None:
    """Filter events before sending to Sentry."""
    # Skip keyboard-interrupt style events.
    exc = hint.get("exc_info")
    if exc is not None:
        exc_type = getattr(exc[0], "__name__", "")
        if exc_type in ("KeyboardInterrupt", "SystemExit"):
            return None

    # Strip sensitive data from breadcrumbs.
    breadcrumbs = event.get("breadcrumbs", {}).get("values", [])
    for crumb in breadcrumbs:
        if "data" in crumb:
            data = crumb["data"]
            for key in ("password", "secret", "token", "authorization"):
                data.pop(key, None)

    return event


def _on_error_bus_error(title: str, detail: str) -> None:
    """Callback from the error bus — capture as Sentry exception."""
    try:
        import sentry_sdk  # type: ignore[import-untyped]

        sentry_sdk.capture_message(title, level="error", extra={"detail": detail})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Qt integration — catches unhandled exceptions in Qt event loop
# ---------------------------------------------------------------------------


class _QtIntegration(QObject):
    """Catch unhandled exceptions from the Qt event loop and forward to Sentry."""

    def __init__(self) -> None:
        super().__init__()
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        try:
            import sys

            original_excepthook = sys.excepthook

            def hook(exc_type, exc_value, exc_tb) -> None:
                if issubclass(exc_type, KeyboardInterrupt):
                    original_excepthook(exc_type, exc_value, exc_tb)
                    return
                try:
                    import sentry_sdk  # type: ignore[import-untyped]

                    sentry_sdk.capture_exception(exc_value)
                except Exception:
                    pass
                # Always call the original so the app still crashes (or shows
                # its toast) — we don't want to silently swallow errors.
                original_excepthook(exc_type, exc_value, exc_tb)

            sys.excepthook = hook
            self._installed = True
            _LOG.debug("Qt exception hook patched for Sentry")
        except Exception:
            _LOG.debug("Failed to patch Qt exception hook")


# ---------------------------------------------------------------------------
# Graceful shutdown — flush pending Sentry events
# ---------------------------------------------------------------------------


def _install_shutdown_flush() -> None:
    """Register a flush-on-exit handler. Call once during startup."""
    try:
        import sentry_sdk  # type: ignore[import-untyped]
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(lambda: sentry_sdk.flush(timeout=2.0))
    except Exception:
        pass


# Make it easy to initialize everything in one call.
def init_sentry_full(dsn: str | None = None, enabled: bool = True) -> None:
    """Full Sentry initialization: init + shutdown flush.

    This is the recommended entry point for production use.
    """
    init_sentry(dsn=dsn, enabled=enabled)
    _install_shutdown_flush()
