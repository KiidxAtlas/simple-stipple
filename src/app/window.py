"""Main application window."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.controllers import (
    CommandController,
    MenuController,
    SettingsController,
    TaskController,
    WorkspaceController,
)
from src.app.page_runtime import PageRuntime
from src.infra.error_reporting import report_error
from src.infra.paths import user_data_dir
from src.infra.settings import (
    DEFAULT_KEYBINDINGS,
    DEFAULT_RADIAL_MENU_TOOLS,
    load_settings,
    save_settings,
)
from src.infra.settings_bus import settings_bus
from src.ui.canvas.interaction import commands as canvas_commands
from src.ui.pages.registry import PageSpec, default_page_specs
from src.ui.style.theme import accessibility_palette

LOGGER = logging.getLogger(__name__)


class App(QMainWindow):
    """Top-level main window coordinating workspace state and cross-tab actions."""

    # Keeps every open window instance alive (Python would otherwise GC a
    # window with no other referrers as soon as _new_window() returns) and
    # lets SingleInstanceGuard find/raise an existing window if relaunched.
    _open_windows: ClassVar[list[App]] = []

    def _autosave_path(self) -> Path:
        return user_data_dir() / "recovery" / f"{self._recovery_id}.workspace.json"

    def _autosave_workspace(self) -> None:
        """Compatibility entry point delegated to the task controller."""
        self._autosave_controller._autosave_workspace()

    def __getattr__(self, name: str):
        """Delegate extracted shell/command compatibility methods."""
        state = object.__getattribute__(self, "__dict__")
        for key in ("_workspace_controller", "_menu_controller", "_command_controller"):
            controller = state.get(key)
            if controller is not None and hasattr(type(controller), name):
                return getattr(controller, name)
        raise AttributeError(name)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._recovery_id = uuid4().hex
        self._menu_controller = MenuController(self)
        self._command_controller = CommandController(self)
        self.setWindowTitle("AA Laser Studio")
        self.resize(1100, 740)
        self.setMinimumSize(860, 580)

        self._settings = load_settings()
        self._settings.setdefault("keybindings", dict(DEFAULT_KEYBINDINGS))
        canvas_commands.apply_keybindings(self._settings.get("keybindings"))
        self._settings.setdefault("radial_menu_tools", list(DEFAULT_RADIAL_MENU_TOOLS))
        self._apply_accessibility_settings()
        # Drop missing entries from recent-files MRU lists on startup so menus
        # never offer dead links. Done lazily after settings load so a stale
        # settings file never blocks startup.
        try:
            from src.ui.util import prune_missing

            prune_missing(self._settings)
        except Exception:  # never block startup on a stale list
            logging.getLogger(__name__).exception("Failed to prune recent-files MRU")
        self._workspace_path: Path | None = None
        self._workspace_dirty: bool = False
        self._last_saved_document: dict | None = None
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
        central_layout.setSpacing(6)
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
        self._workspace_controller = WorkspaceController(self, self._page_runtime, self._tabs)
        self._workspace_timer.timeout.connect(self._update_workspace_dirty)
        self._shell_header = self._build_shell_header()
        central_layout.insertWidget(0, self._shell_header)
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

    def _init_tab_bindings(self) -> None:
        self._draft_page: Any = self._page_runtime.get("draft")
        self._pattern_page: Any = cast(Any, self._page_runtime.get("pattern"))
        self._repo_page: Any = self._page_runtime.get("repo")

        self._page_runtime.connect_state_changed(self._schedule_workspace_dirty_check)

        self._page_runtime.connect_signal_if_present(
            page_id="draft",
            signal_name="sendSelectedToPatternRequested",
            slot=self._send_shape_selection_to_pattern,
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
        for page_id in ("draft", "trace"):
            self._page_runtime.connect_signal_if_present(
                page_id=page_id,
                signal_name="customTileRequested",
                slot=self._pattern_page.use_custom_tile,
            )

    def _switch_to_page(self, page_id: str) -> None:
        self._page_runtime.switch_to(page_id)

    def _has_workspace_content(self) -> bool:
        return self._page_runtime.has_workspace_content()

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

    def _on_setting_echo(self, key: str, value: Any) -> None:
        self._settings_controller.update(key, value)

    def _on_external_setting_changed(self, key: str, value: Any, source: object) -> None:
        if source is self:
            return
        self._settings[key] = value
        self._page_runtime.apply(key, value)
        if key in {"ui_scale", "high_contrast", "reduced_motion", "persistent_notifications"}:
            self._apply_accessibility_settings()

    def _apply_accessibility_settings(self) -> None:
        instance = QApplication.instance()
        if instance is None:
            return
        app = cast(QApplication, instance)
        font = app.font()
        base_size = float(app.property("basePointSize") or font.pointSizeF() or 12.0)
        app.setProperty("basePointSize", base_size)
        font.setPointSizeF(base_size * float(self._settings.get("ui_scale", 1.0) or 1.0))
        app.setFont(font)
        high_contrast = bool(self._settings.get("high_contrast", False))
        app.setProperty("highContrast", high_contrast)
        app.setPalette(accessibility_palette(high_contrast))

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
        if not self._confirm_discard_if_dirty():
            event.ignore()
            return
        # Stop timers before children destroyed to avoid late callbacks.
        self._task_controller.shutdown()
        settings_bus.changed.disconnect(self._on_external_setting_changed)
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
