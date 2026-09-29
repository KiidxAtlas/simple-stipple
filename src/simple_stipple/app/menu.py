from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.canvas.dialogs.customize_dialogs import (
    ContextMenuActionCustomizeDialog,
    DrawSidebarCustomizeDialog,
    RadialMenuDialog,
)
from simple_stipple.canvas.dialogs.keybindings_dialog import KeybindingsDialog
from simple_stipple.features.trace.form import TRACE_DEFAULT_FIELDS, trace_default
from simple_stipple.platform.settings import (
    DEFAULT_KEYBINDINGS,
    DEFAULT_RADIAL_MENU_TOOLS,
    save_settings,
)
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.icons import (
    download_icon,
    gear_icon,
)
from simple_stipple.ui.components.layout import (
    info_chip,
    surface_frame,
)
from simple_stipple.ui.dialogs.command_palette import CommandPaletteDialog
from simple_stipple.ui.dialogs.settings_dialog import SettingsDialog
from simple_stipple.ui.dialogs.update_dialog import UpdateDialog
from simple_stipple.ui.dialogs.welcome import MANUAL_CHOICE, WelcomeDialog

if TYPE_CHECKING:
    from simple_stipple.app.window import App


LOGGER = logging.getLogger(__name__)


def _native_keys(shortcut: str) -> str:
    """Platform-native display text for a Qt shortcut string (⌘K on macOS)."""
    if not shortcut:
        return ""
    return QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)


@dataclass(frozen=True)
class CommandSpec:
    action_id: str
    title: str
    keywords: str
    run: Callable[[], Any]


