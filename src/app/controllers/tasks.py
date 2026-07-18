from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox

from src.backend.persistence import read_json_file, write_json_file_atomic
from src.core.paths import user_data_dir

if TYPE_CHECKING:
    from src.app.window import App


LOGGER = logging.getLogger(__name__)


class AutosaveController:
    """Manages periodic workspace snapshots for crash recovery and regular autosaving."""

    def __init__(self, app: App) -> None:
        self._app = app
        self._recovery_offered = False

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
        if not self._app._has_workspace_content() or not self._app._workspace_dirty:
            return
        try:
            path = self._app._autosave_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_file_atomic(
                path,
                {
                    "recovery": {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "workspace_path": str(self._app._workspace_path or ""),
                        "window_id": self._app._recovery_id,
                    },
                    "document": self._app._collect_workspace_document(),
                },
            )
        except Exception as exc:  # noqa: BLE001 — autosave must never crash
            LOGGER.warning("Workspace autosave failed: %s", exc)

    def _autosave(self) -> None:
        """Auto-save workspace if it has a path and is dirty."""
        if self._app._workspace_path and self._app._workspace_dirty:
            try:
                document = self._app._collect_workspace_document()
                write_json_file_atomic(self._app._workspace_path, document)
                self._app._last_saved_document = document
                self._app._workspace_dirty = False
                self._app._has_unsaved_changes = False
                self._app._update_title()
            except (OSError, TypeError, ValueError) as exc:
                LOGGER.warning("Auto-save failed: %s", exc)
                self._app._workspace_state_chip.setText("Error: Auto-save failed")
                self._app._workspace_state_chip.setProperty("tone", "danger")
                self._app._workspace_state_chip.setToolTip(
                    f"Auto-save could not write the workspace: {exc}\n"
                    "Use Save As to choose another location, then retry."
                )
                self._app._workspace_state_chip.setAccessibleDescription(
                    f"Auto-save failed. {exc}. Use Save As to choose another location."
                )
                from src.ui.util import record_notification

                record_notification(
                    f"Auto-save failed: {exc}. Use Save As to choose another location."
                )
                self._app._workspace_state_chip.style().unpolish(self._app._workspace_state_chip)
                self._app._workspace_state_chip.style().polish(self._app._workspace_state_chip)

    def _discard_autosave(self) -> None:
        try:
            self._app._autosave_path().unlink(missing_ok=True)
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
        if self._recovery_offered:
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
        labels: list[str] = []
        payloads: dict[str, tuple[Path, dict]] = {}
        for path in paths:
            try:
                raw = read_json_file(path)
                metadata = raw.get("recovery", {}) if isinstance(raw, dict) else {}
                document = raw.get("document", raw) if isinstance(raw, dict) else {}
                timestamp = str(metadata.get("timestamp", ""))
                try:
                    recovered_at = datetime.fromisoformat(timestamp).astimezone()
                    age_seconds = max(
                        0, int((datetime.now().astimezone() - recovered_at).total_seconds())
                    )
                    age = (
                        f"{age_seconds // 60} min ago"
                        if age_seconds < 3600
                        else f"{age_seconds // 3600} hr ago"
                    )
                    timestamp = recovered_at.strftime("%b %d, %I:%M %p") + f" ({age})"
                except (TypeError, ValueError):
                    timestamp = "Unknown time"
                workspace = (
                    Path(str(metadata.get("workspace_path", ""))).name or "Unsaved workspace"
                )
                label = f"{workspace} — {timestamp}"
                labels.append(label)
                payloads[label] = (path, document)
            except (OSError, ValueError, TypeError):
                continue
        if not labels:
            return
        label, accepted = QInputDialog.getItem(
            self._app, "Recover Unsaved Work", "Choose a recovery snapshot:", labels, 0, False
        )
        if not accepted:
            return
        path, document = payloads[label]
        try:
            self._app._apply_workspace_document(document)
            self._app._workspace_dirty = True
            self._app._update_title()
            # Keep the known-good snapshot until the restored work is actually
            # saved. A second crash before the next timer tick must not erase it.
            self._app._restored_recovery_path = path
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self._app, "Recovery Failed", f"Could not restore snapshot:\n{exc}")
        self._app._restored_recovery_path = None

    def open_recovery_manager(self) -> None:
        """Open the recovery dialog to manually recover unsaved work."""
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
            QMessageBox.information(
                self._app, "No Recovery Snapshots", "No recovery snapshots found."
            )
            return
        labels: list[str] = []
        payloads: dict[str, tuple[Path, dict]] = {}
        for path in paths:
            try:
                raw = read_json_file(path)
                metadata = raw.get("recovery", {}) if isinstance(raw, dict) else {}
                document = raw.get("document", raw) if isinstance(raw, dict) else {}
                timestamp = str(metadata.get("timestamp", ""))
                try:
                    recovered_at = datetime.fromisoformat(timestamp).astimezone()
                    age_seconds = max(
                        0, int((datetime.now().astimezone() - recovered_at).total_seconds())
                    )
                    age = (
                        f"{age_seconds // 60} min ago"
                        if age_seconds < 3600
                        else f"{age_seconds // 3600} hr ago"
                    )
                    timestamp = recovered_at.strftime("%b %d, %I:%M %p") + f" ({age})"
                except (TypeError, ValueError):
                    timestamp = "Unknown time"
                workspace = (
                    Path(str(metadata.get("workspace_path", ""))).name or "Unsaved workspace"
                )
                label = f"{workspace} — {timestamp}"
                labels.append(label)
                payloads[label] = (path, document)
            except (OSError, ValueError, TypeError):
                continue
        if not labels:
            QMessageBox.information(
                self._app, "No Recovery Snapshots", "No valid recovery snapshots found."
            )
            return
        label, accepted = QInputDialog.getItem(
            self._app, "Recover Unsaved Work", "Choose a recovery snapshot:", labels, 0, False
        )
        if not accepted:
            return
        path, document = payloads[label]
        try:
            self._app._apply_workspace_document(document)
            self._app._workspace_dirty = True
            self._app._update_title()
            self._app._restored_recovery_path = path
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self._app, "Recovery Failed", f"Could not restore snapshot:\n{exc}")

    def shutdown(self) -> None:
        """Stops all timers and cleans up the autosave file."""
        self._recovery_timer.stop()
        self._regular_timer.stop()
        self._discard_autosave()


