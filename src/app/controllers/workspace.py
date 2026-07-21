from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox

from src.app.workspace_session import (
    apply_workspace_document,
    clear_workspace_state,
    collect_workspace_document,
    recent_workspace_paths,
    remember_workspace_path,
    workspace_default_dir,
    workspace_title,
)
from src.backend.model.document import WORKSPACE_FILE_SUFFIX, normalize_workspace_path
from src.backend.persistence import (
    MAX_WORKSPACE_FILE_BYTES,
    read_json_file,
    write_json_file_atomic,
)
from src.core.paths import saved_workspaces_dir, user_data_dir
from src.core.settings import save_settings
from src.ui.widgets.dialogs.workspace_library import WorkspaceLibraryDialog

if TYPE_CHECKING:
    from src.app.window import App


LOGGER = logging.getLogger(__name__)


class _WorkspaceStateController:
    """Own workspace identity/state and page-document serialization."""

    def __init__(self, page_runtime, tabs) -> None:
        self._page_runtime = page_runtime
        self._tabs = tabs
        self.path: Path | None = None
        self.dirty = False
        self.last_saved_document: dict | None = None
        self.has_unsaved_changes = False

    def collect(self) -> dict:
        return collect_workspace_document(
            workspace_path=self.path,
            current_tab_index=self._tabs.currentIndex(),
            workspace_pages=self._page_runtime.iter_workspace_pages(),
            preset_pages=self._page_runtime.iter_preset_pages(),
        )

    def apply(self, document: dict) -> None:
        # Page application is necessarily imperative. Keep a complete snapshot
        # so a late page failure cannot leave a half-old, half-new workspace.
        previous = self.collect()
        try:
            apply_workspace_document(
                document=document,
                workspace_pages=self._page_runtime.iter_workspace_pages(),
                preset_pages=self._page_runtime.iter_preset_pages(),
                tab_count=self._tabs.count(),
                set_current_tab_index=self._tabs.setCurrentIndex,
            )
        except Exception as apply_error:
            try:
                apply_workspace_document(
                    document=previous,
                    workspace_pages=self._page_runtime.iter_workspace_pages(),
                    preset_pages=self._page_runtime.iter_preset_pages(),
                    tab_count=self._tabs.count(),
                    set_current_tab_index=self._tabs.setCurrentIndex,
                )
            except Exception as rollback_error:
                raise RuntimeError(
                    "Workspace could not be loaded and the previous view could not be "
                    f"fully restored. Load error: {apply_error}. "
                    f"Restore error: {rollback_error}. The workspace was not saved."
                ) from apply_error
            raise

    def clear(self) -> None:
        clear_workspace_state(
            workspace_pages=self._page_runtime.iter_workspace_pages(),
            set_current_tab_index=self._tabs.setCurrentIndex,
        )