class MenuController:
    """Own shell header and menu presentation."""

    def __init__(self, app: App) -> None:
        self._app = app

    def _build_edit_view_help_menus(self) -> None:
        """Standard menus routing to the active page's canvas.

        Text-editing shortcuts (undo/cut/copy/paste/select-all) check the
        focused widget first so they never hijack typing in a line edit.
        """
        edit_menu = self._app.menuBar().addMenu("Edit")

        def add(menu, text, slot, shortcut=None):
            action = QAction(text, self._app)
            if shortcut is not None:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            return action

        add(edit_menu, "Undo", self._app._menu_undo, QKeySequence.StandardKey.Undo)
        add(edit_menu, "Redo", self._app._menu_redo, QKeySequence.StandardKey.Redo)
        edit_menu.addSeparator()
        add(edit_menu, "Cut", self._app._menu_cut, QKeySequence.StandardKey.Cut)
        add(edit_menu, "Copy", self._app._menu_copy, QKeySequence.StandardKey.Copy)
        add(edit_menu, "Paste", self._app._menu_paste, QKeySequence.StandardKey.Paste)

        def add_cmd(menu, cmd_id, *, label=None):
            cmd = canvas_commands.get(cmd_id)
            return add(
                menu,
                label or cmd.label,
                lambda: self._app._run_canvas_command(cmd_id),
                QKeySequence(cmd.shortcut) if cmd.shortcut else None,
            )

        add_cmd(edit_menu, "edit.duplicate")
        # Delete intentionally has no global shortcut: Backspace must keep
        # typing in text fields; the canvas handles it when focused, and the
        # label says so.
        add(
            edit_menu,
            "Delete Selected (Del on canvas)",
            lambda: self._app._canvas_call("delete_selected"),
        )
        edit_menu.addSeparator()
        add(
            edit_menu,
            "Select All",
            self._app._menu_select_all,
            QKeySequence.StandardKey.SelectAll,
        )
        add_cmd(edit_menu, "select.none")

        view_menu = self._app.menuBar().addMenu("View")
        # Fit View reuses the registered canvas.fit action so the menu shows
        # the real, rebindable shortcut. Qt offers ShortcutOverride to the
        # focused widget first, so a single-letter key still types into text
        # fields.
        fit_action = self._app._global_actions["canvas.fit"]
        fit_action.setText("Fit View")
        view_menu.addAction(fit_action)
        add(
            view_menu,
            "Zoom In",
            lambda: self._app._run_canvas_command("view.zoom_in"),
            QKeySequence.StandardKey.ZoomIn,
        )
        add(
            view_menu,
            "Zoom Out",
            lambda: self._app._run_canvas_command("view.zoom_out"),
            QKeySequence.StandardKey.ZoomOut,
        )
        view_menu.addSeparator()
        self._app._grid_action = QAction("Show Grid", self._app, checkable=True)
        self._app._grid_action.triggered.connect(
            lambda checked: self._app._canvas_call("set_grid_visible", checked)
        )
        view_menu.addAction(self._app._grid_action)
        self._app._snap_action = QAction("Snap to Grid", self._app, checkable=True)
        self._app._snap_action.triggered.connect(
            lambda checked: self._app._canvas_call("set_grid_snap", checked)
        )
        view_menu.addAction(self._app._snap_action)
        view_menu.aboutToShow.connect(self._sync_view_menu_state)

        help_menu = self._app.menuBar().addMenu("Help")
        add(help_menu, "Welcome…", self._show_welcome)
        add(help_menu, "User Manual…", self._app._show_help)
        add(help_menu, "Keyboard Shortcuts…", self._show_shortcuts_reference)
        add(help_menu, "Notification History…", self._show_notification_history)
        help_menu.addSeparator()
        add(help_menu, "Check for Updates…", self._app._open_update_check)
        add(help_menu, "Support Me", lambda: self._show_support_dialog())
        help_menu.addSeparator()
        add(help_menu, "About Simple Stipple", self._show_about)

    def _show_about(self) -> None:
        from simple_stipple.platform.updates import get_current_version, get_releases_page_url

        dialog = QMessageBox(self._app)
        dialog.setWindowTitle("About Simple Stipple")
        dialog.setText(
            f"<h3>Simple Stipple</h3>"
            f"<p>Draft, trace, pattern, and export vector geometry for laser workflows.</p>"
            f"<p><b>Version {get_current_version()}</b></p>"
        )
        dialog.setInformativeText(
            "Simple Stipple prepares files; it does not control laser hardware. "
            "Verify every export in your machine software before running a job."
        )
        releases_button = dialog.addButton("Release notes", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() is releases_button:
            QDesktopServices.openUrl(QUrl(get_releases_page_url()))

    def _show_notification_history(self) -> None:
        from simple_stipple.ui.components.feedback import notification_history

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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_support_dialog(self) -> None:
        """Open the Support Me dialog with donation link."""
        from simple_stipple.ui.dialogs.support import SupportMeDialog

        dialog = SupportMeDialog(self._app)
        dialog.exec()

    def _offer_welcome(self) -> None:
        """Show the first-run welcome unless the user has already dismissed it."""
        if not self._app._settings.get("welcome_seen", False):
            self._show_welcome()

    def _show_welcome(self) -> None:
        """Open the task chooser without blocking (startup and Help → Welcome…)."""
        dialog = WelcomeDialog(self._app)
        dialog.finished.connect(lambda _result: self._on_welcome_finished(dialog))
        dialog.open()

    def _on_welcome_finished(self, dialog: WelcomeDialog) -> None:
        # Any dismissal counts as seen; Help → Welcome… reopens it.
        if not self._app._settings.get("welcome_seen", False):
            self._app._settings["welcome_seen"] = True
            save_settings(self._app._settings)
        choice = dialog.choice
        dialog.deleteLater()
        if choice == MANUAL_CHOICE:
            self._app._show_help()
        elif choice is not None:
            self._app._switch_to_page(choice)

    def _refresh_shortcut_tooltips(self) -> None:
        """(Re)build tooltips that embed a shortcut hint, e.g. "Save (Ctrl+S)".

        Rebinding a shortcut in Settings already updates the real QAction
        kept showing the OLD/default shortcut text forever after a rebind.
        """
        for btn, base_text, shortcut_id in self._app._shortcut_tooltip_specs:
            keys = _native_keys(self._app._shortcut(shortcut_id))
            btn.setToolTip(f"{base_text} ({keys})" if keys else base_text)

    def _show_shortcuts_reference(self) -> None:
        # Every row is generated from the live bindings (page registry, File
        # actions, canvas command registry), so rebinding or adding a page can
        # never leave this dialog stale.
        rows = self._app._shell_shortcut_rows()
        rows += [("", ""), ("Canvas shortcuts — work while the canvas has focus", "")]
        rows += canvas_commands.shortcut_reference_rows()
        rows += [
            ("Canvas interaction", ""),
            ("Pan", "P, Space-drag, or middle mouse"),
            ("Zoom", "Mouse wheel, ⌘+ / ⌘-"),
            ("Nudge selection", "Arrows (⇧ = 1 mm)"),
            ("Delete selected", "Backspace / Del (canvas)"),
            ("Quick shape (select mode)", "Q radial menu · ⇧R/⇧C/⇧S/⇧P"),
        ]
        dialog = QDialog(self._app)
        dialog.setWindowTitle("Keyboard Shortcuts")
        dialog.resize(480, 620)
        layout = QVBoxLayout(dialog)
        # ~30 rows didn't fit a fixed-size QMessageBox on smaller screens
        # and it wasn't resizable — a scrollable, resizable dialog instead.
        # Muted key-hint color read from the applied palette rather than a
        # hardcoded dark-only hex, so it stays legible in Light mode.
        is_light = dialog.palette().color(QPalette.ColorRole.Window).lightness() > 128
        muted = "#57606a" if is_light else "#8b949e"
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        lines = []
        for label, keys in rows:
            if not label:
                lines.append("")
            elif not keys:
                lines.append(f"<b>{label}</b>")
            else:
                lines.append(
                    f"{label}&nbsp;&nbsp;&mdash;&nbsp;&nbsp;"
                    f"<span style='color:{muted}'>{keys}</span>"
                )
        browser.setHtml("<br>".join(lines))
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _sync_view_menu_state(self) -> None:
        canvas = self._app._active_canvas()
        has_canvas = canvas is not None
        self._app._grid_action.setEnabled(has_canvas)
        self._app._snap_action.setEnabled(has_canvas)
        if has_canvas:
            self._app._grid_action.setChecked(bool(getattr(canvas, "_grid_visible", False)))
            self._app._snap_action.setChecked(bool(getattr(canvas, "_grid_snap", False)))

    def _refresh_workspace_header(self) -> None:
        path = self._app._workspace_path
        title = path.stem if path else "Untitled"
        self._app._workspace_title_label.setText(title)
        # A save to the named workspace file and a crash-recovery snapshot are
        # different guarantees; report each with its own time.
        saved_at = self._app._last_workspace_save_at
        if path is None:
            save_line = "Not saved to a workspace file yet"
        elif saved_at is None:
            save_line = f"Workspace file: {path.name} (not saved this session)"
        else:
            save_line = f"Saved to {path.name} {saved_at:%H:%M}"
        snapshot_at = self._app._last_recovery_snapshot_at
        recovery_line = (
            f"Recovery snapshot {snapshot_at:%H:%M} (not a workspace save)"
            if snapshot_at is not None
            else "No recovery snapshot yet (taken every 90 s while changes are unsaved)"
        )
        workspace_detail = (
            "Open saved workspaces, recent files, and recovery snapshots\n"
            f"{save_line}\n{recovery_line}"
        )
        self._app._workspace_title_label.setToolTip(workspace_detail)
        self._app._workspace_title_label.setAccessibleDescription(workspace_detail)
        if getattr(self._app, "_restored_recovery_path", None) is not None:
            chip_text, chip_tone = "Recovered workspace · save it", "warn"
        elif self._app._workspace_dirty:
            chip_text, chip_tone = "Unsaved changes", "warn"
        elif self._app._workspace_path is None:
            chip_text, chip_tone = "Not saved", "neutral"
        else:
            chip_text, chip_tone = "Saved", "success"
        self._app._workspace_state_chip.setText(chip_text)
        self._app._workspace_state_chip.setToolTip(f"{chip_text}. {workspace_detail}")
        self._app._workspace_state_chip.setAccessibleDescription(
            f"Workspace state: {chip_text}. {workspace_detail}"
        )
        self._app._workspace_state_chip.setProperty("tone", chip_tone)
        refresh_style(self._app._workspace_state_chip)
        # Disable save actions if there's no workspace content
        has_content = self._app._has_workspace_content()
        self._app._save_workspace_action.setEnabled(has_content and self._app._workspace_dirty)

    def _build_shell_header(self) -> QWidget:
        from simple_stipple.platform.updates import get_current_version

        self._app._shortcut_tooltip_specs = []
        shell = surface_frame("panel")
        shell.setProperty("role", "hero")
        layout = QHBoxLayout(shell)
        # This is application chrome, not a landing-page hero. A compact
        # header preserves space for the drawing and keeps the workspace
        # controls visually secondary to the active tool.
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # App identity — compact single line
        title = QLabel("Simple Stipple")
        title.setProperty("role", "shell-title")
        layout.addWidget(title)

        # Show the version that is actually installed, rather than only
        # exposing it through About / the updater.  This is especially useful
        # when support reports compare Windows package versions.
        installed_version = get_current_version()
        version = QLabel(f"v{installed_version}")
        version.setProperty("role", "shell-meta")
        version.setToolTip(f"Installed version {installed_version}")
        version.setAccessibleName("Installed application version")
        version.setAccessibleDescription(f"Simple Stipple version {installed_version}")
        layout.addWidget(version)

        # Separator
        sep = QLabel("·")
        sep.setProperty("role", "shell-sep")
        layout.addWidget(sep)

        # Workspace name
        self._app._workspace_title_label = QPushButton()
        self._app._workspace_title_label.setMinimumHeight(24)
        self._app._workspace_title_label.setProperty("role", "shell-meta")
        self._app._workspace_title_label.setToolTip(
            "Open saved workspaces, recent files, and recovery snapshots"
        )
        self._app._workspace_title_label.setAccessibleName("Current workspace")
        self._app._workspace_title_label.clicked.connect(self._app._open_saved_workspaces)
        layout.addWidget(self._app._workspace_title_label)

        # Status chip
        self._app._workspace_state_chip = info_chip("Saved", "success")
        self._app._workspace_state_chip.setAccessibleName("Workspace save status")
        layout.addWidget(self._app._workspace_state_chip)

        layout.addStretch()

        # Hick's Law: the header used to offer New, Open, Workspaces, Save,
        # Updates, Commands, Settings and Help as eight peers, so choosing
        # took a scan every time. The three that are not Save collapse into
        # one menu each — the same actions, one decision.
        workspace_btn = QToolButton()
        workspace_btn.setText("Workspace")
        workspace_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        workspace_btn.setAccessibleName("Workspace actions")
        workspace_btn.setToolTip("New, open, save, and recover workspaces")
        workspace_menu = QMenu(workspace_btn)
        # Same QActions, labels, and grouping as the File menu, so each
        # shortcut belongs to exactly one QAction (duplicates make Qt report
        # ambiguous shortcuts) and the two menus cannot drift apart.
        self._app._populate_workspace_menu(workspace_menu)
        workspace_btn.setMenu(workspace_menu)
        layout.addWidget(workspace_btn)

        # Von Restorff: the single filled button in the whole shell. Its job
        # is to be the thing you see without looking for it.
        save_btn = QPushButton("Save")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._app._save_workspace)
        self._app._shortcut_tooltip_specs.append((save_btn, "Save Workspace", "workspace.save"))
        layout.addWidget(save_btn)

        action_sep = QLabel("│")
        action_sep.setProperty("role", "toolbar-sep")
        action_sep.setToolTip("Application actions")
        layout.addWidget(action_sep)

        # The command palette reaches everything else, so it keeps its own
        # affordance rather than hiding inside the overflow it duplicates.
        palette_btn = QPushButton(
            _native_keys(self._app._shortcut("app.command_palette")) or "Commands"
        )
        # Let the shared control contract determine the height. A fixed 30px
        # exception made the ⌘K affordance visibly misalign with Workspace,
        # Save, and Settings in the same header.
        palette_btn.setMinimumWidth(44)
        self._app._shortcut_tooltip_specs.append(
            (palette_btn, "Command palette", "app.command_palette")
        )
        palette_btn.clicked.connect(self._app._open_command_palette)
        layout.addWidget(palette_btn)

        app_btn = QToolButton()
        app_btn.setIcon(gear_icon())
        app_btn.setIconSize(QSize(18, 18))
        app_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        app_btn.setProperty("role", "overflow")
        app_btn.setAccessibleName("Application menu")
        app_menu = QMenu(app_btn)
        # The registered app.settings action, so its shortcut is the live,
        # rebindable one and never an ambiguous duplicate.
        settings_action = self._app._global_actions["app.settings"]
        settings_action.setText("Settings…")
        app_menu.addAction(settings_action)
        app_menu.addAction("User Manual…", self._app._show_help)
        app_menu.addAction("Support Me", self._show_support_dialog)
        app_menu.addSeparator()
        update_action = app_menu.addAction("Check for Updates…", self._app._open_update_check)
        update_action.setIcon(download_icon())
        app_btn.setMenu(app_menu)
        self._app._shortcut_tooltip_specs.append((app_btn, "Settings and help", "app.settings"))
        layout.addWidget(app_btn)

        self._refresh_shortcut_tooltips()
        return shell


