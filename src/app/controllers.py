"""Small App-owned background helper controllers, split out of ``App``
itself to keep that class focused on high-level coordination.

Two previously-separate modules merged here — both are the same kind of
thing (a small controller ``App`` delegates a background-timer concern to),
each with no other consumer than ``App``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.backend.document import WORKSPACE_FILE_SUFFIX, normalize_workspace_path
from src.backend.persistence import read_json_file, write_json_file_atomic
from src.infra.paths import user_data_dir
from src.infra.settings import (
    DEFAULT_KEYBINDINGS,
    DEFAULT_RADIAL_MENU_TOOLS,
    save_settings,
    settings_bus,
)
from src.ui.canvas.interaction import commands as canvas_commands
from src.ui.components import download_icon, gear_icon, info_chip, surface_frame
from src.ui.widgets.command_palette import CommandPaletteDialog
from src.ui.widgets.settings_dialog import SettingsDialog
from src.ui.widgets.update_dialog import UpdateDialog
from src.ui.workspace_session import (
    apply_workspace_document,
    clear_workspace_state,
    collect_workspace_document,
    recent_workspace_paths,
    remember_workspace_path,
    workspace_default_dir,
    workspace_title,
)

if TYPE_CHECKING:
    from src.app import App

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandSpec:
    action_id: str
    title: str
    keywords: str
    run: Callable[[], Any]


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
        apply_workspace_document(
            document=document,
            workspace_pages=self._page_runtime.iter_workspace_pages(),
            preset_pages=self._page_runtime.iter_preset_pages(),
            tab_count=self._tabs.count(),
            set_current_tab_index=self._tabs.setCurrentIndex,
        )

    def clear(self) -> None:
        clear_workspace_state(
            workspace_pages=self._page_runtime.iter_workspace_pages(),
            set_current_tab_index=self._tabs.setCurrentIndex,
        )


class SettingsController:
    """Own persistence and canvas fan-out for application settings."""

    def __init__(self, settings: dict, page_runtime, *, source: object) -> None:
        self.settings = settings
        self._page_runtime = page_runtime
        self._source = source

    def replace(self, settings: dict) -> None:
        previous = dict(self.settings)
        incoming = dict(settings)
        self.settings.clear()
        self.settings.update(incoming)
        self._page_runtime.apply_all(self.settings)
        for key, value in self.settings.items():
            if previous.get(key) != value:
                settings_bus.publish(key, value, self._source)

    def update(self, key: str, value) -> None:
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        save_settings(self.settings)
        self._page_runtime.apply(key, value)
        settings_bus.publish(key, value, self._source)


class AutosaveController:
    """Manages periodic workspace snapshots for crash recovery and regular autosaving."""

    _recovery_offered = False

    def __init__(self, app: App) -> None:
        self._app = app

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
                self._app._workspace_state_chip.setText("Auto-save failed")
                self._app._workspace_state_chip.setProperty("tone", "danger")
                self._app._workspace_state_chip.style().unpolish(self._app._workspace_state_chip)
                self._app._workspace_state_chip.style().polish(self._app._workspace_state_chip)

    def _discard_autosave(self) -> None:
        try:
            self._app._autosave_path().unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("Could not remove autosave: %s", exc)

    def _offer_autosave_recovery(self) -> None:
        if AutosaveController._recovery_offered:
            return
        AutosaveController._recovery_offered = True
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
                timestamp = str(metadata.get("timestamp", "Unknown time"))
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
            path.unlink(missing_ok=True)
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
        from src.ui.widgets.update_dialog import UpdateCheckThread

        self._startup_update_thread = UpdateCheckThread(self._app)
        self._startup_update_thread.checkComplete.connect(self._on_startup_complete)
        self._startup_update_thread.start()

    def _on_startup_complete(self, info) -> None:
        if info and info.is_newer:
            from src.ui.widgets.update_dialog import UpdateDialog

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
        QTimer.singleShot(200, self.autosave._offer_autosave_recovery)
        if check_updates:
            QTimer.singleShot(1000, self.updates._attempt_startup_update_check)
        self.updates._configure_auto_fetch_timer()

    def shutdown(self) -> None:
        self.autosave.shutdown()
        self.updates.shutdown()


class _AppProxy:
    """Forward controller state access to its composed App window."""

    _settings: dict

    def __init__(self, app: App) -> None:
        object.__setattr__(self, "_app", app)

    def __getattr__(self, name: str):
        return getattr(self._app, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "_app":
            object.__setattr__(self, name, value)
        else:
            setattr(self._app, name, value)


class MenuController(_AppProxy):
    """Own shell header and menu presentation."""

    def _build_edit_view_help_menus(self) -> None:
        """Standard menus routing to the active page's canvas.

        Text-editing shortcuts (undo/cut/copy/paste/select-all) check the
        focused widget first so they never hijack typing in a line edit.
        """
        edit_menu = self.menuBar().addMenu("Edit")

        def add(menu, text, slot, shortcut=None):
            action = QAction(text, self._app)
            if shortcut is not None:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            return action

        add(edit_menu, "Undo", self._menu_undo, QKeySequence.StandardKey.Undo)
        add(edit_menu, "Redo", self._menu_redo, QKeySequence.StandardKey.Redo)
        edit_menu.addSeparator()
        add(edit_menu, "Cut", self._menu_cut, QKeySequence.StandardKey.Cut)
        add(edit_menu, "Copy", self._menu_copy, QKeySequence.StandardKey.Copy)
        add(edit_menu, "Paste", self._menu_paste, QKeySequence.StandardKey.Paste)

        def add_cmd(menu, cmd_id, *, label=None):
            cmd = canvas_commands.get(cmd_id)
            return add(
                menu,
                label or cmd.label,
                lambda: self._run_canvas_command(cmd_id),
                QKeySequence(cmd.shortcut) if cmd.shortcut else None,
            )

        add_cmd(edit_menu, "edit.duplicate")
        # Delete intentionally has no global shortcut: Backspace must keep
        # working in text fields; the canvas handles it when focused.
        add(edit_menu, "Delete Selected", lambda: self._canvas_call("delete_selected"))
        edit_menu.addSeparator()
        add(
            edit_menu,
            "Select All",
            self._menu_select_all,
            QKeySequence.StandardKey.SelectAll,
        )
        add_cmd(edit_menu, "select.none")

        view_menu = self.menuBar().addMenu("View")
        # Single-letter canvas keys (F/S/D/E/M) stay widget-local; the menu
        # spells them out instead of registering global shortcuts.
        add(view_menu, "Fit View (F)", lambda: self._run_canvas_command("view.fit"))
        add(
            view_menu,
            "Zoom In",
            lambda: self._run_canvas_command("view.zoom_in"),
            QKeySequence.StandardKey.ZoomIn,
        )
        add(
            view_menu,
            "Zoom Out",
            lambda: self._run_canvas_command("view.zoom_out"),
            QKeySequence.StandardKey.ZoomOut,
        )
        view_menu.addSeparator()
        self._grid_action = QAction("Show Grid", self._app, checkable=True)
        self._grid_action.triggered.connect(
            lambda checked: self._canvas_call("set_grid_visible", checked)
        )
        view_menu.addAction(self._grid_action)
        self._snap_action = QAction("Snap to Grid", self._app, checkable=True)
        self._snap_action.triggered.connect(
            lambda checked: self._canvas_call("set_grid_snap", checked)
        )
        view_menu.addAction(self._snap_action)
        view_menu.aboutToShow.connect(self._sync_view_menu_state)

        help_menu = self.menuBar().addMenu("Help")
        add(help_menu, "Keyboard Shortcuts…", self._show_shortcuts_reference)
        add(help_menu, "Notification History…", self._show_notification_history)

    def _show_notification_history(self) -> None:
        from src.ui.util import notification_history

        history = notification_history()
        dialog = QDialog(self._app)
        dialog.setWindowTitle("Notification History")
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setPlainText(
            "\n".join(f"{timestamp}  {message}" for timestamp, message in history)
            if history
            else "No notifications yet."
        )
        layout.addWidget(log)
        dialog.exec()

    def _refresh_shortcut_tooltips(self) -> None:
        """(Re)build tooltips that embed a shortcut hint, e.g. "Save (Ctrl+S)".

        Rebinding a shortcut in Settings already updates the real QAction
        shortcuts (see ``_open_settings``) but these header-button tooltips
        were only ever set once at construction — without this, hovering
        kept showing the OLD/default shortcut text forever after a rebind.
        """
        for btn, base_text, shortcut_id in self._shortcut_tooltip_specs:
            btn.setToolTip(f"{base_text} ({self._shortcut(shortcut_id)})")

    def _show_shortcuts_reference(self) -> None:
        rows = [
            ("Workspace", ""),
            ("New / Open / Save / Save As", "per File menu"),
            ("Command palette", self._shortcut("app.command_palette")),
            ("Settings", self._shortcut("app.settings")),
            ("Switch tabs", "Alt+1 … Alt+4"),
            ("Repository sync", self._shortcut("tab.repo")),
            ("", ""),
        ]
        # Canvas commands come straight from the registry so this dialog
        # can never drift from the actual keymap.
        rows += canvas_commands.shortcut_reference_rows()
        rows += [
            ("Canvas interaction", ""),
            ("Pan", "Space-drag or middle mouse"),
            ("Zoom", "Mouse wheel, ⌘+ / ⌘-"),
            ("Nudge selection", "Arrows (⇧ = 1 mm)"),
            ("Delete selected", "Backspace / Del"),
            ("Quick shape (select mode)", "Q radial menu · ⇧R/⇧C/⇧S/⇧P"),
        ]
        lines = []
        for label, keys in rows:
            if not label:
                lines.append("")
            elif not keys:
                lines.append(f"<b>{label}</b>")
            else:
                lines.append(
                    f"{label}&nbsp;&nbsp;&mdash;&nbsp;&nbsp;"
                    f"<span style='color:#8b949e'>{keys}</span>"
                )
        QMessageBox.information(
            self._app,
            "Keyboard Shortcuts",
            "<br>".join(lines),
        )

    def _sync_view_menu_state(self) -> None:
        canvas = self._active_canvas()
        has_canvas = canvas is not None
        self._grid_action.setEnabled(has_canvas)
        self._snap_action.setEnabled(has_canvas)
        if has_canvas:
            self._grid_action.setChecked(bool(getattr(canvas, "_grid_visible", False)))
            self._snap_action.setChecked(bool(getattr(canvas, "_grid_snap", False)))

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

    def _build_shell_header(self) -> QWidget:
        self._shortcut_tooltip_specs: list[tuple[QPushButton, str, str]] = []
        shell = surface_frame("panel")
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
        self._workspace_state_chip = info_chip("Saved", "success")
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
                self._shortcut_tooltip_specs.append((btn, text, shortcut_hint))
            layout.addWidget(btn)

        action_sep = QLabel("│")
        action_sep.setProperty("role", "toolbar-sep")
        action_sep.setToolTip("Application actions")
        layout.addWidget(action_sep)

        # Application actions — visually separated from workspace file actions.
        update_btn = QPushButton()
        update_btn.setIcon(download_icon())
        update_btn.setIconSize(QSize(18, 18))
        update_btn.setFixedSize(30, 30)
        update_btn.setToolTip("Check for updates")
        update_btn.clicked.connect(self._open_update_check)
        layout.addWidget(update_btn)

        palette_btn = QPushButton("⌘K")
        palette_btn.setFixedSize(44, 30)
        self._shortcut_tooltip_specs.append((palette_btn, "Command palette", "app.command_palette"))
        palette_btn.clicked.connect(self._open_command_palette)
        layout.addWidget(palette_btn)

        settings_btn = QPushButton()
        settings_btn.setIcon(gear_icon())
        settings_btn.setIconSize(QSize(18, 18))
        settings_btn.setFixedSize(30, 30)
        self._shortcut_tooltip_specs.append((settings_btn, "Settings", "app.settings"))
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)

        help_btn = QPushButton("?")
        help_btn.setFixedSize(30, 30)
        help_btn.setToolTip("User Manual")
        help_btn.clicked.connect(self._show_help)
        layout.addWidget(help_btn)

        self._refresh_shortcut_tooltips()
        return shell


class CommandController(_AppProxy):
    """Own commands, shortcuts, palette, and active-canvas dispatch."""

    def _invoke_canvas_fit(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "fit"):
            canvas.fit()

    def _register_action(
        self, action_id: str, text: str, callback, *, shortcut: str | None = None
    ) -> None:
        action = QAction(text, self._app)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        else:
            sc = self._shortcut(action_id)
            if sc:
                action.setShortcut(QKeySequence(sc))
        action.triggered.connect(callback)
        self.addAction(action)
        self._global_actions[action_id] = action

    def _open_command_palette(self) -> None:
        dlg = CommandPaletteDialog(self._build_command_palette_commands(), self._app)
        dlg.exec()

    def _menu_undo(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.undo()
        else:
            self._run_canvas_command("edit.undo")

    def _canvas_call(self, name: str, *args) -> None:
        canvas = self._active_canvas()
        fn = getattr(canvas, name, None)
        if callable(fn):
            fn(*args)

    def _setup_global_shortcuts(self) -> None:
        # workspace.* commands are already QActions via _build_workspace_actions
        _MENU_HANDLED = {
            "workspace.new",
            "workspace.new_window",
            "workspace.open",
            "workspace.save",
            "workspace.save_as",
            "tab.repo",  # Repository Sync menu action owns this shortcut
        }
        for cmd in self._build_commands():
            if cmd.action_id not in _MENU_HANDLED:
                self._register_action(cmd.action_id, cmd.title, cmd.run)

    def _run_canvas_command(self, cmd_id: str) -> None:
        canvas = self._active_canvas()
        if canvas is not None:
            canvas_commands.run(canvas, cmd_id)

    def _shortcut(self, action_id: str) -> str:
        keybindings = self._settings.get("keybindings", {})
        if not isinstance(keybindings, dict):
            keybindings = {}
        value = keybindings.get(action_id)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return DEFAULT_KEYBINDINGS.get(action_id, "")

    def _menu_select_all(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.selectAll()
        else:
            self._run_canvas_command("select.all")

    def _build_commands(self) -> list[CommandSpec]:
        """Single source of truth for all user-accessible commands.

        Each entry has: action_id, title, keywords, run.
        Used by both _setup_global_shortcuts and _build_command_palette_commands.
        """
        cmds: list[CommandSpec] = [
            CommandSpec(
                "app.settings",
                "App: Settings",
                "settings preferences",
                self._open_settings,
            ),
            CommandSpec(
                "app.command_palette",
                "App: Command Palette",
                "command palette search",
                self._open_command_palette,
            ),
            CommandSpec(
                "workspace.new",
                "Workspace: New",
                "new workspace file",
                self._new_workspace,
            ),
            CommandSpec(
                "workspace.new_window",
                "Workspace: New Window",
                "new window multi document",
                self._new_window,
            ),
            CommandSpec(
                "workspace.open",
                "Workspace: Open",
                "open workspace file",
                self._open_workspace,
            ),
            CommandSpec(
                "workspace.save",
                "Workspace: Save",
                "save workspace",
                self._save_workspace,
            ),
            CommandSpec(
                "workspace.save_as",
                "Workspace: Save As",
                "save as workspace",
                self._save_workspace_as,
            ),
            CommandSpec(
                "canvas.select_mode",
                "Canvas: Mode Select",
                "canvas select mode",
                lambda: self._invoke_canvas_mode("select"),
            ),
            CommandSpec(
                "canvas.draw_mode",
                "Canvas: Mode Draw",
                "canvas draw mode",
                lambda: self._invoke_canvas_mode("draw"),
            ),
            CommandSpec(
                "canvas.edit_mode",
                "Canvas: Mode Edit",
                "canvas edit mode",
                lambda: self._invoke_canvas_mode("edit"),
            ),
            CommandSpec(
                "canvas.measure",
                "Canvas: Toggle Measure",
                "canvas measure",
                self._invoke_canvas_measure,
            ),
            CommandSpec(
                "canvas.dimension",
                "Canvas: Toggle Dimension Tool",
                "canvas dimension annotation drafting",
                self._invoke_canvas_dimension,
            ),
            CommandSpec(
                "canvas.fit",
                "Canvas: Fit View",
                "canvas fit zoom",
                self._invoke_canvas_fit,
            ),
            CommandSpec(
                "window.fullscreen",
                "Window: Toggle Fullscreen",
                "fullscreen window maximize",
                self._toggle_fullscreen,
            ),
        ]
        for spec in self._page_specs:
            cmds.append(
                CommandSpec(
                    spec.shortcut_id,
                    spec.command_title,
                    spec.command_keywords,
                    lambda page_id=spec.page_id: self._switch_to_page(page_id),  # type: ignore[misc]
                )
            )
        cmds.append(
            CommandSpec(
                "tab.repo",
                "App: Repository Sync",
                "repo git sync pull push commit",
                self._open_repo_dialog,
            )
        )
        return cmds

    def _menu_redo(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.redo()
        else:
            self._run_canvas_command("edit.redo")

    def _focused_text_widget(self):
        widget = QApplication.focusWidget()
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return widget
        return None

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _invoke_canvas_mode(self, mode: str) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "set_mode"):
            current = canvas.get_mode() if hasattr(canvas, "get_mode") else None
            canvas.set_mode(mode if current != mode else "select")

    def _invoke_canvas_dimension(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "toggle_dimension_mode"):
            canvas.toggle_dimension_mode()

    def _menu_paste(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.paste()
        else:
            self._run_canvas_command("clipboard.paste")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._app, self._settings)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            # Propagate changed settings to tabs that cache paths at init time.
            self._settings = dlg._settings
            self._settings.setdefault("keybindings", dict(DEFAULT_KEYBINDINGS))
            canvas_commands.apply_keybindings(self._settings.get("keybindings"))
            self._settings.setdefault("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
            self._settings_controller.replace(self._settings)
            self._app._apply_accessibility_settings()
            self._repo_page.sync_from_settings()

            for action_id, action in self._global_actions.items():
                shortcut = self._shortcut(action_id)
                action.setShortcut(QKeySequence(shortcut) if shortcut else QKeySequence())

            self._new_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.new")))
            self._new_window_action.setShortcut(
                QKeySequence(self._shortcut("workspace.new_window"))
            )
            self._open_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.open")))
            self._save_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.save")))
            self._save_workspace_as_action.setShortcut(
                QKeySequence(self._shortcut("workspace.save_as"))
            )
            self._refresh_shortcut_tooltips()

            self._update_checker._configure_auto_fetch_timer()

    def _build_command_palette_commands(self) -> list[dict[str, object]]:
        """Return command-palette entries derived from _build_commands()."""
        return [
            {
                "id": spec.action_id,
                "title": spec.title,
                "shortcut": self._shortcut(spec.action_id),
                "keywords": spec.keywords,
                "run": spec.run,
            }
            for spec in self._build_commands()
        ]

    def _open_update_check(self) -> None:
        """Open the update check dialog."""
        dlg = UpdateDialog(self._app)
        dlg.exec()

    def _show_help(self) -> None:
        """Show the user manual help dialog."""
        from src.ui.pages.help import HelpDialog

        HelpDialog.show_help(self._app, self._app)

    def _invoke_canvas_measure(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "toggle_measure"):
            canvas.toggle_measure()

    def _menu_cut(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.cut()
        else:
            self._run_canvas_command("clipboard.cut")

    def _active_canvas(self) -> Any | None:
        current = self._tabs.currentWidget()
        return getattr(current, "_canvas", None)

    def _menu_copy(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.copy()
        else:
            self._run_canvas_command("clipboard.copy")


class WorkspaceController(_WorkspaceStateController):
    """Own workspace actions, state, persistence, and recent files."""

    _LOCAL = {
        "_app",
        "_page_runtime",
        "_tabs",
        "path",
        "dirty",
        "last_saved_document",
        "has_unsaved_changes",
    }
    _workspace_path: Path | None
    _last_saved_document: dict | None

    def __init__(self, app: App, page_runtime, tabs) -> None:
        object.__setattr__(self, "_app", app)
        super().__init__(page_runtime, tabs)

    def __getattr__(self, name: str):
        return getattr(self._app, name)

    def __setattr__(self, name: str, value) -> None:
        if name in self._LOCAL:
            object.__setattr__(self, name, value)
        else:
            setattr(self._app, name, value)

    def _build_workspace_actions(self) -> None:
        self._new_workspace_action = QAction("New Workspace", self._app)
        self._new_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.new")))
        self._new_workspace_action.triggered.connect(self._new_workspace)
        self._workspace_menu.addAction(self._new_workspace_action)

        self._new_window_action = QAction("New Window", self._app)
        self._new_window_action.setShortcut(QKeySequence(self._shortcut("workspace.new_window")))
        self._new_window_action.triggered.connect(self._new_window)
        self._workspace_menu.addAction(self._new_window_action)

        self._open_workspace_action = QAction("Open Workspace…", self._app)
        self._open_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.open")))
        self._open_workspace_action.triggered.connect(self._open_workspace)
        self._workspace_menu.addAction(self._open_workspace_action)

        self._save_workspace_action = QAction("Save Workspace", self._app)
        self._save_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.save")))
        self._save_workspace_action.triggered.connect(self._save_workspace)
        self._workspace_menu.addAction(self._save_workspace_action)

        self._save_workspace_as_action = QAction("Save Workspace As…", self._app)
        self._save_workspace_as_action.setShortcut(
            QKeySequence(self._shortcut("workspace.save_as"))
        )
        self._save_workspace_as_action.triggered.connect(self._save_workspace_as)
        self._workspace_menu.addAction(self._save_workspace_as_action)

        self._workspace_menu.addSeparator()

        # Repository sync moved out of the top-level tabs into this menu —
        # the action id stays "tab.repo" so existing keybindings keep working.
        self._repo_dialog_action = QAction("Repository Sync…", self._app)
        self._repo_dialog_action.setShortcut(QKeySequence(self._shortcut("tab.repo")))
        self._repo_dialog_action.triggered.connect(self._open_repo_dialog)
        self._workspace_menu.addAction(self._repo_dialog_action)

        self._workspace_menu.addSeparator()

    def _collect_workspace_document(self) -> dict:
        self._workspace_controller.path = self._workspace_path
        return self._workspace_controller.collect()

    def _schedule_workspace_dirty_check(self) -> None:
        self._has_unsaved_changes = True
        self._workspace_timer.start(150)

    def _save_workspace_as(self) -> bool:
        default_name = (
            self._workspace_path.name
            if self._workspace_path
            else f"workspace{WORKSPACE_FILE_SUFFIX}"
        )
        path, _ = QFileDialog.getSaveFileName(
            self._app,
            "Save Workspace As",
            str(Path(self._workspace_default_dir()) / default_name),
            f"Workspace files (*{WORKSPACE_FILE_SUFFIX});;JSON files (*.json)",
        )
        if not path:
            return False
        self._workspace_path = normalize_workspace_path(path)
        return self._save_workspace()

    def _update_title(self) -> None:
        self.setWindowTitle(workspace_title(self._workspace_path, self._workspace_dirty))
        self._refresh_workspace_header()

    def _load_recent_workspace_action(self) -> None:
        action = self.sender()
        if not isinstance(action, QAction):
            return
        path_str = action.data()
        if not path_str:
            return
        self._load_workspace_file(Path(str(path_str)))

    def _confirm_discard_if_dirty(self) -> bool:
        self._update_workspace_dirty()
        # Don't prompt for save if there's no content to save
        if not self._workspace_dirty or not self._has_workspace_content():
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
        if not self._has_unsaved_changes:
            self._workspace_dirty = False
        elif self._last_saved_document is not None:
            self._workspace_dirty = self._collect_workspace_document() != self._last_saved_document
        else:
            self._workspace_dirty = False
        self._update_title()

    def _workspace_pages(self):
        return self._page_runtime.iter_workspace_pages()

    def _preset_pages(self):
        return self._page_runtime.iter_preset_pages()

    def _rebuild_recent_workspaces_menu(self) -> None:
        self._recent_workspaces_menu.clear()
        recent = recent_workspace_paths(self._settings)
        if not recent:
            action = QAction("No recent workspaces", self._app)
            action.setEnabled(False)
            self._recent_workspaces_menu.addAction(action)
            return
        for path in recent:
            action = QAction(path.name, self._recent_workspaces_menu)
            action.setData(str(path))
            action.setToolTip(str(path))
            action.triggered.connect(
                lambda _checked=False, recent_path=path: self._load_workspace_file(recent_path)
            )
            self._recent_workspaces_menu.addAction(action)

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
            self._has_unsaved_changes = False
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

    def _clear_workspace_state(self) -> None:
        self._workspace_controller.clear()

    def _apply_workspace_document(self, document: dict) -> None:
        self._workspace_controller.apply(document)

    def _workspace_default_dir(self) -> str:
        return workspace_default_dir(self._settings)

    def _remember_workspace_path(self, path: Path) -> None:
        remember_workspace_path(settings=self._settings, path=path, max_recent=8)
        save_settings(self._settings)
        self._rebuild_recent_workspaces_menu()

    def _save_workspace(self) -> bool:
        if self._workspace_path is None:
            return self._save_workspace_as()
        try:
            document = self._collect_workspace_document()
            write_json_file_atomic(self._workspace_path, document)
            self._last_saved_document = document
            self._workspace_dirty = False
            self._has_unsaved_changes = False
            self._remember_workspace_path(self._workspace_path)
            self._update_title()
            self._autosave_controller._discard_autosave()
            return True
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self._app, "Workspace Error", str(exc))
            return False

    def _new_workspace(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self._workspace_path = None
        self._clear_workspace_state()
        self._last_saved_document = self._collect_workspace_document()
        self._workspace_dirty = False
        self._has_unsaved_changes = False
        self._update_title()
