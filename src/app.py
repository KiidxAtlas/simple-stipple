"""Main application window."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
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

from src.backend.document.state import (
    WORKSPACE_FILE_SUFFIX,
    normalize_workspace_path,
)
from src.backend.io import read_json_file, write_json_file_atomic
from src.settings import DEFAULT_KEYBINDINGS, load_settings, save_settings
from src.ui.components.common.command_palette import CommandPaletteDialog
from src.ui.components.common.factories import _info_chip, _surface_frame
from src.ui.components.common.update_dialog import UpdateDialog
from src.ui.shell.registry import PageSpec, default_page_specs
from src.ui.shell.runtime import PageRuntime
from src.ui.shell.settings_dialog import SettingsDialog
from src.ui.workspace.session import (
    apply_workspace_document,
    clear_workspace_state,
    collect_workspace_document,
    recent_workspace_paths,
    remember_workspace_path,
    workspace_default_dir,
    workspace_title,
)

LOGGER = logging.getLogger(__name__)


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

        # Periodic auto-fetch while app is open (when enabled in settings)
        self._auto_fetch_timer = QTimer(self)
        self._auto_fetch_timer.setSingleShot(False)
        self._auto_fetch_timer.timeout.connect(self._attempt_auto_fetch)

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

        self._page_specs: tuple[PageSpec, ...] = default_page_specs()
        self._page_runtime = PageRuntime(
            tab_widget=self._tabs,
            settings=self._settings,
            specs=self._page_specs,
        )
        self._init_tab_bindings()
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

    def _init_tab_bindings(self) -> None:
        self._draft_page: Any = self._page_runtime.get("draft")
        self._pattern_page: Any = self._page_runtime.get("pattern")
        self._trace_page: Any = self._page_runtime.get("trace")
        self._convert_page: Any = self._page_runtime.get("convert")
        self._repo_page: Any = self._page_runtime.get("repo")

        self._page_runtime.connect_state_changed(self._schedule_workspace_dirty_check)

        self._page_runtime.connect_signal_if_present(
            page_id="draft",
            signal_name="sendSelectedToPatternRequested",
            slot=self._send_shape_selection_to_pattern,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="draft",
            signal_name="useSelectedAsFillPatternRequested",
            slot=self._use_shape_selection_as_fill_pattern,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="pattern",
            signal_name="sendSelectedToDraftRequested",
            slot=self._send_pattern_selection_to_draft,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="trace",
            signal_name="sendSelectedToDraftRequested",
            slot=self._send_pattern_selection_to_draft,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="trace",
            signal_name="sendSelectedToPatternRequested",
            slot=self._send_shape_selection_to_pattern,
        )

    def _switch_to_page(self, page_id: str) -> None:
        self._page_runtime.switch_to(page_id)

    def _has_workspace_content(self) -> bool:
        return self._page_runtime.has_workspace_content()

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
        # Disable save actions if there's no workspace content
        has_content = self._has_workspace_content()
        self._save_workspace_action.setEnabled(has_content and self._workspace_dirty)

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
        return workspace_default_dir(self._settings)

    def _workspace_pages(self):
        return self._page_runtime.iter_workspace_pages()

    def _preset_pages(self):
        return self._page_runtime.iter_preset_pages()

    def _collect_workspace_document(self) -> dict:
        return collect_workspace_document(
            workspace_path=self._workspace_path,
            current_tab_index=self._tabs.currentIndex(),
            workspace_pages=self._workspace_pages(),
            preset_pages=self._preset_pages(),
        )

    def _apply_workspace_document(self, document: dict) -> None:
        apply_workspace_document(
            document=document,
            workspace_pages=self._workspace_pages(),
            preset_pages=self._preset_pages(),
            tab_count=self._tabs.count(),
            set_current_tab_index=self._tabs.setCurrentIndex,
        )

    def _clear_workspace_state(self) -> None:
        clear_workspace_state(
            workspace_pages=self._workspace_pages(),
            set_current_tab_index=self._tabs.setCurrentIndex,
        )

    def _schedule_workspace_dirty_check(self) -> None:
        self._workspace_timer.start(150)

    def _send_shape_selection_to_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self._pattern_page.load_outline_polys(polys, source_label="Draft selection")
        self._tabs.setCurrentWidget(self._pattern_page)
        self._schedule_workspace_dirty_check()

    def _send_pattern_selection_to_draft(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        self._draft_page.load_outline_polys(polys, source_label="Pattern selection")
        self._tabs.setCurrentWidget(self._draft_page)
        self._schedule_workspace_dirty_check()

    def _use_shape_selection_as_fill_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if not polys:
            return
        if self._pattern_page.use_polys_as_fill_pattern(
            polys,
            source_label="Draft selection",
        ):
            self._tabs.setCurrentWidget(self._pattern_page)
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
        self.setWindowTitle(
            workspace_title(self._workspace_path, self._workspace_dirty)
        )
        self._refresh_workspace_header()

    def _remember_workspace_path(self, path: Path) -> None:
        remember_workspace_path(settings=self._settings, path=path, max_recent=8)
        save_settings(self._settings)
        self._rebuild_recent_workspaces_menu()

    def _rebuild_recent_workspaces_menu(self) -> None:
        self._recent_workspaces_menu.clear()
        recent = recent_workspace_paths(self._settings)
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
        # Don't prompt for save if there's no content to save
        if not self._workspace_dirty or not self._has_workspace_content():
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

        # Keep fetching periodically while app remains open (if enabled)
        self._configure_auto_fetch_timer()

        # Check for updates on startup if setting is enabled
        if self._settings.get("check_updates_on_startup", False):
            QTimer.singleShot(1000, self._attempt_startup_update_check)

    def _attempt_auto_fetch(self) -> None:
        """Attempt to fetch remote repository metadata without altering the working tree."""
        try:
            self._repo_page.auto_fetch()
        except (OSError, RuntimeError, ValueError) as exc:
            LOGGER.warning("Auto-fetch failed: %s", exc)

    def _auto_fetch_interval_ms(self) -> int:
        """Return periodic auto-fetch interval in milliseconds."""
        raw = self._settings.get("auto_fetch_interval_minutes", 10)
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            minutes = 10
        minutes = max(1, minutes)
        return minutes * 60_000

    def _configure_auto_fetch_timer(self) -> None:
        """Start/stop periodic auto-fetch based on current settings."""
        enabled = bool(self._settings.get("auto_fetch_periodic", False))
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
        for spec in self._page_specs:
            self._register_action(
                spec.shortcut_id,
                f"Switch to {spec.title} Page",
                lambda checked=False, page_id=spec.page_id: self._switch_to_page(
                    page_id
                ),
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
        commands = [
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
        ]
        for spec in self._page_specs:
            commands.append(
                self._command_entry(
                    command_id=spec.shortcut_id,
                    title=spec.command_title,
                    keywords=spec.command_keywords,
                    run=lambda page_id=spec.page_id: self._switch_to_page(page_id),
                )
            )
        return commands

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
                LOGGER.warning("Auto-save failed: %s", exc)
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
            self._page_runtime.apply_settings(self._settings)
            self._repo_page.sync_from_settings()

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

            self._configure_auto_fetch_timer()

    def _open_update_check(self) -> None:
        """Open the update check dialog."""
        dlg = UpdateDialog(self)
        dlg.exec()
