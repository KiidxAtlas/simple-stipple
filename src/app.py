"""Main application window."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.backend.document.state import WORKSPACE_FILE_SUFFIX, normalize_workspace_path
from src.backend.io import read_json_file, write_json_file_atomic
from src.error_reporting import report_error
from src.paths import user_data_dir
from src.settings import (
    DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE,
    DEFAULT_DRAW_SIDEBAR_PATH_TOOLS,
    DEFAULT_DRAW_SIDEBAR_SECTIONS,
    DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS,
    DEFAULT_DRAW_SIDEBAR_WIDTH,
    DEFAULT_KEYBINDINGS,
    DEFAULT_RADIAL_MENU_TOOLS,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_SMOOTH_ITERATIONS,
    DEFAULT_SMOOTHING_METHOD,
    load_settings,
    save_settings,
)
from src.ui.canvas import commands as canvas_commands
from src.ui.core.factories import info_chip, surface_frame
from src.ui.core.icons import download_icon, gear_icon
from src.ui.shell.registry import PageSpec, default_page_specs
from src.ui.shell.runtime import PageRuntime
from src.ui.shell.settings_dialog import SettingsDialog
from src.ui.units import DEFAULT_UNIT_SYSTEM
from src.ui.widgets.command_palette import CommandPaletteDialog
from src.ui.widgets.update_dialog import UpdateDialog
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


@dataclass(frozen=True)
class CommandSpec:
    action_id: str
    title: str
    keywords: str
    run: Callable[[], Any]


class App(QMainWindow):
    """Top-level main window coordinating workspace state and cross-tab actions."""

    # Keeps every open window instance alive (Python would otherwise GC a
    # window with no other referrers as soon as _new_window() returns) and
    # lets SingleInstanceGuard find/raise an existing window if relaunched.
    _open_windows: ClassVar[list[App]] = []

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("AA Laser Studio")
        self.resize(1100, 740)
        self.setMinimumSize(860, 580)

        self._settings = load_settings()
        self._settings.setdefault("keybindings", dict(DEFAULT_KEYBINDINGS))
        canvas_commands.apply_keybindings(self._settings.get("keybindings"))
        self._settings.setdefault("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
        # Drop missing entries from recent-files MRU lists on startup so menus
        # never offer dead links. Done lazily after settings load so a stale
        # settings file never blocks startup.
        try:
            from src.ui.util.recent_files import prune_missing

            prune_missing(self._settings)
        except Exception:  # never block startup on a stale list
            logging.getLogger(__name__).exception("Failed to prune recent-files MRU")
        self._workspace_path: Path | None = None
        self._workspace_dirty: bool = False
        self._last_saved_document: dict | None = None
        self._has_unsaved_changes: bool = False
        self._workspace_timer = QTimer(self)
        self._workspace_timer.setSingleShot(True)
        self._workspace_timer.timeout.connect(self._update_workspace_dirty)

        # Crash recovery: periodically snapshot unsaved work to the app data
        # dir; a clean exit or successful save removes the snapshot.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(90_000)
        self._autosave_timer.timeout.connect(self._autosave_workspace)
        self._autosave_timer.start()

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
        self._page_runtime.apply_unit_system(
            self._settings.get("unit_system", DEFAULT_UNIT_SYSTEM)
        )
        self._page_runtime.apply_radial_menu_tools(
            self._settings.get("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
        )
        self._page_runtime.apply_smoothing_method(
            self._settings.get("smoothing_method", DEFAULT_SMOOTHING_METHOD)
        )
        self._page_runtime.apply_smooth_iterations(
            self._settings.get("smooth_iterations", DEFAULT_SMOOTH_ITERATIONS)
        )
        self._page_runtime.apply_simplify_tolerance(
            self._settings.get("simplify_tolerance", DEFAULT_SIMPLIFY_TOLERANCE)
        )
        self._page_runtime.apply_draw_sidebar_width(
            self._settings.get("draw_sidebar_width", DEFAULT_DRAW_SIDEBAR_WIDTH)
        )
        self._page_runtime.apply_draw_sidebar_height(
            self._settings.get("draw_sidebar_height")
        )
        self._page_runtime.apply_draw_sidebar_sections(
            self._settings.get(
                "draw_sidebar_sections", list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
            )
        )
        self._page_runtime.apply_draw_sidebar_path_tools(
            self._settings.get(
                "draw_sidebar_path_tools", list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
            )
        )
        self._page_runtime.apply_draw_sidebar_shape_tools(
            self._settings.get(
                "draw_sidebar_shape_tools", list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
            )
        )
        self._page_runtime.apply_draw_sidebar_always_visible(
            self._settings.get(
                "draw_sidebar_always_visible", DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE
            )
        )
        self._page_runtime.connect_draw_sidebar_width_changed(
            self._on_draw_sidebar_width_changed
        )
        self._page_runtime.connect_draw_sidebar_height_changed(
            self._on_draw_sidebar_height_changed
        )
        self._page_runtime.connect_smoothing_method_changed(
            self._on_smoothing_method_changed
        )
        self._page_runtime.connect_smooth_iterations_changed(
            self._on_smooth_iterations_changed
        )
        self._page_runtime.connect_simplify_tolerance_changed(
            self._on_simplify_tolerance_changed
        )
        self._tabs.currentChanged.connect(self._schedule_workspace_dirty_check)
        self._tabs.currentChanged.connect(lambda _: self._refresh_workspace_header())

        self._workspace_menu = self.menuBar().addMenu("File")
        self._recent_workspaces_menu = self._workspace_menu.addMenu("Open Recent")
        self._build_workspace_actions()
        self._rebuild_recent_workspaces_menu()
        self._build_edit_view_help_menus()

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        self._global_actions: dict[str, QAction] = {}
        self._setup_global_shortcuts()

        self._clear_workspace_state()
        self._last_saved_document = self._collect_workspace_document()
        self._update_title()

    def _init_tab_bindings(self) -> None:
        self._draft_page: Any = self._page_runtime.get("draft")
        self._pattern_page: Any = self._page_runtime.get("pattern")
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
                btn.setToolTip(f"{text} ({self._shortcut(shortcut_hint)})")
            layout.addWidget(btn)

        # Settings — visually separated
        update_btn = QPushButton()
        update_btn.setIcon(download_icon())
        update_btn.setIconSize(QSize(18, 18))
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

        settings_btn = QPushButton()
        settings_btn.setIcon(gear_icon())
        settings_btn.setIconSize(QSize(18, 18))
        settings_btn.setFixedSize(30, 30)
        settings_btn.setToolTip(f"Settings ({self._shortcut('app.settings')})")
        settings_btn.clicked.connect(self._open_settings)
        layout.addWidget(settings_btn)

        help_btn = QPushButton("?")
        help_btn.setFixedSize(30, 30)
        help_btn.setToolTip("User Manual")
        help_btn.clicked.connect(self._show_help)
        layout.addWidget(help_btn)

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

        self._new_window_action = QAction("New Window", self)
        self._new_window_action.setShortcut(
            QKeySequence(self._shortcut("workspace.new_window"))
        )
        self._new_window_action.triggered.connect(self._new_window)
        self._workspace_menu.addAction(self._new_window_action)

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
        self._has_unsaved_changes = True
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

    def _on_draw_sidebar_width_changed(self, width: int) -> None:
        """Persist a live sidebar-resize drag and echo the new width to
        every other tab's sidebar so they stay consistent."""
        self._settings["draw_sidebar_width"] = width
        save_settings(self._settings)
        self._page_runtime.apply_draw_sidebar_width(width)

    def _on_draw_sidebar_height_changed(self, height: int) -> None:
        """Persist a live sidebar-resize drag and echo the new height to
        every other tab's sidebar so they stay consistent."""
        self._settings["draw_sidebar_height"] = height
        save_settings(self._settings)
        self._page_runtime.apply_draw_sidebar_height(height)

    def _on_smoothing_method_changed(self, method: str) -> None:
        """Persist a sidebar-driven smoothing-method change and echo it to
        every other tab, matching Settings dialog's own persistence."""
        self._settings["smoothing_method"] = method
        save_settings(self._settings)
        self._page_runtime.apply_smoothing_method(method)

    def _on_smooth_iterations_changed(self, iterations: int) -> None:
        """Remember the last value typed into the Smooth HUD prompt so the
        user doesn't have to retype it every time, and echo it to every
        other tab."""
        self._settings["smooth_iterations"] = iterations
        save_settings(self._settings)
        self._page_runtime.apply_smooth_iterations(iterations)

    def _on_simplify_tolerance_changed(self, tolerance: float) -> None:
        """Remember the last value typed into the Simplify HUD prompt so
        the user doesn't have to retype it every time, and echo it to
        every other tab."""
        self._settings["simplify_tolerance"] = tolerance
        save_settings(self._settings)
        self._page_runtime.apply_simplify_tolerance(tolerance)

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

    @staticmethod
    def _autosave_path() -> Path:
        return user_data_dir() / "autosave.workspace.json"

    def _autosave_workspace(self) -> None:
        if not self._workspace_dirty or not self._has_workspace_content():
            return
        try:
            write_json_file_atomic(
                self._autosave_path(), self._collect_workspace_document()
            )
        except Exception as exc:  # noqa: BLE001 — autosave must never crash
            LOGGER.warning("Workspace autosave failed: %s", exc)

    def _discard_autosave(self) -> None:
        try:
            self._autosave_path().unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("Could not remove autosave: %s", exc)

    def _offer_autosave_recovery(self) -> None:
        path = self._autosave_path()
        if not path.exists():
            return
        reply = QMessageBox.question(
            self,
            "Recover Unsaved Work",
            "A workspace snapshot from a previous session was found "
            "(the app may not have closed cleanly).\n\nRestore it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self._apply_workspace_document(read_json_file(path))
                self._workspace_dirty = True
                self._update_title()
            except (OSError, ValueError, KeyError) as exc:
                QMessageBox.warning(
                    self, "Recovery Failed", f"Could not restore snapshot:\n{exc}"
                )
                return  # keep the file for manual inspection
        self._discard_autosave()

    def _update_workspace_dirty(self) -> None:
        if not self._has_unsaved_changes:
            self._workspace_dirty = False
        elif self._last_saved_document is not None:
            self._workspace_dirty = (
                self._collect_workspace_document() != self._last_saved_document
            )
        else:
            self._workspace_dirty = False
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
            action = QAction(path.name, self._recent_workspaces_menu)
            action.setData(str(path))
            action.setToolTip(str(path))
            action.triggered.connect(self._load_recent_workspace_action)
            self._recent_workspaces_menu.addAction(action)

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
        self._has_unsaved_changes = False
        self._update_title()

    def _new_window(self) -> None:
        """Open a second, fully independent window — its own workspace
        path/dirty state, its own PageRuntime — sharing only the on-disk
        settings/recent-files each window reads at construction time."""
        window = App()
        App._open_windows.append(window)
        window.show()

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
            self._has_unsaved_changes = False
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
            self._has_unsaved_changes = False
            self._remember_workspace_path(self._workspace_path)
            self._update_title()
            self._discard_autosave()
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

    # ── Edit / View / Help menus ──────────────────────────────────────────

    def _build_edit_view_help_menus(self) -> None:
        """Standard menus routing to the active page's canvas.

        Text-editing shortcuts (undo/cut/copy/paste/select-all) check the
        focused widget first so they never hijack typing in a line edit.
        """
        edit_menu = self.menuBar().addMenu("Edit")

        def add(menu, text, slot, shortcut=None):
            action = QAction(text, self)
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
        self._grid_action = QAction("Show Grid", self, checkable=True)
        self._grid_action.triggered.connect(
            lambda checked: self._canvas_call("set_grid_visible", checked)
        )
        view_menu.addAction(self._grid_action)
        self._snap_action = QAction("Snap to Grid", self, checkable=True)
        self._snap_action.triggered.connect(
            lambda checked: self._canvas_call("set_grid_snap", checked)
        )
        view_menu.addAction(self._snap_action)
        view_menu.aboutToShow.connect(self._sync_view_menu_state)

        help_menu = self.menuBar().addMenu("Help")
        add(help_menu, "Keyboard Shortcuts…", self._show_shortcuts_reference)

    def _canvas_call(self, name: str, *args) -> None:
        canvas = self._active_canvas()
        fn = getattr(canvas, name, None)
        if callable(fn):
            fn(*args)

    def _run_canvas_command(self, cmd_id: str) -> None:
        canvas = self._active_canvas()
        if canvas is not None:
            canvas_commands.run(canvas, cmd_id)

    def _focused_text_widget(self):
        widget = QApplication.focusWidget()
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return widget
        return None

    def _menu_undo(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.undo()
        else:
            self._run_canvas_command("edit.undo")

    def _menu_redo(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.redo()
        else:
            self._run_canvas_command("edit.redo")

    def _menu_cut(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.cut()
        else:
            self._run_canvas_command("clipboard.cut")

    def _menu_copy(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.copy()
        else:
            self._run_canvas_command("clipboard.copy")

    def _menu_paste(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.paste()
        else:
            self._run_canvas_command("clipboard.paste")

    def _menu_select_all(self) -> None:
        text = self._focused_text_widget()
        if text is not None:
            text.selectAll()
        else:
            self._run_canvas_command("select.all")

    def _sync_view_menu_state(self) -> None:
        canvas = self._active_canvas()
        has_canvas = canvas is not None
        self._grid_action.setEnabled(has_canvas)
        self._snap_action.setEnabled(has_canvas)
        if has_canvas:
            self._grid_action.setChecked(bool(getattr(canvas, "_grid_visible", False)))
            self._snap_action.setChecked(bool(getattr(canvas, "_grid_snap", False)))

    def _show_shortcuts_reference(self) -> None:
        rows = [
            ("Workspace", ""),
            ("New / Open / Save / Save As", "per File menu"),
            ("Command palette", self._shortcut("app.command_palette")),
            ("Settings", self._shortcut("app.settings")),
            ("Switch tabs", "Alt+1 … Alt+5"),
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
            self,
            "Keyboard Shortcuts",
            "<br>".join(lines),
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard_if_dirty():
            event.ignore()
            return
        # Stop timers before children destroyed to avoid late callbacks.
        for timer in (
            getattr(self, "_workspace_timer", None),
            getattr(self, "_auto_fetch_timer", None),
            getattr(self, "_autosave_timer", None),
        ):
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass
        # Persist settings on exit so any in-memory changes survive.
        try:
            save_settings(self._settings)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to persist settings on close: %s", exc)
        # Cancel/clean up tracked workers if pages expose a hook.
        for page in self._page_runtime.iter_workspace_pages():
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception as exc:  # noqa: BLE001
                    report_error("Page shutdown failed", exc)
        self._discard_autosave()
        if self in App._open_windows:
            App._open_windows.remove(self)
        event.accept()

    def showEvent(self, event) -> None:
        """Handle window show event — perform startup tasks like auto-fetch and update check."""
        super().showEvent(event)
        if hasattr(self, "_startup_done"):
            return
        self._startup_done = True

        QTimer.singleShot(200, self._offer_autosave_recovery)

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
                    lambda page_id=spec.page_id: self._switch_to_page(page_id),
                )
            )
        return cmds

    def _setup_global_shortcuts(self) -> None:
        # workspace.* commands are already QActions via _build_workspace_actions
        _MENU_HANDLED = {
            "workspace.new",
            "workspace.new_window",
            "workspace.open",
            "workspace.save",
            "workspace.save_as",
        }
        for cmd in self._build_commands():
            if cmd.action_id not in _MENU_HANDLED:
                self._register_action(cmd.action_id, cmd.title, cmd.run)

    def _register_action(
        self, action_id: str, text: str, callback, *, shortcut: str | None = None
    ) -> None:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        else:
            sc = self._shortcut(action_id)
            if sc:
                action.setShortcut(QKeySequence(sc))
        action.triggered.connect(callback)
        self.addAction(action)
        self._global_actions[action_id] = action

    def _active_canvas(self) -> Any | None:
        current = self._tabs.currentWidget()
        return getattr(current, "_canvas", None)

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

    def _invoke_canvas_mode(self, mode: str) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "set_mode"):
            current = canvas.get_mode() if hasattr(canvas, "get_mode") else None
            canvas.set_mode(mode if current != mode else "select")

    def _invoke_canvas_measure(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "toggle_measure"):
            canvas.toggle_measure()

    def _invoke_canvas_dimension(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "toggle_dimension_mode"):
            canvas.toggle_dimension_mode()

    def _invoke_canvas_fit(self) -> None:
        canvas = self._active_canvas()
        if canvas is not None and hasattr(canvas, "fit"):
            canvas.fit()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

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
                self._has_unsaved_changes = False
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
            canvas_commands.apply_keybindings(self._settings.get("keybindings"))
            self._page_runtime.apply_settings(self._settings)
            self._page_runtime.apply_unit_system(
                self._settings.get("unit_system", DEFAULT_UNIT_SYSTEM)
            )
            self._page_runtime.apply_smoothing_method(
                self._settings.get("smoothing_method", DEFAULT_SMOOTHING_METHOD)
            )
            self._page_runtime.apply_smooth_iterations(
                self._settings.get("smooth_iterations", DEFAULT_SMOOTH_ITERATIONS)
            )
            self._page_runtime.apply_simplify_tolerance(
                self._settings.get("simplify_tolerance", DEFAULT_SIMPLIFY_TOLERANCE)
            )
            self._settings.setdefault("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
            self._page_runtime.apply_radial_menu_tools(
                self._settings.get("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
            )
            self._page_runtime.apply_draw_sidebar_sections(
                self._settings.get(
                    "draw_sidebar_sections", list(DEFAULT_DRAW_SIDEBAR_SECTIONS)
                )
            )
            self._page_runtime.apply_draw_sidebar_path_tools(
                self._settings.get(
                    "draw_sidebar_path_tools", list(DEFAULT_DRAW_SIDEBAR_PATH_TOOLS)
                )
            )
            self._page_runtime.apply_draw_sidebar_shape_tools(
                self._settings.get(
                    "draw_sidebar_shape_tools", list(DEFAULT_DRAW_SIDEBAR_SHAPE_TOOLS)
                )
            )
            self._page_runtime.apply_draw_sidebar_always_visible(
                self._settings.get(
                    "draw_sidebar_always_visible", DEFAULT_DRAW_SIDEBAR_ALWAYS_VISIBLE
                )
            )
            self._repo_page.sync_from_settings()

            for action_id, action in self._global_actions.items():
                shortcut = self._shortcut(action_id)
                action.setShortcut(
                    QKeySequence(shortcut) if shortcut else QKeySequence()
                )

            self._new_workspace_action.setShortcut(
                QKeySequence(self._shortcut("workspace.new"))
            )
            self._new_window_action.setShortcut(
                QKeySequence(self._shortcut("workspace.new_window"))
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

    def _show_help(self) -> None:
        """Show the user manual help dialog."""
        from src.ui.pages.help import HelpDialog

        HelpDialog.show_help(self, self)

    def _open_update_check(self) -> None:
        """Open the update check dialog."""
        dlg = UpdateDialog(self)
        dlg.exec()