class UpdateChecker:
    """Manages periodic auto-fetching of repository metadata and startup update checks."""

    def __init__(self, app: App) -> None:
        self._app = app

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
        from src.ui.widgets.dialogs.update_dialog import UpdateCheckThread

        self._startup_update_thread = UpdateCheckThread(self._app)
        self._startup_update_thread.checkComplete.connect(self._on_startup_complete)
        self._startup_update_thread.start()

    def _on_startup_complete(self, info) -> None:
        if info and info.is_newer:
            from src.ui.widgets.dialogs.update_dialog import UpdateDialog

            UpdateDialog(self._app, info).exec()

    def shutdown(self) -> None:
        """Stops the auto-fetch timer."""
        self._auto_fetch_timer.stop()


class TaskController:
    """Single lifecycle surface for background application tasks."""

    def __init__(self, app: App) -> None:
        self.autosave = AutosaveController(app)
        self.updates = UpdateChecker(app)

    def startup(self, *, check_updates: bool) -> None:
        QTimer.singleShot(200, self.autosave.offer_startup_autosave_recovery)
        if check_updates:
            QTimer.singleShot(1000, self.updates._attempt_startup_update_check)
        self.updates._configure_auto_fetch_timer()

    def shutdown(self) -> None:
        self.autosave.shutdown()
        self.updates.shutdown()
