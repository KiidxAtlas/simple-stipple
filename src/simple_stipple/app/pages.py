"""Runtime orchestration for declarative page composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from PySide6.QtWidgets import QTabWidget, QWidget

from simple_stipple.features.convert import ConvertPage
from simple_stipple.features.draft import DraftPage
from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.features.trace.page import TracePage

PageFactory = Callable[[dict], QWidget]


@dataclass(frozen=True)
class PageSpec:
    """Registration metadata for one top-level application page."""

    page_id: str
    title: str
    command_keywords: str
    factory: PageFactory
    content_canvas_attrs: tuple[str, ...] = ()

    @property
    def shortcut_id(self) -> str:
        return f"tab.{self.page_id}"

    @property
    def command_title(self) -> str:
        return f"Page: {self.title}"


def default_page_specs() -> tuple[PageSpec, ...]:
    """Return the declarative page registrations in UI order."""
    return (
        PageSpec(
            "draft",
            "Draft",
            "page draft",
            lambda settings: DraftPage(settings=settings),
            ("_canvas",),
        ),
        PageSpec(
            "pattern",
            "Pattern",
            "page pattern fill",
            lambda settings: PatternPage(settings=settings),
            ("_canvas",),
        ),
        PageSpec(
            "trace",
            "Trace",
            "page trace",
            lambda settings: TracePage(settings=settings),
            ("_canvas",),
        ),
        PageSpec(
            "convert",
            "Convert",
            "page convert utilities",
            lambda settings: ConvertPage(settings=settings),
            ("_preview_canvas",),
        ),
    )


@dataclass(frozen=True)
class SettingSync:
    key: str
    setter: str
    signal: str | None = None


SETTINGS_SYNC_TABLE: tuple[SettingSync, ...] = (
    SettingSync("unit_system", "set_unit_system"),
    SettingSync("grid_visible", "set_grid_visible"),
    SettingSync("grid_snap", "set_grid_snap"),
    SettingSync("grid_spacing", "set_grid_spacing"),
    SettingSync("snap_master", "set_snap_master"),
    SettingSync("snap_vertex", "set_snap_vertex"),
    SettingSync("snap_edge", "set_snap_edge"),
    SettingSync("snap_tangent", "set_snap_tangent"),
    SettingSync("snap_extension", "set_snap_extension"),
    SettingSync("snap_angle", "set_snap_angle"),
    SettingSync("snap_equal_length", "set_snap_equal_length"),
    SettingSync("snap_axis_alignment", "set_snap_axis_alignment"),
    SettingSync("construction_mode_default", "set_construction_mode"),
    SettingSync("aspect_ratio_locked_default", "set_aspect_ratio_locked"),
    SettingSync("geometry_health_visible", "set_geometry_health_visible"),
    SettingSync("curvature_visible", "set_curvature_visible"),
    SettingSync("rotation_snap_increment", "set_rotation_snap_increment"),
    SettingSync("smoothing_method", "set_smoothing_method", "smoothingMethodChanged"),
    SettingSync("smooth_iterations", "set_smooth_iterations", "smoothIterationsChanged"),
    SettingSync("simplify_tolerance", "set_simplify_tolerance", "simplifyToleranceChanged"),
    SettingSync("radial_menu_tools", "set_radial_menu_tools"),
    SettingSync("context_menu_sections", "set_context_menu_sections"),
    SettingSync("context_menu_overflow_sections", "set_context_menu_overflow_sections"),
    SettingSync("draw_sidebar_width", "set_draw_sidebar_width", "drawSidebarWidthChanged"),
    SettingSync("draw_sidebar_height", "set_draw_sidebar_height", "drawSidebarHeightChanged"),
    SettingSync("draw_sidebar_sections", "set_draw_sidebar_sections"),
    SettingSync("draw_sidebar_path_tools", "set_draw_sidebar_path_tools"),
    SettingSync("draw_sidebar_shape_tools", "set_draw_sidebar_shape_tools"),
    SettingSync("draw_sidebar_always_visible", "set_draw_sidebar_always_visible"),
)


class WorkspacePage(Protocol):
    def get_workspace_state(self) -> dict: ...

    def apply_workspace_state(self, state: dict | None) -> None: ...

    def clear_workspace_state(self) -> None: ...


class PresetPage(Protocol):
    def get_preset_state(self) -> dict: ...

    def apply_preset_state(self, state: dict | None) -> None: ...


class PageRuntime:
    """Manage page creation, discovery, and shared integration behavior."""

    def __init__(
        self,
        *,
        tab_widget: QTabWidget,
        settings: dict,
        specs: tuple[PageSpec, ...],
    ) -> None:
        self._page_widget = tab_widget
        self._settings = settings
        self._specs = specs
        self._page_by_id: dict[str, QWidget] = {}
        self._init_pages()

    def _init_pages(self) -> None:
        for spec in self._specs:
            page = spec.factory(self._settings)
            self._page_by_id[spec.page_id] = page
            self._page_widget.addTab(page, spec.title)

    def specs(self) -> tuple[PageSpec, ...]:
        return self._specs

    def get(self, page_id: str) -> QWidget | None:
        return self._page_by_id.get(page_id)

    def content_canvas_for(self, page: QWidget | None) -> Any | None:
        """First live content canvas declared for this page widget, if any."""
        if page is None:
            return None
        for spec in self._specs:
            if self._page_by_id.get(spec.page_id) is page:
                for canvas_attr in spec.content_canvas_attrs:
                    canvas = getattr(page, canvas_attr, None)
                    if canvas is not None:
                        return canvas
        return None

    def switch_to(self, page_id: str) -> None:
        page = self.get(page_id)
        if page is None:
            return
        self._page_widget.setCurrentWidget(page)

    def connect_state_changed(self, slot: Callable[..., Any]) -> None:
        for page in self._page_by_id.values():
            signal = getattr(page, "stateChanged", None)
            if signal is None:
                continue
            signal.connect(slot)

    def connect_signal_if_present(
        self,
        *,
        page_id: str,
        signal_name: str,
        slot: Callable[..., Any],
    ) -> None:
        page = self.get(page_id)
        if page is None:
            return
        signal = getattr(page, signal_name, None)
        if signal is None:
            return
        signal.connect(slot)

    def iter_workspace_pages(self) -> list[tuple[str, WorkspacePage]]:
        items: list[tuple[str, WorkspacePage]] = []
        for page_id, page in self._page_by_id.items():
            if all(
                hasattr(page, method)
                for method in (
                    "get_workspace_state",
                    "apply_workspace_state",
                    "clear_workspace_state",
                )
            ):
                items.append((page_id, cast(WorkspacePage, page)))
        return items

    def iter_preset_pages(self) -> list[tuple[str, PresetPage]]:
        items: list[tuple[str, PresetPage]] = []
        for page_id, page in self._page_by_id.items():
            if all(hasattr(page, method) for method in ("get_preset_state", "apply_preset_state")):
                items.append((page_id, cast(PresetPage, page)))
        return items

    def has_workspace_content(self) -> bool:
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            page_has_content = getattr(page, "has_workspace_content", None)
            if callable(page_has_content) and bool(page_has_content()):
                return True
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                if canvas is not None and bool(getattr(canvas, "poly_count", 0)):
                    return True
        return False

    def apply_settings(self, settings: dict) -> None:
        self._settings = settings
        for page in self._page_by_id.values():
            page_any = cast(Any, page)
            apply_page_settings = getattr(page, "apply_settings", None)
            if callable(apply_page_settings):
                apply_page_settings(settings)
            else:
                page_any._settings = settings

    def _canvases(self):
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                if canvas is not None:
                    yield canvas

    def _apply_to_canvases(self, setter_name: str, value: Any) -> None:
        """Call ``canvas.<setter_name>(value)`` on every registered canvas."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                setter = getattr(canvas, setter_name, None)
                if callable(setter):
                    setter(value)

    def _connect_signals_to(self, slot: Callable[..., Any], signal_name: str) -> None:
        """Connect ``canvas.<signal_name>`` to ``slot`` on every registered canvas."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                signal = getattr(canvas, signal_name, None)
                if signal is not None:
                    signal.connect(slot)

    def apply(self, key: str, value: Any) -> None:
        """Push one setting to every canvas that supports its declared setter."""
        sync = next((item for item in SETTINGS_SYNC_TABLE if item.key == key), None)
        if sync is None:
            raise KeyError(f"Unknown synchronized setting: {key}")
        for canvas in self._canvases():
            setter = getattr(canvas, sync.setter, None)
            if callable(setter):
                setter(value)

    def apply_all(self, settings: dict) -> None:
        self.apply_settings(settings)
        for sync in SETTINGS_SYNC_TABLE:
            if sync.key in settings:
                self.apply(sync.key, settings[sync.key])

    def connect_echoes(self, handler: Callable[[str, Any], None]) -> None:
        for sync in SETTINGS_SYNC_TABLE:
            if sync.signal is None:
                continue
            for canvas in self._canvases():
                signal = getattr(canvas, sync.signal, None)
                if signal is not None:
                    signal.connect(lambda value, key=sync.key: handler(key, value))

    def apply_unit_system(self, unit: str) -> None:
        """Push the active display-unit setting to every page's canvas(es)."""
        self._apply_to_canvases("set_unit_system", unit)

    def apply_smoothing_method(self, method: str) -> None:
        """Push the chosen path-smoothing algorithm to every page's canvas(es)."""
        self._apply_to_canvases("set_smoothing_method", method)

    def connect_smoothing_method_changed(self, slot: Callable[..., Any]) -> None:
        """Connect every page's canvas(es) smoothingMethodChanged signal to
        ``slot`` — used to persist a sidebar-driven method change and echo
        it to every other tab's sidebar/Settings state."""
        self._connect_signals_to(slot, "smoothingMethodChanged")

    def apply_smooth_iterations(self, iterations: int) -> None:
        """Push the remembered Smooth-prompt iteration count to every
        page's canvas(es)."""
        self._apply_to_canvases("set_smooth_iterations", iterations)

    def connect_smooth_iterations_changed(self, slot: Callable[..., Any]) -> None:
        self._connect_signals_to(slot, "smoothIterationsChanged")

    def apply_simplify_tolerance(self, tolerance: float) -> None:
        """Push the remembered Simplify-prompt tolerance to every page's
        canvas(es)."""
        self._apply_to_canvases("set_simplify_tolerance", tolerance)

    def connect_simplify_tolerance_changed(self, slot: Callable[..., Any]) -> None:
        self._connect_signals_to(slot, "simplifyToleranceChanged")

    def apply_radial_menu_tools(self, tools: list[str]) -> None:
        """Push the customized radial ("Q") menu wedge list to every page's
        canvas(es) that support it (only DxfCanvas does)."""
        self._apply_to_canvases("set_radial_menu_tools", tools)

    def apply_draw_sidebar_width(self, width: int) -> None:
        """Push the draw sidebar's width to every page's canvas(es), so
        resizing it on one tab keeps every tab's sidebar consistent."""
        self._apply_to_canvases("set_draw_sidebar_width", width)

    def connect_draw_sidebar_width_changed(self, slot: Callable[..., Any]) -> None:
        """Connect every page's canvas(es) drawSidebarWidthChanged signal to
        ``slot`` — used to persist a live sidebar-resize drag and echo it to
        every other tab's sidebar."""
        self._connect_signals_to(slot, "drawSidebarWidthChanged")

    def apply_draw_sidebar_height(self, height: int | None) -> None:
        """Push the draw sidebar's height to every page's canvas(es), so
        resizing it on one tab keeps every tab's sidebar consistent."""
        self._apply_to_canvases("set_draw_sidebar_height", height)

    def connect_draw_sidebar_height_changed(self, slot: Callable[..., Any]) -> None:
        """Connect every page's canvas(es) drawSidebarHeightChanged signal
        to ``slot`` — used to persist a live sidebar-resize drag and echo
        it to every other tab's sidebar."""
        self._connect_signals_to(slot, "drawSidebarHeightChanged")

    def apply_draw_sidebar_sections(self, sections: list[str]) -> None:
        """Push the customized draw-sidebar section list to every page's
        canvas(es)."""
        self._apply_to_canvases("set_draw_sidebar_sections", sections)

    def apply_draw_sidebar_path_tools(self, tools: list[str]) -> None:
        """Push the customized Path tool icon list (which show, in what
        order) to every page's canvas(es)."""
        self._apply_to_canvases("set_draw_sidebar_path_tools", tools)

    def apply_draw_sidebar_shape_tools(self, tools: list[str]) -> None:
        """Push the customized Shapes tool icon list (which show, in what
        order) to every page's canvas(es)."""
        self._apply_to_canvases("set_draw_sidebar_shape_tools", tools)

    def apply_draw_sidebar_always_visible(self, enabled: bool) -> None:
        """Push the "always visible" draw sidebar setting to every page's
        canvas(es)."""
        self._apply_to_canvases("set_draw_sidebar_always_visible", enabled)