class WorkspaceController(_WorkspaceStateController):
    """Own workspace actions, state, persistence, and recent files."""

    _workspace_path: Path | None
    _last_saved_document: dict | None

    def __init__(self, app: App, page_runtime, tabs) -> None:
        self._app = app
        super().__init__(page_runtime, tabs)

    def _build_workspace_actions(self) -> None:
        self._app._new_workspace_action = QAction("New Workspace", self._app)
        self._app._new_workspace_action.setShortcut(
            QKeySequence(self._app._shortcut("workspace.new"))
        )
        self._app._new_workspace_action.triggered.connect(self._new_workspace)
        self._app._workspace_menu.addAction(self._app._new_workspace_action)

        self._app._new_window_action = QAction("New Window", self._app)
        self._app._new_window_action.setShortcut(
            QKeySequence(self._app._shortcut("workspace.new_window"))
        )
        self._app._new_window_action.triggered.connect(self._app._new_window)
        self._app._workspace_menu.addAction(self._app._new_window_action)

        self._app._open_workspace_action = QAction("Open Workspace…", self._app)
        self._app._open_workspace_action.setShortcut(
            QKeySequence(self._app._shortcut("workspace.open"))
        )
        self._app._open_workspace_action.triggered.connect(self._open_workspace)
        self._app._workspace_menu.addAction(self._app._open_workspace_action)

        self._app._saved_workspaces_action = QAction("Workspaces and Recovery…", self._app)
        self._app._saved_workspaces_action.triggered.connect(self._open_saved_workspaces)
        self._app._workspace_menu.addAction(self._app._saved_workspaces_action)

        self._app._save_workspace_action = QAction("Save Workspace", self._app)
        self._app._save_workspace_action.setShortcut(
            QKeySequence(self._app._shortcut("workspace.save"))
        )
        self._app._save_workspace_action.triggered.connect(self._save_workspace)
        self._app._workspace_menu.addAction(self._app._save_workspace_action)

        self._app._save_workspace_as_action = QAction("Save Workspace As…", self._app)
        self._app._save_workspace_as_action.setShortcut(
            QKeySequence(self._app._shortcut("workspace.save_as"))
        )
        self._app._save_workspace_as_action.triggered.connect(self._save_workspace_as)
        self._app._workspace_menu.addAction(self._app._save_workspace_as_action)

        self._app._recover_workspace_action = QAction("Recover Unsaved Work…", self._app)
        self._app._recover_workspace_action.triggered.connect(
            lambda: self._open_saved_workspaces(initial_source="recovery")
        )
        self._app._workspace_menu.addAction(self._app._recover_workspace_action)

        self._app._workspace_menu.addSeparator()

        self._app._repo_dialog_action = QAction("Repository Sync…", self._app)
        self._app._repo_dialog_action.setShortcut(QKeySequence(self._app._shortcut("tab.repo")))
        self._app._repo_dialog_action.triggered.connect(self._app._open_repo_dialog)
        self._app._workspace_menu.addAction(self._app._repo_dialog_action)

        self._app._workspace_menu.addSeparator()

    def _collect_workspace_document(self) -> dict:
        self._app._workspace_controller.path = self._app._workspace_path
        return self._app._workspace_controller.collect()

    def _schedule_workspace_dirty_check(self) -> None:
        self._app._has_unsaved_changes = True
        self._app._workspace_timer.start(150)

    def _save_workspace_as(self) -> bool:
        default_name = (
            self._app._workspace_path.name
            if self._app._workspace_path
            else f"workspace{WORKSPACE_FILE_SUFFIX}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self._app,
            "Save Workspace As",
            str(
                (
                    self._app._workspace_path.parent
                    if self._app._workspace_path
                    else saved_workspaces_dir()
                )
                / default_name
            ),
            f"Workspace files (*{WORKSPACE_FILE_SUFFIX});;JSON files (*.json)",
        )
        if not path:
            return False
        candidate = normalize_workspace_path(path)
        try:
            document = self._collect_workspace_document()
            write_json_file_atomic(candidate, document)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self._app, "Workspace Error", str(exc))
            return False
        self._app._workspace_path = candidate
        self._app._last_saved_document = self._collect_workspace_document()
        self._app._workspace_dirty = False
        self._app._has_unsaved_changes = False
        self._remember_workspace_path(candidate)
        self._update_title()
        self._app._autosave_controller._discard_autosave()
        self._app._autosave_controller._discard_restored_snapshot()
        return True

    def _update_title(self) -> None:
        self._app.setWindowTitle(
            workspace_title(self._app._workspace_path, self._app._workspace_dirty)
        )
        self._app._refresh_workspace_header()

    def _load_recent_workspace_action(self) -> None:
        action = self._app.sender()
        if not isinstance(action, QAction):
            return
        path_str = action.data()
        if not path_str:
            return
        self._load_workspace_file(Path(str(path_str)))

    def _confirm_discard_if_dirty(self) -> bool:
        self._update_workspace_dirty()
        if not self._app._workspace_dirty or not self._app._has_workspace_content():
            return True
        choice = QMessageBox.question(
            self._app,
            "Unsaved Workspace",
            "The current workspace has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._save_workspace()
        return True

    def _update_workspace_dirty(self) -> None:
        if not self._app._has_unsaved_changes:
            self._app._workspace_dirty = False
        elif self._app._last_saved_document is not None:
            self._app._workspace_dirty = (
                self._collect_workspace_document() != self._app._last_saved_document
            )
        else:
            # Recovered documents intentionally have no saved baseline. They
            # remain dirty until Save As establishes one; treating a missing
            # baseline as clean allowed restored work to close without warning.
            self._app._workspace_dirty = self._app._has_unsaved_changes
        self._update_title()

    def _workspace_pages(self):
        return self._page_runtime.iter_workspace_pages()

    def _preset_pages(self):
        return self._page_runtime.iter_preset_pages()

    def _rebuild_recent_workspaces_menu(self) -> None:
        self._app._recent_workspaces_menu.clear()
        recent = recent_workspace_paths(self._app._settings)
        if not recent:
            action = QAction("No recent workspaces", self._app._recent_workspaces_menu)
            action.setEnabled(False)
            self._app._recent_workspaces_menu.addAction(action)
            return
        for path in recent:
            action = QAction(path.name, self._app._recent_workspaces_menu)
            action.setData(str(path))
            action.setToolTip(str(path))
            action.triggered.connect(
                lambda _checked=False, recent_path=path: self._load_workspace_file(recent_path)
            )
            self._app._recent_workspaces_menu.addAction(action)

    def _load_workspace_file(self, path: Path, check_dirty: bool = True) -> None:
        if check_dirty and not self._confirm_discard_if_dirty():
            return
        try:
            data = read_json_file(
                path,
                default=None,
                max_bytes=MAX_WORKSPACE_FILE_BYTES,
            )
            if not isinstance(data, dict):
                raise TypeError("Workspace file is invalid or is not a JSON object.")
            self._apply_workspace_document(data)
            self._app._workspace_path = path
            self._app._last_saved_document = self._collect_workspace_document()
            self._app._workspace_dirty = False
            self._app._has_unsaved_changes = False
            self._remember_workspace_path(path)
            self._update_title()
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self._app, "Workspace Error", str(exc))

    def _open_workspace(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        path, _ = QFileDialog.getOpenFileName(
            self._app,
            "Open Workspace",
            self._workspace_default_dir(),
            f"Workspace files (*{WORKSPACE_FILE_SUFFIX});;JSON files (*.json);;All files (*)",
        )
        if path:
            self._load_workspace_file(Path(path), check_dirty=False)

    def _open_saved_workspaces(self, *, initial_source: str = "saved") -> None:
        active_path = self._app._workspace_path
        dialog = WorkspaceLibraryDialog(
            saved_workspaces_dir(),
            self._app,
            recent_paths=recent_workspace_paths(self._app._settings),
            recovery_dir=user_data_dir() / "recovery",
            initial_source=initial_source,
        )
        accepted = dialog.exec()
        if active_path in dialog.renamed_paths:
            self._app._workspace_path = dialog.renamed_paths[active_path]
            self._remember_workspace_path(self._app._workspace_path)
            self._update_title()
        elif active_path in dialog.deleted_paths:
            # Keep the open document in memory after its backing file is
            # deleted, but make the next save ask for a new name.
            self._app._workspace_path = None
            self._app._last_saved_document = None
            self._app._workspace_dirty = True
            self._app._has_unsaved_changes = True
            self._update_title()
        if accepted and dialog.selected_document is not None:
            if not self._confirm_discard_if_dirty():
                return
            try:
                self._apply_workspace_document(dialog.selected_document)
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(
                    self._app,
                    "Recovery Failed",
                    f"Could not restore this snapshot:\n{exc}",
                )
                return
            self._app._workspace_path = None
            self._app._last_saved_document = None
            self._app._workspace_dirty = True
            self._app._has_unsaved_changes = True
            self._app._restored_recovery_path = dialog.selected_path
            self._update_title()
        elif accepted and dialog.selected_path is not None:
            self._load_workspace_file(dialog.selected_path)

    def _clear_workspace_state(self) -> None:
        self._app._workspace_controller.clear()

    def _apply_workspace_document(self, document: dict) -> None:
        self._app._workspace_controller.apply(document)

    def _workspace_default_dir(self) -> str:
        return workspace_default_dir(self._app._settings)

    def _remember_workspace_path(self, path: Path) -> None:
        remember_workspace_path(settings=self._app._settings, path=path, max_recent=8)
        save_settings(self._app._settings)
        self._rebuild_recent_workspaces_menu()

    def _save_workspace(self) -> bool:
        if self._app._workspace_path is None:
            return self._save_workspace_as()
        try:
            document = self._collect_workspace_document()
            write_json_file_atomic(self._app._workspace_path, document)
            self._app._last_saved_document = document
            self._app._workspace_dirty = False
            self._app._has_unsaved_changes = False
            self._remember_workspace_path(self._app._workspace_path)
            self._update_title()
            self._app._autosave_controller._discard_autosave()
            self._app._autosave_controller._discard_restored_snapshot()
            return True
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self._app, "Workspace Error", str(exc))
            return False

    def _new_workspace(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self._app._workspace_path = None
        self._clear_workspace_state()
        self._app._last_saved_document = self._collect_workspace_document()
        self._app._workspace_dirty = False
        self._app._has_unsaved_changes = False
        self._update_title()