class CommandController:
    """Own commands, shortcuts, palette, and active-canvas dispatch."""

    def __init__(self, app: App) -> None:
        self._app = app

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
        self._app.addAction(action)
        self._app._global_actions[action_id] = action

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
        if canvas is not None:
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
            "tab.repo",  # Repository menu action owns this shortcut
        }
        for cmd in self._build_commands():
            if cmd.action_id not in _MENU_HANDLED:
                self._register_action(cmd.action_id, cmd.title, cmd.run)

    def _run_canvas_command(self, cmd_id: str) -> None:
        canvas = self._active_canvas()
        if canvas is not None:
            canvas_commands.run(canvas, cmd_id)

    def _shell_shortcut_rows(self) -> list[tuple[str, str]]:
        """(label, keys) rows for workspace, application, and page shortcuts.

        Labels are the menu/page names users see; keys are the effective
        bindings, so the Keyboard Shortcuts dialog and the manual both follow
        rebinding and the registered page list.
        """

        def keys(action_id: str) -> str:
            return _native_keys(self._shortcut(action_id)) or "Not assigned"

        app = self._app
        rows: list[tuple[str, str]] = [("Workspace", "")]
        rows += [
            (action.text(), keys(action_id))
            for action, action_id in (
                (app._new_workspace_action, "workspace.new"),
                (app._new_window_action, "workspace.new_window"),
                (app._open_workspace_action, "workspace.open"),
                (app._save_workspace_action, "workspace.save"),
                (app._save_workspace_as_action, "workspace.save_as"),
            )
        ]
        rows += [
            ("Application", ""),
            ("Command Palette", keys("app.command_palette")),
            ("Settings…", keys("app.settings")),
            ("Toggle Fullscreen", keys("window.fullscreen")),
            ("Pages", ""),
        ]
        rows += [(spec.title, keys(spec.shortcut_id)) for spec in app._page_specs]
        return rows

    def _shortcut(self, action_id: str) -> str:
        keybindings = self._app._settings.get("keybindings", {})
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
                self._app._new_workspace,
            ),
            CommandSpec(
                "workspace.new_window",
                "Workspace: New Window",
                "new window multi document",
                self._app._new_window,
            ),
            CommandSpec(
                "workspace.open",
                "Workspace: Open",
                "open workspace file",
                self._app._open_workspace,
            ),
            CommandSpec(
                "workspace.save",
                "Workspace: Save",
                "save workspace",
                self._app._save_workspace,
            ),
            CommandSpec(
                "workspace.save_as",
                "Workspace: Save As",
                "save as workspace",
                self._app._save_workspace_as,
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
                "Canvas: Toggle Scale",
                "canvas measure scale",
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
        for spec in self._app._page_specs:
            cmds.append(
                CommandSpec(
                    spec.shortcut_id,
                    spec.command_title,
                    spec.command_keywords,
                    lambda page_id=spec.page_id: self._app._switch_to_page(page_id),  # type: ignore[misc]
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
        if self._app.isFullScreen():
            self._app.showNormal()
        else:
            self._app.showFullScreen()

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
        dlg = SettingsDialog(
            self._app,
            self._app._settings,
            trace_default_fields=TRACE_DEFAULT_FIELDS,
            trace_default_fn=trace_default,
            keybindings_dialog_cls=KeybindingsDialog,
            radial_menu_dialog_cls=RadialMenuDialog,
            draw_sidebar_customize_dialog_cls=DrawSidebarCustomizeDialog,
            context_menu_customize_dialog_cls=ContextMenuActionCustomizeDialog,
        )
        # Apply used to only save to disk — the running app kept its stale
        # in-memory settings until the dialog was later accepted (or the app
        # restarted). Route Apply through the same propagation as Save/OK.
        dlg.applied.connect(lambda: self._apply_settings_dialog_result(dlg))
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            self._apply_settings_dialog_result(dlg)

    def _apply_settings_dialog_result(self, dlg: SettingsDialog) -> None:
        # Propagate changed settings to tabs that cache paths at init time.
        self._app._settings = dlg._settings
        self._app._settings.setdefault("keybindings", dict(DEFAULT_KEYBINDINGS))
        canvas_commands.apply_keybindings(self._app._settings.get("keybindings"))
        self._app._settings.setdefault("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
        self._app._settings_controller.replace(self._app._settings)
        self._app._apply_accessibility_settings()
        self._app._repo_page.sync_from_settings()

        for action_id, action in self._app._global_actions.items():
            shortcut = self._shortcut(action_id)
            action.setShortcut(QKeySequence(shortcut) if shortcut else QKeySequence())

        self._app._new_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.new")))
        self._app._new_window_action.setShortcut(
            QKeySequence(self._shortcut("workspace.new_window"))
        )
        self._app._open_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.open")))
        self._app._save_workspace_action.setShortcut(QKeySequence(self._shortcut("workspace.save")))
        self._app._save_workspace_as_action.setShortcut(
            QKeySequence(self._shortcut("workspace.save_as"))
        )
        self._app._repo_dialog_action.setShortcut(QKeySequence(self._shortcut("tab.repo")))
        self._app._refresh_shortcut_tooltips()

        self._app._update_checker._configure_auto_fetch_timer()
        # Re-read the Settings dialog's "Auto sync" checkbox; the controller
        # updates the File-menu toggle and the Repository page from here.
        self._app._task_controller.auto_sync.configure()

    def _build_command_palette_commands(self) -> list[dict[str, object]]:
        """Return every shell and canvas command from their live registries."""
        active_canvas = self._active_canvas()
        entries: list[dict[str, object]] = [
            {
                "id": spec.action_id,
                "title": spec.title,
                "shortcut": _native_keys(self._shortcut(spec.action_id)),
                "keywords": spec.keywords,
                "enabled": not spec.action_id.startswith("canvas.") or active_canvas is not None,
                "disabled_reason": (
                    "Open Draft, Pattern, Trace, or a conversion preview first"
                    if spec.action_id.startswith("canvas.") and active_canvas is None
                    else ""
                ),
                "run": spec.run,
            }
            for spec in self._build_commands()
        ]
        represented_bindings = {spec.action_id for spec in self._build_commands()}
        for command in canvas_commands.COMMANDS:
            if command.hidden or command.keybinding_id in represented_bindings:
                continue
            category = command.category or "Canvas"
            enabled = active_canvas is not None and canvas_commands.can_run(active_canvas, command)
            if active_canvas is None:
                reason = "Open a canvas page first"
            elif enabled:
                reason = ""
            else:
                reason = "Not available in the current context"
            entries.append(
                {
                    "id": command.id,
                    "title": f"{category}: {command.label}",
                    # Read the effective registry shortcut, which reflects
                    # user overrides and platform-native modifier labels.
                    "shortcut": canvas_commands.native_shortcut(command.id),
                    "keywords": f"canvas {category} {command.id.replace('.', ' ')}",
                    "description": command.label,
                    "enabled": enabled,
                    "disabled_reason": reason if not enabled else "",
                    "run": lambda command_id=command.id: self._run_canvas_command(command_id),
                }
            )
        # Page commands belong to the global palette even while another page
        # is active.  Restricting contributions to currentWidget() made the
        # palette's contents change unpredictably between tabs.
        for spec in self._app._page_specs:
            page = self._app._page_runtime.get(spec.page_id)
            page_commands = getattr(page, "command_palette_commands", None)
            if not callable(page_commands):
                continue
            contributed = page_commands()
            if not isinstance(contributed, list):
                contributed = []
            for entry in contributed:
                if not isinstance(entry, dict) or not callable(entry.get("run")):
                    continue
                item = dict(entry)
                title = str(item.get("title", "Command"))
                item["title"] = title if ":" in title else f"{spec.title}: {title}"
                item["keywords"] = " ".join(
                    (
                        str(item.get("keywords", "")),
                        str(item.get("subtitle", "")),
                        spec.page_id,
                        spec.title,
                    )
                )
                item.setdefault("enabled", True)
                item.setdefault("disabled_reason", "")
                callback = item["run"]
                item["run"] = lambda page_id=spec.page_id, run=callback: self._run_page_command(
                    page_id, run
                )
                entries.append(item)
        return entries

    def _run_page_command(self, page_id: str, callback: Callable[[], Any]) -> Any:
        self._app._switch_to_page(page_id)
        return callback()

    def _open_update_check(self) -> None:
        """Open the update check dialog."""
        dlg = UpdateDialog(self._app)
        dlg.exec()

    def _show_help(self) -> None:
        """Show the user manual help dialog."""
        from simple_stipple.features.help import HelpDialog

        HelpDialog.show_help(self._app, self._app, shell_shortcuts=self._shell_shortcut_rows())

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
        tabs = getattr(self._app, "_tabs", None)
        if tabs is None:
            return None
        current = tabs.currentWidget()
        runtime = getattr(self._app, "_page_runtime", None)
        if runtime is not None:
            return runtime.content_canvas_for(current)
        return getattr(current, "_canvas", None)

    def _menu_copy(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.copy()
        else:
            self._run_canvas_command("clipboard.copy")
