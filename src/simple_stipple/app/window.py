"""Main application window."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.app.menu import CommandController, MenuController
from simple_stipple.app.pages import PageRuntime, PageSpec, default_page_specs
from simple_stipple.app.settings_controller import SettingsController
from simple_stipple.app.tasks import TaskController
from simple_stipple.app.workspace_controller import WorkspaceController
from simple_stipple.canvas import commands as canvas_commands
from simple_stipple.platform.error_reporting import report_error
from simple_stipple.platform.paths import user_data_dir
from simple_stipple.platform.settings import (
    DEFAULT_KEYBINDINGS,
    DEFAULT_RADIAL_MENU_TOOLS,
    load_settings,
    save_settings,
    settings_bus,
)
from simple_stipple.ui.style.theme import accessibility_palette, apply_dark_theme, load_app_qss

LOGGER = logging.getLogger(__name__)

_FALLBACK_POINT_SIZE = 12.0


def resolve_scaled_point_size(
    current_point_size: float, stored_base: object, ui_scale: float
) -> tuple[float, float]:
    """Return ``(base, scaled)`` point sizes for the global UI font.

    Qt silently accepts a non-positive point size and then renders text as
    unusably small, so both ways this can go non-positive are closed here:

    * ``QFont.pointSizeF()`` returns ``-1`` when the font was defined in pixels
      (some platform default fonts are), and
    * ``ui_scale`` may be missing, ``0``, or otherwise corrupt in settings.

    ``base`` is always the genuine unscaled size — a previously stored base wins
    over the (possibly already-scaled) live font, so repeated calls never
    compound. By construction the returned sizes are strictly positive, making a
    negative/zero global font size unreachable.
    """
    if isinstance(stored_base, (int, float)) and float(stored_base) > 0:
        base = float(stored_base)
    elif current_point_size > 0:
        base = float(current_point_size)
    else:
        base = _FALLBACK_POINT_SIZE
    scale = ui_scale if ui_scale and ui_scale > 0 else 1.0
    return base, base * scale


class App(QMainWindow):
    """Top-level main window coordinating workspace state and cross-tab actions."""

    # Keeps every open window instance alive (Python would otherwise GC a
    # window with no other referrers as soon as _new_window() returns) and
    # lets SingleInstanceGuard find/raise an existing window if relaunched.
    _open_windows: ClassVar[list[App]] = []
    _grid_action: QAction
    _snap_action: QAction
    _new_workspace_action: QAction
    _new_window_action: QAction
    _open_workspace_action: QAction
    _saved_workspaces_action: QAction
    _save_workspace_action: QAction
    _save_workspace_as_action: QAction
    _recover_workspace_action: QAction
    _repo_dialog_action: QAction
    _workspace_title_label: QPushButton
    _workspace_state_chip: QLabel
    _shortcut_tooltip_specs: list[tuple[QWidget, str, str]]

    def _restore_window_geometry(self) -> None:
        """Restore size/position saved by ``_save_window_geometry`` — window
        geometry was never persisted, unlike splitter and sidebar widths, so
        the window reset to a fixed 1280x820 on every launch."""
        raw = self._settings.get("window_geometry")
        if not isinstance(raw, str) or not raw:
            return
        try:
            geometry = QByteArray.fromHex(raw.encode("ascii"))
        except (TypeError, ValueError):
            return
        self.restoreGeometry(geometry)

    def _save_window_geometry(self) -> None:
        self._settings["window_geometry"] = bytes(self.saveGeometry().toHex().data()).decode("ascii")

    def _autosave_path(self) -> Path:
        return user_data_dir() / "recovery" / f"{self._recovery_id}.workspace.json"

    def _autosave_workspace(self) -> None:
        """Compatibility entry point that waits for the delegated snapshot.

        The periodic timer calls the controller directly and remains fully
        asynchronous.  Explicit callers historically relied on this helper
        returning only after the recovery file was durable.
        """
        self._autosave_controller._autosave_workspace()
        thread = self._autosave_controller._recovery_write_thread
        if thread is not None:
            thread.join(timeout=10.0)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._recovery_id = uuid4().hex
        self._menu_controller = MenuController(self)
        self._command_controller = CommandController(self)
        self.setWindowTitle("Simple Stipple")
        self.resize(1280, 820)
        self.setMinimumSize(900, 600)

        self._settings = load_settings()
        self._restore_window_geometry()
        self._settings.setdefault("keybindings", dict(DEFAULT_KEYBINDINGS))
        canvas_commands.apply_keybindings(self._settings.get("keybindings"))
        self._settings.setdefault("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
        self._apply_accessibility_settings()
        # Drop missing entries from recent-files MRU lists on startup so menus
        # never offer dead links. Done lazily after settings load so a stale
        # settings file never blocks startup.
        try:
            from simple_stipple.ui.recent import prune_missing

            prune_missing(self._settings)
        except Exception:  # never block startup on a stale list
            logging.getLogger(__name__).exception("Failed to prune recent-files MRU")
        self._workspace_path: Path | None = None
        self._restored_recovery_path: Path | None = None
        self._workspace_dirty: bool = False
        self._last_saved_document: dict | None = None
        self._last_autosave_at: datetime | None = None
        self._has_unsaved_changes: bool = False
        self._workspace_timer = QTimer(self)
        self._workspace_timer.setSingleShot(True)

        self._task_controller = TaskController(self)
        # Compatibility aliases while callers migrate to the lifecycle facade.
        self._autosave_controller = self._task_controller.autosave
        self._update_checker = self._task_controller.updates

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 8)
        central_layout.setSpacing(8)
        self.setCentralWidget(central)

        # ── Tabs ──────────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        central_layout.addWidget(self._tabs, stretch=1)

        self._page_specs: tuple[PageSpec, ...] = default_page_specs()
        self._page_runtime = PageRuntime(
            tab_widget=self._tabs,
            settings=self._settings,
            specs=self._page_specs,
        )
        for index, tooltip in enumerate(
            (
                "Draft — create, import, and edit drawing geometry",
                "Pattern — generate fills from a prepared outline",
                "Trace — turn a raster image into editable vector outlines",
                "Convert — convert or repair vector files",
                "Repository — review, commit, pull, and push Git changes",
            )
        ):
            self._tabs.setTabToolTip(index, tooltip)
        self._workspace_controller = WorkspaceController(self, self._page_runtime, self._tabs)
        self._workspace_timer.timeout.connect(self._update_workspace_dirty)
        self._shell_header = self._build_shell_header()
        central_layout.insertWidget(0, self._shell_header)
        self._system_banner = self._build_system_banner()
        central_layout.insertWidget(1, self._system_banner)
        self._init_tab_bindings()
        self._settings_controller = SettingsController(
            self._settings, self._page_runtime, source=self
        )
        settings_bus.changed.connect(self._on_external_setting_changed)
        self._page_runtime.apply_all(self._settings)
        self._page_runtime.connect_echoes(self._on_setting_echo)
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

    # Explicit controller facade keeps every integration point named and its
    # ownership discoverable to readers and type checkers.
    def _build_shell_header(self) -> QWidget:
        return self._menu_controller._build_shell_header()

    def _build_system_banner(self) -> QFrame:
        banner = QFrame()
        banner.setProperty("role", "system-banner")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(12, 8, 8, 8)
        self._system_banner_text = QLabel("")
        self._system_banner_text.setWordWrap(True)
        layout.addWidget(self._system_banner_text, 1)
        retry = QPushButton("Retry")
        retry.clicked.connect(self._autosave_controller._autosave)
        layout.addWidget(retry)
        manage_storage = QPushButton("Manage storage")
        manage_storage.clicked.connect(self._open_saved_workspaces)
        layout.addWidget(manage_storage)
        save_as = QPushButton("Save As…")
        save_as.setProperty("role", "primary")
        save_as.clicked.connect(self._save_workspace_as)
        layout.addWidget(save_as)
        dismiss = QPushButton("Dismiss")
        dismiss.setProperty("role", "ghost")
        dismiss.clicked.connect(banner.hide)
        layout.addWidget(dismiss)
        banner.hide()
        return banner

    def show_system_failure(self, message: str) -> None:
        """Show a persistent, actionable failure above every workflow."""
        self._system_banner_text.setText(message)
        self._system_banner.setAccessibleDescription(message)
        self._system_banner.show()

    def clear_system_failure(self) -> None:
        self._system_banner.hide()

    def _build_edit_view_help_menus(self) -> None:
        self._menu_controller._build_edit_view_help_menus()

    def _setup_global_shortcuts(self) -> None:
        self._command_controller._setup_global_shortcuts()

    def _refresh_workspace_header(self) -> None:
        self._menu_controller._refresh_workspace_header()

    def _build_workspace_actions(self) -> None:
        self._workspace_controller._build_workspace_actions()

    def _rebuild_recent_workspaces_menu(self) -> None:
        self._workspace_controller._rebuild_recent_workspaces_menu()

    def _clear_workspace_state(self) -> None:
        self._workspace_controller._clear_workspace_state()

    def _collect_workspace_document(self) -> dict:
        return self._workspace_controller._collect_workspace_document()

    def _apply_workspace_document(self, document: dict) -> None:
        self._workspace_controller._apply_workspace_document(document)

    def _schedule_workspace_dirty_check(self) -> None:
        self._workspace_controller._schedule_workspace_dirty_check()

    def _update_workspace_dirty(self) -> None:
        self._workspace_controller._update_workspace_dirty()

    def _update_title(self) -> None:
        self._workspace_controller._update_title()

    def _confirm_discard_if_dirty(self, *, quitting: bool = False) -> bool:
        return self._workspace_controller._confirm_discard_if_dirty(quitting=quitting)

    def _new_workspace(self) -> None:
        self._workspace_controller._new_workspace()

    def _open_workspace(self) -> None:
        self._workspace_controller._open_workspace()

    def _open_saved_workspaces(self, *, initial_source: str = "saved") -> None:
        self._workspace_controller._open_saved_workspaces(initial_source=initial_source)

    def _save_workspace(self) -> bool:
        return self._workspace_controller._save_workspace()

    def _save_workspace_as(self) -> bool:
        return self._workspace_controller._save_workspace_as()

    def _active_canvas(self):
        return self._command_controller._active_canvas()

    def _canvas_call(self, name: str, *args) -> None:
        self._command_controller._canvas_call(name, *args)

    def _menu_undo(self) -> None:
        self._command_controller._menu_undo()

    def _menu_redo(self) -> None:
        self._command_controller._menu_redo()

    def _menu_cut(self) -> None:
        self._command_controller._menu_cut()

    def _menu_copy(self) -> None:
        self._command_controller._menu_copy()

    def _menu_paste(self) -> None:
        self._command_controller._menu_paste()

    def _menu_select_all(self) -> None:
        self._command_controller._menu_select_all()

    def _run_canvas_command(self, command_id: str) -> None:
        self._command_controller._run_canvas_command(command_id)

    def _shortcut(self, action_id: str) -> str:
        return self._command_controller._shortcut(action_id)

    def _open_command_palette(self) -> None:
        self._command_controller._open_command_palette()

    def _open_settings(self) -> None:
        self._command_controller._open_settings()

    def _open_update_check(self) -> None:
        self._command_controller._open_update_check()

    def _show_help(self) -> None:
        self._command_controller._show_help()

    def _refresh_shortcut_tooltips(self) -> None:
        self._menu_controller._refresh_shortcut_tooltips()

    def _init_tab_bindings(self) -> None:
        self._draft_page: Any = self._page_runtime.get("draft")
        self._pattern_page: Any = cast(Any, self._page_runtime.get("pattern"))
        # Repository Sync is a first-class page; the File menu action points
        # to this same instance so its state cannot diverge.
        self._repo_page: Any = self._page_runtime.get("repository")

        self._page_runtime.connect_state_changed(self._schedule_workspace_dirty_check)

        self._page_runtime.connect_signal_if_present(
            page_id="draft",
            signal_name="sendSelectedToPatternRequested",
            slot=lambda polys: self._send_shape_selection_to_pattern(polys, source_label="Draft"),
        )
        self._page_runtime.connect_signal_if_present(
            page_id="pattern",
            signal_name="sendSelectedToDraftRequested",
            slot=self._send_pattern_selection_to_draft,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="pattern",
            signal_name="repairTileRequested",
            slot=self._repair_pattern_tile,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="trace",
            signal_name="sendSelectedToDraftRequested",
            slot=self._send_pattern_selection_to_draft,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="trace",
            signal_name="sendSelectedToPatternRequested",
            slot=lambda polys: self._send_shape_selection_to_pattern(polys, source_label="Trace"),
        )
        self._page_runtime.connect_signal_if_present(
            page_id="convert",
            signal_name="openInDraftRequested",
            slot=self._send_pattern_selection_to_draft,
        )
        self._page_runtime.connect_signal_if_present(
            page_id="convert",
            signal_name="openInPatternRequested",
            slot=lambda polys: self._send_shape_selection_to_pattern(polys, source_label="Convert"),
        )
        for page_id in ("draft", "trace"):
            self._page_runtime.connect_signal_if_present(
                page_id=page_id,
                signal_name="customTileRequested",
                slot=self._pattern_page.use_custom_tile,
            )

    def _switch_to_page(self, page_id: str) -> None:
        self._page_runtime.switch_to(page_id)

    def _repair_pattern_tile(self, path: str) -> None:
        convert_page = self._page_runtime.get("convert")
        if convert_page is not None and hasattr(convert_page, "open_repair_input"):
            convert_page.open_repair_input(path)  # type: ignore[attr-defined]
        self._page_runtime.switch_to("convert")

    def _open_repo_dialog(self) -> None:
        """Navigate to Repository Sync from the File menu."""
        self._page_runtime.switch_to("repository")

    def _has_workspace_content(self) -> bool:
        return self._page_runtime.has_workspace_content()

    def _send_shape_selection_to_pattern(
        self,
        polys: list[list[tuple[float, float]]],
        *,
        source_label: str = "Draft",
    ) -> None:
        if not polys:
            return
        self._pattern_page.load_outline_polys(
            polys,
            source_label=f"{source_label} selection",
            offer_undo=True,
        )
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

    def _on_setting_echo(self, key: str, value: Any) -> None:
        self._settings_controller.update(key, value)

    def _on_external_setting_changed(self, key: str, value: Any, source: object) -> None:
        if source is self:
            return
        self._settings[key] = value
        # Apply the setting to canvases if it's in the sync table, otherwise apply to pages
        try:
            self._page_runtime.apply(key, value)
        except KeyError:
            # Setting not in sync table; apply to all pages via apply_settings
            self._page_runtime.apply_settings(self._settings)
        if key in {
            "appearance",
            "interface_density",
            "ui_scale",
            "high_contrast",
            "reduced_motion",
            "persistent_notifications",
        }:
            self._apply_accessibility_settings()

    def _apply_accessibility_settings(self) -> None:
        instance = QApplication.instance()
        if instance is None:
            return
        app = cast(QApplication, instance)
        font = app.font()
        base_size, scaled_size = resolve_scaled_point_size(
            font.pointSizeF(),
            app.property("basePointSize"),
            float(self._settings.get("ui_scale", 1.0) or 1.0),
        )
        app.setProperty("basePointSize", base_size)
        font.setPointSizeF(scaled_size)
        app.setFont(font)
        high_contrast = bool(self._settings.get("high_contrast", False))
        appearance = str(self._settings.get("appearance", "system"))
        if appearance == "system":
            appearance = (
                "dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
            )
        app.setProperty("highContrast", high_contrast)
        app.setProperty("appearance", appearance)
        app.setProperty("reducedMotion", bool(self._settings.get("reduced_motion", False)))
        density = str(self._settings.get("interface_density", "compact"))
        app.setProperty("interfaceDensity", density)
        app.setPalette(accessibility_palette(high_contrast, appearance))
        app.setStyleSheet(
            load_app_qss(
                scale=float(self._settings.get("ui_scale", 1.0) or 1.0),
                high_contrast=high_contrast,
                appearance=appearance,
                density=density,
            )
        )

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

    def _new_window(self) -> None:
        """Open a second, fully independent window — its own workspace
        path/dirty state, its own PageRuntime — sharing only the on-disk
        settings/recent-files each window reads at construction time."""
        window = App()
        App._open_windows.append(window)
        window.show()

    # ── Edit / View / Help menus ──────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard_if_dirty(quitting=True):
            event.ignore()
            return
        # Stop timers before children destroyed to avoid late callbacks.
        self._task_controller.shutdown()
        settings_bus.changed.disconnect(self._on_external_setting_changed)
        # Persist settings on exit so any in-memory changes survive.
        self._save_window_geometry()
        try:
            save_settings(self._settings)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to persist settings on close: %s", exc)
        # Cancel/clean up tracked workers if pages expose a hook.
        # (iter_workspace_pages yields (page_id, page) tuples — unpacking
        # matters, or getattr on the tuple silently skips every shutdown.)
        for _page_id, page in self._page_runtime.iter_workspace_pages():
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception as exc:  # noqa: BLE001
                    report_error("Page shutdown failed", exc)
        # Keep the autosave snapshot when unsaved changes were discarded —
        # it is the only path back to that work. Saving already discards it.
        if not self._workspace_dirty:
            self._autosave_controller._discard_autosave()
        if self in App._open_windows:
            App._open_windows.remove(self)
        event.accept()

    def showEvent(self, event) -> None:
        """Handle window show event — perform startup tasks like auto-fetch and update check."""
        super().showEvent(event)
        if hasattr(self, "_startup_done"):
            return
        self._startup_done = True

        self._task_controller.startup(
            check_updates=bool(self._settings.get("check_updates_on_startup", False))
        )

    @staticmethod
    def apply_theme(application: QApplication) -> None:
        """Apply presentation styling at the application composition boundary."""
        apply_dark_theme(application)
