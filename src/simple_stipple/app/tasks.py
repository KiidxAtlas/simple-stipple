from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QObject, QTimer, Signal

from simple_stipple.platform.settings import user_data_dir
from simple_stipple.platform.storage import (
    MAX_WORKSPACE_FILE_BYTES,
    read_json_file,
    write_json_file_atomic,
)

if TYPE_CHECKING:
    from simple_stipple.app.window import App

from simple_stipple.ui.components.feedback import refresh_style

LOGGER = logging.getLogger(__name__)

# A network-backed QThread cannot be force-stopped safely while it is inside a
# blocking request. Keep detached startup checks alive until Qt reports that
# they finished instead of letting window destruction delete a running thread.
_DETACHED_UPDATE_THREADS: set[Any] = set()


class AutosaveController(QObject):
    """Manages periodic workspace snapshots for crash recovery and regular autosaving."""

    _regular_saved = Signal(object)
    _regular_failed = Signal(str)
    _recovery_saved = Signal()
    _recovery_failed = Signal(str)

    _MAX_SNAPSHOTS_PER_WINDOW = 10

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self._app = app
        self._recovery_offered = False
        self._shutting_down = False
        self._recovery_write_thread: threading.Thread | None = None
        self._regular_write_thread: threading.Thread | None = None
        self._regular_saved.connect(self._on_regular_saved)
        self._regular_failed.connect(self._on_regular_failed)
        self._recovery_saved.connect(self._on_recovery_saved)
        self._recovery_failed.connect(self._on_recovery_failed)
        self._last_failure_message = ""

        # Crash recovery: periodically snapshot unsaved work to the app data
        # dir; a clean exit or successful save removes the snapshot.
        self._recovery_timer = QTimer(self._app)
        self._recovery_timer.setInterval(90_000)
        self._recovery_timer.timeout.connect(self._autosave_workspace)
        self._recovery_timer.start()

        # Auto-save every 60 seconds if workspace has a path and is dirty
        self._regular_timer = QTimer(self._app)
        self._regular_timer.setInterval(60_000)
        self._regular_timer.timeout.connect(self._autosave)
        self._regular_timer.start()

    def _autosave_workspace(self) -> None:
        if self._shutting_down:
            return
        if not self._app._has_workspace_content() or not self._app._workspace_dirty:
            return
        if self._recovery_write_thread is not None and self._recovery_write_thread.is_alive():
            return
        path = self._app._autosave_path()
        document = self._app._collect_workspace_document()
        workspace_path = str(self._app._workspace_path or "")
        recovery_id = self._app._recovery_id

        def write_recovery() -> None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                # Preserve the previous distinct state before replacing the
                # live snapshot.
                if path.exists():
                    previous = read_json_file(
                        path,
                        default={},
                        max_bytes=MAX_WORKSPACE_FILE_BYTES,
                    )
                    previous_document = (
                        previous.get("document", previous) if isinstance(previous, dict) else None
                    )
                    if previous_document != document:
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                        base = path.name.removesuffix(".workspace.json")
                        path.replace(path.with_name(f"{base}-{stamp}.workspace.json"))
                if self._shutting_down:
                    return
                write_json_file_atomic(
                    path,
                    {
                        "recovery": {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "workspace_path": workspace_path,
                            "window_id": recovery_id,
                        },
                        "document": document,
                    },
                )
                base = path.name.removesuffix(".workspace.json")
                snapshots = sorted(
                    path.parent.glob(f"{base}*.workspace.json"),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                for stale in snapshots[self._MAX_SNAPSHOTS_PER_WINDOW :]:
                    stale.unlink(missing_ok=True)
                self._recovery_saved.emit()
            except Exception as exc:  # noqa: BLE001 — worker boundary
                LOGGER.warning("Workspace autosave failed: %s", exc)
                self._recovery_failed.emit(str(exc))

        self._recovery_write_thread = threading.Thread(target=write_recovery, daemon=True)
        self._recovery_write_thread.start()

    def _autosave(self) -> None:
        """Auto-save workspace if it has a path and is dirty."""
        if self._shutting_down:
            return
        if not self._app._workspace_path or not self._app._workspace_dirty:
            return
        if self._regular_write_thread is not None and self._regular_write_thread.is_alive():
            return
        path = self._app._workspace_path
        document = self._app._collect_workspace_document()

        def write_regular() -> None:
            try:
                write_json_file_atomic(path, document)
                self._regular_saved.emit(document)
            except (OSError, TypeError, ValueError) as exc:
                self._regular_failed.emit(str(exc))

        self._regular_write_thread = threading.Thread(target=write_regular, daemon=True)
        self._regular_write_thread.start()

    def _on_regular_saved(self, document: object) -> None:
        if self._shutting_down or not isinstance(document, dict):
            return
        self._app._last_saved_document = document
        self._record_durable_write_success()
        # Only mark clean if no newer GUI state arrived during the write.
        current = self._app._collect_workspace_document()
        if current == document:
            self._app._workspace_dirty = False
            self._app._has_unsaved_changes = False
            self._app._update_title()

    def _on_recovery_saved(self) -> None:
        if not self._shutting_down:
            self._record_durable_write_success()

    def _record_durable_write_success(self) -> None:
        self._last_failure_message = ""
        self._app._last_autosave_at = datetime.now().astimezone()
        self._app.clear_system_failure()
        self._app._refresh_workspace_header()

    def _on_recovery_failed(self, message: str) -> None:
        if not self._shutting_down:
            self._show_autosave_failure(message, recovery=True)

    def _on_regular_failed(self, message: str) -> None:
        if self._shutting_down:
            return
        self._show_autosave_failure(message, recovery=False)

    def _show_autosave_failure(self, message: str, *, recovery: bool) -> None:
        LOGGER.warning("Auto-save failed: %s", message)
        failure_key = f"{'recovery' if recovery else 'workspace'}:{message}"
        duplicate = failure_key == self._last_failure_message
        self._last_failure_message = failure_key
        kind = "Recovery snapshot" if recovery else "Auto-save"
        self._app.show_system_failure(
            f"{kind} failed: {message}. Your current work remains open, but durable "
            "protection is unavailable until a write succeeds."
        )
        self._app._workspace_state_chip.setText(f"Error: {kind} failed")
        self._app._workspace_state_chip.setProperty("tone", "danger")
        self._app._workspace_state_chip.setToolTip(
            f"{kind} could not write: {message}\n"
            "Manage storage or choose another location, then retry."
        )
        self._app._workspace_state_chip.setAccessibleDescription(
            f"{kind} failed. {message}. Manage storage or choose another location."
        )
        from simple_stipple.ui.components.feedback import record_notification

        if not duplicate:
            record_notification(
                f"{kind} failed: {message}. Manage storage or choose another location."
            )
        refresh_style(self._app._workspace_state_chip)

    def _discard_autosave(self) -> None:
        try:
            path = self._app._autosave_path()
            path.unlink(missing_ok=True)
            base = path.name.removesuffix(".workspace.json")
            for snapshot in path.parent.glob(f"{base}*.workspace.json"):
                snapshot.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("Could not remove autosave: %s", exc)

    def _discard_restored_snapshot(self) -> None:
        path = self._app._restored_recovery_path
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
            self._app._restored_recovery_path = None
        except OSError as exc:
            LOGGER.warning("Could not remove restored recovery snapshot: %s", exc)

    def offer_startup_autosave_recovery(self) -> None:
        if self._shutting_down or self._recovery_offered:
            return
        self._recovery_offered = True
        recovery_dir = user_data_dir() / "recovery"
        paths = sorted(
            recovery_dir.glob("*.workspace.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        legacy = user_data_dir() / "autosave.workspace.json"
        if legacy.exists():
            paths.append(legacy)
        if not paths:
            return
        # Startup and manual recovery must use the same browsable surface.  The
        # old QInputDialog made snapshots with similar workspace names appear
        # indistinguishable and offered no direct management actions.
        self._app._open_saved_workspaces(initial_source="recovery")

    def shutdown(self) -> None:
        """Stops all timers and cleans up the autosave file."""
        self._shutting_down = True
        self._recovery_timer.stop()
        self._regular_timer.stop()
        for thread in (self._recovery_write_thread, self._regular_write_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)
        self._discard_autosave()


class UpdateChecker:
    """Manages periodic auto-fetching of repository metadata and startup update checks."""

    def __init__(self, app: App) -> None:
        self._app = app
        self._shutting_down = False
        self._startup_update_thread: Any | None = None

        # Periodic auto-fetch while app is open (when enabled in settings)
        self._auto_fetch_timer = QTimer(self._app)
        self._auto_fetch_timer.setSingleShot(False)
        self._auto_fetch_timer.timeout.connect(self._attempt_auto_fetch)
        self._configure_auto_fetch_timer()

    def _attempt_auto_fetch(self) -> None:
        """Attempt to fetch remote repository metadata without altering the working tree."""
        try:
            cast(Any, self._app._repo_page).auto_fetch()
        except (OSError, RuntimeError, ValueError) as exc:
            LOGGER.warning("Auto-fetch failed: %s", exc)

    def _auto_fetch_interval_ms(self) -> int:
        """Return periodic auto-fetch interval in milliseconds."""
        raw = self._app._settings.get("auto_fetch_interval_minutes", 10)
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            minutes = 10
        minutes = max(1, minutes)
        return minutes * 60_000

    def _configure_auto_fetch_timer(self) -> None:
        """Start/stop periodic auto-fetch based on current settings."""
        enabled = bool(self._app._settings.get("auto_fetch_periodic", False))
        if not enabled:
            self._auto_fetch_timer.stop()
            return

        interval_ms = self._auto_fetch_interval_ms()
        if self._auto_fetch_timer.interval() != interval_ms:
            self._auto_fetch_timer.setInterval(interval_ms)

        if not self._auto_fetch_timer.isActive():
            self._auto_fetch_timer.start()

    def _attempt_startup_update_check(self) -> None:
        """Check for updates on startup (silent if up-to-date)."""
        from simple_stipple.ui.dialogs.update_dialog import UpdateCheckThread

        if self._shutting_down:
            return
        # Do not parent a potentially blocking network thread to the window:
        # deleting a running QThread is a fatal Qt lifecycle error.
        self._startup_update_thread = UpdateCheckThread()
        self._startup_update_thread.checkComplete.connect(self._on_startup_complete)
        self._startup_update_thread.start()

    def _on_startup_complete(self, info) -> None:
        if self._shutting_down:
            return
        if info and info.is_newer:
            from simple_stipple.ui.dialogs.update_dialog import UpdateDialog

            UpdateDialog(self._app, info).exec()

    def shutdown(self) -> None:
        """Stop timers and suppress UI delivery from an in-flight check."""
        self._shutting_down = True
        self._auto_fetch_timer.stop()
        thread = getattr(self, "_startup_update_thread", None)
        if thread is not None and thread.isRunning():
            try:
                thread.checkComplete.disconnect(self._on_startup_complete)
            except RuntimeError:
                pass
            thread.requestInterruption()
            _DETACHED_UPDATE_THREADS.add(thread)
            thread.finished.connect(lambda active=thread: _DETACHED_UPDATE_THREADS.discard(active))
        self._startup_update_thread = None


class TaskController:
    """Single lifecycle surface for background application tasks."""

    def __init__(self, app: App) -> None:
        self.autosave = AutosaveController(app)
        self.updates = UpdateChecker(app)
        self._recovery_start_timer = QTimer(app)
        self._recovery_start_timer.setSingleShot(True)
        self._recovery_start_timer.timeout.connect(self.autosave.offer_startup_autosave_recovery)
        self._update_start_timer = QTimer(app)
        self._update_start_timer.setSingleShot(True)
        self._update_start_timer.timeout.connect(self.updates._attempt_startup_update_check)

    def startup(self, *, check_updates: bool) -> None:
        self._recovery_start_timer.start(200)
        if check_updates:
            self._update_start_timer.start(1000)
        self.updates._configure_auto_fetch_timer()

    def shutdown(self) -> None:
        self._recovery_start_timer.stop()
        self._update_start_timer.stop()
        self.autosave.shutdown()
        self.updates.shutdown()
