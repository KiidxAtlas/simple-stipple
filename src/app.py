"""Main application window."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.document.state import (
    WORKSPACE_FILE_SUFFIX,
    build_workspace_document,
    normalize_workspace_path,
    validate_workspace_document,
)
from src.core.io import read_json_file, write_json_file_atomic
from src.settings import DEFAULT_KEYBINDINGS, load_settings, save_settings
from src.ui.components.factories import _info_chip, _surface_frame
from src.ui.dialogs.command_palette import CommandPaletteDialog
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.ui.dialogs.update_dialog import UpdateDialog
from src.ui.style.theme import apply_dark_theme
from src.ui.tabs.convert_tab import UtilitiesTab
from src.ui.tabs.draft_tab import ShapeTab
from src.ui.tabs.pattern import PatternTab
from src.ui.tabs.repo_tab import RepoTab
from src.ui.tabs.trace_tab import ImageTab


def _apply_dark_palette(app: QApplication) -> None:
    """Backward-compatible theming entrypoint used by app bootstrap."""
    apply_dark_theme(app)


class App(QMainWindow):
    """Top-level main window coordinating workspace state and cross-tab actions."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("AA Laser Studio")
        self.resize(1100, 740)
        self.setMinimumSize(860, 580)

        self._settings = load_settings()
        self._settings.setdefault("keybindings", dict(DEFAULT_KEYBINDINGS))
        self._workspace_path: Path | None = None
        self._workspace_dirty: bool = False
        self._last_saved_document: dict | None = None
        self._workspace_timer = QTimer(self)
        self._workspace_timer.setSingleShot(True)
        self._workspace_timer.timeout.connect(self._update_workspace_dirty)

        # Auto-save every 60 seconds if workspace has a path and is dirty
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 8)
        central_layout.setSpacing(6)
        self.setCentralWidget(central)

        self._shell_header = self._build_shell_header()
        central_layout.addWidget(self._shell_header)

        # ── Tabs ──────────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        central_layout.addWidget(self._tabs, stretch=1)

        self._utilities_tab = UtilitiesTab(settings=self._settings)
        self._pattern_tab = PatternTab(settings=self._settings)
        self._shape_tab = ShapeTab(settings=self._settings)
        self._image_tab = ImageTab(settings=self._settings)
        self._repo_tab = RepoTab(settings=self._settings)

        self._tabs.addTab(self._shape_tab, "Draft")
        self._tabs.addTab(self._pattern_tab, "Pattern Fill")
        self._tabs.addTab(self._image_tab, "Trace")
        self._tabs.addTab(self._utilities_tab, "Convert")
        self._tabs.addTab(self._repo_tab, "Repo")

        for tab in (
            self._pattern_tab,
            self._shape_tab,
            self._image_tab,
            self._repo_tab,
        ):
            tab.stateChanged.connect(self._schedule_workspace_dirty_check)
        self._shape_tab.sendSelectedToPatternRequested.connect(
            self._send_shape_selection_to_pattern
        )
        self._shape_tab.useSelectedAsFillPatternRequested.connect(
            self._use_shape_selection_as_fill_pattern
        )
        self._pattern_tab.sendSelectedToDraftRequested.connect(
            self._send_pattern_selection_to_draft
        )
        self._image_tab.sendSelectedToDraftRequested.connect(
            self._send_pattern_selection_to_draft
        )
        self._image_tab.sendSelectedToPatternRequested.connect(
            self._send_shape_selection_to_pattern
        )
        self._tabs.currentChanged.connect(self._schedule_workspace_dirty_check)
        self._tabs.currentChanged.connect(lambda _: self._refresh_workspace_header())

        self._workspace_menu = self.menuBar().addMenu("File")
        self._recent_workspaces_menu = self._workspace_menu.addMenu("Open Recent")
        self._build_workspace_actions()
        self._rebuild_recent_workspaces_menu()

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        self._global_actions: dict[str, QAction] = {}
        self._setup_global_shortcuts()

        self._clear_workspace_state()
        self._last_saved_document = self._collect_workspace_document()
        self._update_title()

    def _has_workspace_content(self) -> bool:
        shape_canvas = getattr(self._shape_tab, "_canvas", None)
        pattern_canvas = getattr(self._pattern_tab, "_canvas", None)
        trace_canvas = getattr(self._image_tab, "_canvas", None)
        util_canvas = getattr(self._utilities_tab, "_preview_canvas", None)
        return any(
            bool(getattr(canvas, "poly_count", 0))
            for canvas in (shape_canvas, pattern_canvas, trace_canvas, util_canvas)
            if canvas is not None
        )

    def _build_shell_header(self) -> QWidget:
        shell = _surface_frame("panel")
        shell.setProperty("role", "hero")
        layout = QHBoxLayout(shell)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        # App identity — compact single line
        title = QLabel("AA Laser Studio")
        title.setProperty("role", "shell-title")
        layout.addWidget(title)

        # Separator
        sep = QLabel("·")
        sep.setStyleSheet("color: #30363d; font-size: 18px;")
        layout.addWidget(sep)

        # Workspace name
        self._workspace_title_label = QLabel()
        self._workspace_title_label.setProperty("role", "shell-meta")
        layout.addWidget(self._workspace_title_label)

        # Status chip
        self._workspace_state_chip = _info_chip("Saved", "success")
        layout.addWidget(self._workspace_state_chip)

        layout.addStretch()

        # File actions — grouped tightly
        for text, slot, role in [
            ("New", self._new_workspace, None),
            ("Open", self._open_workspace, None),
            ("Save", self._save_workspace, "primary"),
            ("Save As", self._save_workspace_as, None),
        ]:
            btn = QPushButton(text)
            btn.setMinimumHeight(30)
            if role:
                btn.setProperty("role", role)
            btn.clicked.connect(slot)
            shortcut_hint = {
                "New": "workspace.new",
                "Open": "workspace.open",
                "Save": "workspace.save",
                "Save As": "workspace.save_as",
            }.get(text)
            if shortcut_hint:
                btn.setToolTip(f"{text} ({self._shortcut(shortcut_hint)})")
            layout.addWidget(btn)

        # Settings — visually separated
        update_btn = QPushButton("↓")
        update_btn.setFixedSize(30, 30)
        update_btn.setToolTip("Check for updates")
        update_btn.clicked.connect(self._open_update_check)
        layout.addWidget(update_btn)

        palette_btn = QPushButton("⌘K")
        palette_btn.setFixedSize(44, 30)
        palette_btn.setToolTip(
            f"Command palette ({self._shortcut('app.command_palette')})"
        )
        palette_btn.clicked.connect(self._open_command_palette)
        layout.addWidget(palette_btn)

        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(30, 30)
        settings_btn.setToolTip(f"Settings ({self._shortcut('app.settings')})")
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)

        return shell

    def _refresh_workspace_header(self) -> None:
        title = self._workspace_path.stem if self._workspace_path else "Untitled"
        self._workspace_title_label.setText(title)
        chip_text = "Unsaved" if self._workspace_dirty else "Saved"
        chip_tone = "warn" if self._workspace_dirty else "success"
        self._workspace_state_chip.setText(chip_text)
        self._workspace_state_chip.setProperty("tone", chip_tone)
        self._workspace_state_chip.style().unpolish(self._workspace_state_chip)
        self._workspace_state_chip.style().polish(self._workspace_state_chip)

    def _build_workspace_actions(self) -> None:
        self._new_workspace_action = QAction("New Workspace", self)
        self._new_workspace_action.setShortcut(
            QKeySequence(self._shortcut("workspace.new"))
        )
        self._new_workspace_action.triggered.connect(self._new_workspace)
        self._workspace_menu.addAction(self._new_workspace_action)

        self._open_workspace_action = QAction("Open Workspace…", self)
        self._open_workspace_action.setShortcut(
            QKeySequence(self._shortcut("workspace.open"))
        )
        self._open_workspace_action.triggered.connect(self._open_workspace)
        self._workspace_menu.addAction(self._open_workspace_action)

        self._save_workspace_action = QAction("Save Workspace", self)
        self._save_workspace_action.setShortcut(
            QKeySequence(self._shortcut("workspace.save"))
        )
        self._save_workspace_action.triggered.connect(self._save_workspace)
        self._workspace_menu.addAction(self._save_workspace_action)

        self._save_workspace_as_action = QAction("Save Workspace As…", self)
        self._save_workspace_as_action.setShortcut(
            QKeySequence(self._shortcut("workspace.save_as"))
        )
        self._save_workspace_as_action.triggered.connect(self._save_workspace_as)
        self._workspace_menu.addAction(self._save_workspace_as_action)

        self._workspace_menu.addSeparator()

    def _workspace_default_dir(self) -> str:
        return self._settings.get(
            "workspace_dir",
            self._settings.get("last_workspace_dir", str(Path.home())),
        )

    def _collect_workspace_document(self) -> dict:
        workspace_name = (
            self._workspace_path.stem.replace(
                WORKSPACE_FILE_SUFFIX.replace(".json", ""), ""
            )
            if self._workspace_path
            else "Untitled Workspace"
        )
        return build_workspace_document(
            workspace_name=workspace_name,
            app_state={"current_tab": self._tabs.currentIndex()},
            tab_states={
                "shape": self._shape_tab.get_workspace_state(),
                "pattern": self._pattern_tab.get_workspace_state(),
                "image": self._image_tab.get_workspace_state(),
                "utilities": self._utilities_tab.get_workspace_state(),
                "repo": self._repo_tab.get_workspace_state(),
            },
            preset_state={
                "shape": self._shape_tab.get_preset_state(),
                "pattern": self._pattern_tab.get_preset_state(),
            },
            meta={
                "workspace_path": str(self._workspace_path)
                if self._workspace_path
                else ""
            },
        )

    def _apply_workspace_document(self, document: dict) -> None:
        data = validate_workspace_document(document)
        self._shape_tab.apply_preset_state(data.get("presets", {}).get("shape", {}))
        self._pattern_tab.apply_preset_state(data.get("presets", {}).get("pattern", {}))
        tabs = data.get("tabs", {})
        self._shape_tab.apply_workspace_state(tabs.get("shape", {}))
        self._pattern_tab.apply_workspace_state(tabs.get("pattern", {}))
        self._image_tab.apply_workspace_state(tabs.get("image", {}))
        self._utilities_tab.apply_workspace_state(tabs.get("utilities", {}))
        self._repo_tab.apply_workspace_state(tabs.get("repo", {}))
        idx = int(data.get("app", {}).get("current_tab", 0))
        self._tabs.setCurrentIndex(max(0, min(idx, self._tabs.count() - 1)))

    def _clear_workspace_state(self) -> None:
        self._shape_tab.clear_workspace_state()
        self._pattern_tab.clear_workspace_state()
        self._image_tab.clear_workspace_state()
        self._utilities_tab.clear_workspace_state()
        self._repo_tab.clear_workspace_state()
        self._tabs.setCurrentIndex(0)

    def _schedule_workspace_dirty_check(self) -> None:
        self._workspace_timer.start(150)

    def _send_shape_selection_to_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self._pattern_tab.load_outline_polys(polys, source_label="Draft selection")
        self._tabs.setCurrentWidget(self._pattern_tab)
        self._schedule_workspace_dirty_check()

    def _send_pattern_selection_to_draft(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self._shape_tab.load_outline_polys(polys, source_label="Pattern selection")
        self._tabs.setCurrentWidget(self._shape_tab)
        self._schedule_workspace_dirty_check()

    def _use_shape_selection_as_fill_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        if self._pattern_tab.use_polys_as_fill_pattern(
            polys,
            source_label="Draft selection",
        ):
            self._tabs.setCurrentWidget(self._pattern_tab)
            self._schedule_workspace_dirty_check()

    def _update_workspace_dirty(self) -> None:
        if self._last_saved_document is None:
            self._workspace_dirty = False
        else:
            self._workspace_dirty = (
                self._collect_workspace_document() != self._last_saved_document
            )
        self._update_title()

    def _update_title(self) -> None:
        if self._workspace_path:
            name = self._workspace_path.name
        else:
            name = "Untitled Workspace"
        dirty = " *" if self._workspace_dirty else ""
        self.setWindowTitle(f"AA Laser Studio — {name}{dirty}")
        self._refresh_workspace_header()

    def _remember_workspace_path(self, path: Path) -> None:
        # Preserve user-configured workspace_dir from Settings; keep runtime
        # navigation convenience in a separate key.
        self._settings["last_workspace_dir"] = str(path.parent)
        self._settings["current_workspace"] = str(path)
        recent = [
            p for p in self._settings.get("recent_workspaces", []) if p != str(path)
        ]
        recent.insert(0, str(path))
        self._settings["recent_workspaces"] = recent[:8]
        save_settings(self._settings)
        self._rebuild_recent_workspaces_menu()

    def _rebuild_recent_workspaces_menu(self) -> None:
        self._recent_workspaces_menu.clear()
        recent = [
            Path(path)
            for path in self._settings.get("recent_workspaces", [])
            if Path(path).exists()
        ]
        if not recent:
            action = QAction("No recent workspaces", self)
            action.setEnabled(False)
            self._recent_workspaces_menu.addAction(action)
            return
        for path in recent:
            self._recent_workspaces_menu.addAction(
                path.name,
                lambda checked=False, p=path: self._load_workspace_file(p),
            )

    def _confirm_discard_if_dirty(self) -> bool:
        self._update_workspace_dirty()
        if not self._workspace_dirty:
            return True
        choice = QMessageBox.question(
            self,
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

    def _new_workspace(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self._workspace_path = None
        self._clear_workspace_state()
        self._last_saved_document = self._collect_workspace_document()
        self._workspace_dirty = False
        self._update_title()

    def _open_workspace(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Workspace",
            self._workspace_default_dir(),
            f"Workspace files (*{WORKSPACE_FILE_SUFFIX});;JSON files (*.json);;All files (*)",
        )
        if path:
            self._load_workspace_file(Path(path), check_dirty=False)

    def _load_workspace_file(self, path: Path, check_dirty: bool = True) -> None:
        if check_dirty and not self._confirm_discard_if_dirty():
            return
        try:
            data = read_json_file(path, default={})
            if not isinstance(data, dict):
                raise TypeError("Workspace file is not a JSON object.")
            self._apply_workspace_document(data)
            self._workspace_path = path
            self._last_saved_document = self._collect_workspace_document()
            self._workspace_dirty = False
            self._remember_workspace_path(path)
            self._update_title()
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Workspace Error", str(exc))

    def _save_workspace(self) -> bool:
        if self._workspace_path is None:
            return self._save_workspace_as()
        try:
            document = self._collect_workspace_document()
            write_json_file_atomic(self._workspace_path, document)
            self._last_saved_document = document
            self._workspace_dirty = False
            self._remember_workspace_path(self._workspace_path)
            self._update_title()
            return True
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Workspace Error", str(exc))
            return False

    def _save_workspace_as(self) -> bool:
        default_name = (
            self._workspace_path.name
            if self._workspace_path
            else f"workspace{WORKSPACE_FILE_SUFFIX}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Workspace As",
            str(Path(self._workspace_default_dir()) / default_name),
            f"Workspace files (*{WORKSPACE_FILE_SUFFIX});;JSON files (*.json)",
        )
        if not path:
            return False
        self._workspace_path = normalize_workspace_path(path)
        return self._save_workspace()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard_if_dirty():
            event.accept()
            return
        event.ignore()

    def showEvent(self, event) -> None:
        """Handle window show event — perform startup tasks like auto-fetch and update check."""
        super().showEvent(event)
        if hasattr(self, "_startup_done"):
            return
        self._startup_done = True

        # Auto-fetch repository metadata on startup if setting is enabled
        if self._settings.get("auto_fetch_on_startup", False):
            QTimer.singleShot(500, self._attempt_auto_fetch)

        # Check for updates on startup if setting is enabled
        if self._settings.get("check_updates_on_startup", False):
            QTimer.singleShot(1000, self._attempt_startup_update_check)

    def _attempt_auto_fetch(self) -> None:
        """Attempt to fetch remote repository metadata without altering the working tree."""
        try:
            self._repo_tab.auto_fetch()
        except (OSError, RuntimeError, ValueError) as exc:
            logging.warning("Auto-fetch failed: %s", exc)

    def _attempt_startup_update_check(self) -> None:
        """Check for updates on startup (silent if up-to-date)."""
        from src.updates import check_for_updates

        info = check_for_updates(timeout=8)
        if info and info.is_newer:
            # Only show dialog if there's an update available
            dlg = UpdateDialog(self, info)
            dlg.exec()

    def _shortcut(self, action_id: str) -> str:
        keybindings = self._settings.get("keybindings", {})
        if not isinstance(keybindings, dict):
            keybindings = {}
        value = keybindings.get(action_id)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return DEFAULT_KEYBINDINGS.get(action_id, "")

    def _setup_global_shortcuts(self) -> None:
        self._register_action("app.settings", "Settings", self._open_settings)
        self._register_action(
            "app.command_palette",
            "Command Palette",
            self._open_command_palette,
        )
        self._register_fixed_shortcut(
            "Command Palette (Cmd+K)",
            "Meta+K",
            self._open_command_palette,
        )
        self._register_action(
            "canvas.select_mode",
            "Canvas Mode Select",
            lambda: self._invoke_canvas_mode("select"),
        )
        self._register_action(
            "canvas.draw_mode",
            "Canvas Mode Draw",
            lambda: self._invoke_canvas_mode("draw"),
        )
        self._register_action(
            "canvas.edit_mode",
            "Canvas Mode Edit",
            lambda: self._invoke_canvas_mode("edit"),
        )
        self._register_action(
            "canvas.measure",
            "Canvas Toggle Measure",
            self._invoke_canvas_measure,
        )
        self._register_action(
            "canvas.fit",
            "Canvas Fit",
            self._invoke_canvas_fit,
        )
        self._register_action(
            "tab.draft",
            "Switch to Draft Tab",
            lambda: self._tabs.setCurrentWidget(self._shape_tab),
        )
        self._register_action(
            "tab.pattern",
            "Switch to Pattern Tab",
            lambda: self._tabs.setCurrentWidget(self._pattern_tab),
        )
        self._register_action(
            "tab.trace",
            "Switch to Trace Tab",
            lambda: self._tabs.setCurrentWidget(self._image_tab),
        )
        self._register_action(
            "tab.convert",
            "Switch to Convert Tab",
            lambda: self._tabs.setCurrentWidget(self._utilities_tab),
        )
        self._register_action(
            "tab.repo",
            "Switch to Repo Tab",
            lambda: self._tabs.setCurrentWidget(self._repo_tab),
        )

    def _register_action(self, action_id: str, text: str, callback) -> None:
        action = QAction(text, self)
        shortcut = self._shortcut(action_id)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        self.addAction(action)
        self._global_actions[action_id] = action

    def _register_fixed_shortcut(self, text: str, shortcut: str, callback) -> None:
        action = QAction(text, self)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        self.addAction(action)

    def _active_canvas(self) -> Any | None:
        current = self._tabs.currentWidget()
        return getattr(current, "_canvas", None)

    def _command_entry(
        self,
        *,
        command_id: str,
        title: str,
        keywords: str,
        run,
    ) -> dict[str, object]:
        """Create a command-palette entry with a settings-backed shortcut."""
        return {
            "id": command_id,
            "title": title,
            "shortcut": self._shortcut(command_id),
            "keywords": keywords,
            "run": run,
        }

    def _build_command_palette_commands(self) -> list[dict[str, object]]:
        """Return command-palette entries exposed to users."""
        return [
            self._command_entry(
                command_id="workspace.new",
                title="Workspace: New",
                keywords="new workspace file",
                run=self._new_workspace,
            ),
            self._command_entry(
                command_id="workspace.open",
                title="Workspace: Open",
                keywords="open workspace file",
                run=self._open_workspace,
            ),
            self._command_entry(
                command_id="workspace.save",
                title="Workspace: Save",
                keywords="save workspace",
                run=self._save_workspace,
            ),
            self._command_entry(
                command_id="workspace.save_as",
                title="Workspace: Save As",
                keywords="save as workspace",
                run=self._save_workspace_as,
            ),
            self._command_entry(
                command_id="app.settings",
                title="App: Settings",
                keywords="settings preferences",
                run=self._open_settings,
            ),
            self._command_entry(
                command_id="canvas.select_mode",
                title="Canvas: Mode Select",
                keywords="canvas select mode",
                run=lambda: self._invoke_canvas_mode("select"),
            ),
            self._command_entry(
                command_id="canvas.draw_mode",
                title="Canvas: Mode Draw",
                keywords="canvas draw mode",
                run=lambda: self._invoke_canvas_mode("draw"),
            ),
            self._command_entry(
                command_id="canvas.edit_mode",
                title="Canvas: Mode Edit",
                keywords="canvas edit mode",
                run=lambda: self._invoke_canvas_mode("edit"),
            ),
            self._command_entry(
                command_id="canvas.measure",
                title="Canvas: Toggle Measure",
                keywords="canvas measure",
                run=self._invoke_canvas_measure,
            ),
            self._command_entry(
                command_id="canvas.fit",
                title="Canvas: Fit View",
                keywords="canvas fit zoom",
                run=self._invoke_canvas_fit,
            ),
            self._command_entry(
                command_id="tab.draft",
                title="Tab: Draft",
                keywords="tab draft",
                run=lambda: self._tabs.setCurrentWidget(self._shape_tab),
            ),
            self._command_entry(
                command_id="tab.pattern",
                title="Tab: Pattern Fill",
                keywords="tab pattern",
                run=lambda: self._tabs.setCurrentWidget(self._pattern_tab),
            ),
            self._command_entry(
                command_id="tab.trace",
                title="Tab: Trace",
                keywords="tab trace",
                run=lambda: self._tabs.setCurrentWidget(self._image_tab),
            ),
            self._command_entry(
                command_id="tab.convert",
                title="Tab: Convert",
                keywords="tab convert utilities",
                run=lambda: self._tabs.setCurrentWidget(self._utilities_tab),
            ),
            self._command_entry(
                command_id="tab.repo",
                title="Tab: Repo",
                keywords="tab repo git",
                run=lambda: self._tabs.setCurrentWidget(self._repo_tab),
            ),
        ]

    def _invoke_canvas_mode(self, mode: str) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "set_mode"):
            canvas.set_mode(mode)

    def _invoke_canvas_measure(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "toggle_measure"):
            canvas.toggle_measure()

    def _invoke_canvas_fit(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "fit"):
            canvas.fit()

    def _open_command_palette(self) -> None:
        dlg = CommandPaletteDialog(self._build_command_palette_commands(), self)
        dlg.exec()

    def _autosave(self) -> None:
        """Auto-save workspace if it has a path and is dirty."""
        if self._workspace_path and self._workspace_dirty:
            try:
                document = self._collect_workspace_document()
                write_json_file_atomic(self._workspace_path, document)
                self._last_saved_document = document
                self._workspace_dirty = False
                self._update_title()
            except (OSError, TypeError, ValueError) as exc:
                logging.warning("Auto-save failed: %s", exc)
                self._workspace_state_chip.setText("Auto-save failed")
                self._workspace_state_chip.setProperty("tone", "danger")
                self._workspace_state_chip.style().unpolish(self._workspace_state_chip)
                self._workspace_state_chip.style().polish(self._workspace_state_chip)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self, self._settings)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            # Propagate changed settings to tabs that cache paths at init time.
            self._settings = dlg._settings
            self._settings.setdefault("keybindings", dict(DEFAULT_KEYBINDINGS))
            for tab in (
                self._utilities_tab,
                self._pattern_tab,
                self._shape_tab,
                self._image_tab,
                self._repo_tab,
            ):
                tab._settings = self._settings
            self._repo_tab.sync_from_settings()

            for action_id, action in self._global_actions.items():
                shortcut = self._shortcut(action_id)
                action.setShortcut(
                    QKeySequence(shortcut) if shortcut else QKeySequence()
                )

            self._new_workspace_action.setShortcut(
                QKeySequence(self._shortcut("workspace.new"))
            )
            self._open_workspace_action.setShortcut(
                QKeySequence(self._shortcut("workspace.open"))
            )
            self._save_workspace_action.setShortcut(
                QKeySequence(self._shortcut("workspace.save"))
            )
            self._save_workspace_as_action.setShortcut(
                QKeySequence(self._shortcut("workspace.save_as"))
            )

    def _open_update_check(self) -> None:
        """Open the update check dialog."""
        dlg = UpdateDialog(self)
        dlg.exec()
