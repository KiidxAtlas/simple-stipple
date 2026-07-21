"""Runtime orchestration for declarative page composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from PySide6.QtWidgets import QTabWidget, QWidget

from src.ui.pages.convert import ConvertPage
from src.ui.pages.draft import DraftPage
from src.ui.pages.pattern.tab import PatternPage
from src.ui.pages.trace.tab import TracePage

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
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_unit = getattr(canvas, "set_unit_system", None)
                if callable(set_unit):
                    set_unit(unit)

    def apply_smoothing_method(self, method: str) -> None:
        """Push the chosen path-smoothing algorithm to every page's canvas(es)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_method = getattr(canvas, "set_smoothing_method", None)
                if callable(set_method):
                    set_method(method)

    def connect_smoothing_method_changed(self, slot: Callable[..., Any]) -> None:
        """Connect every page's canvas(es) smoothingMethodChanged signal to
        ``slot`` — used to persist a sidebar-driven method change and echo
        it to every other tab's sidebar/Settings state."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                signal = getattr(canvas, "smoothingMethodChanged", None)
                if signal is not None:
                    signal.connect(slot)

    def apply_smooth_iterations(self, iterations: int) -> None:
        """Push the remembered Smooth-prompt iteration count to every
        page's canvas(es)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_it = getattr(canvas, "set_smooth_iterations", None)
                if callable(set_it):
                    set_it(iterations)

    def connect_smooth_iterations_changed(self, slot: Callable[..., Any]) -> None:
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                signal = getattr(canvas, "smoothIterationsChanged", None)
                if signal is not None:
                    signal.connect(slot)

    def apply_simplify_tolerance(self, tolerance: float) -> None:
        """Push the remembered Simplify-prompt tolerance to every page's
        canvas(es)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_tol = getattr(canvas, "set_simplify_tolerance", None)
                if callable(set_tol):
                    set_tol(tolerance)

    def connect_simplify_tolerance_changed(self, slot: Callable[..., Any]) -> None:
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                signal = getattr(canvas, "simplifyToleranceChanged", None)
                if signal is not None:
                    signal.connect(slot)

    def apply_radial_menu_tools(self, tools: list[str]) -> None:
        """Push the customized radial ("Q") menu wedge list to every page's
        canvas(es) that support it (only DxfCanvas does)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_tools = getattr(canvas, "set_radial_menu_tools", None)
                if callable(set_tools):
                    set_tools(tools)

    def apply_draw_sidebar_width(self, width: int) -> None:
        """Push the draw sidebar's width to every page's canvas(es), so
        resizing it on one tab keeps every tab's sidebar consistent."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_width = getattr(canvas, "set_draw_sidebar_width", None)
                if callable(set_width):
                    set_width(width)

    def connect_draw_sidebar_width_changed(self, slot: Callable[..., Any]) -> None:
        """Connect every page's canvas(es) drawSidebarWidthChanged signal to
        ``slot`` — used to persist a live sidebar-resize drag and echo it to
        every other tab's sidebar."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                signal = getattr(canvas, "drawSidebarWidthChanged", None)
                if signal is not None:
                    signal.connect(slot)

    def apply_draw_sidebar_height(self, height: int | None) -> None:
        """Push the draw sidebar's height to every page's canvas(es), so
        resizing it on one tab keeps every tab's sidebar consistent."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_height = getattr(canvas, "set_draw_sidebar_height", None)
                if callable(set_height):
                    set_height(height)

    def connect_draw_sidebar_height_changed(self, slot: Callable[..., Any]) -> None:
        """Connect every page's canvas(es) drawSidebarHeightChanged signal
        to ``slot`` — used to persist a live sidebar-resize drag and echo
        it to every other tab's sidebar."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                signal = getattr(canvas, "drawSidebarHeightChanged", None)
                if signal is not None:
                    signal.connect(slot)

    def apply_draw_sidebar_sections(self, sections: list[str]) -> None:
        """Push the customized draw-sidebar section list to every page's
        canvas(es)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_sections = getattr(canvas, "set_draw_sidebar_sections", None)
                if callable(set_sections):
                    set_sections(sections)

    def apply_draw_sidebar_path_tools(self, tools: list[str]) -> None:
        """Push the customized Path tool icon list (which show, in what
        order) to every page's canvas(es)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_tools = getattr(canvas, "set_draw_sidebar_path_tools", None)
                if callable(set_tools):
                    set_tools(tools)

    def apply_draw_sidebar_shape_tools(self, tools: list[str]) -> None:
        """Push the customized Shapes tool icon list (which show, in what
        order) to every page's canvas(es)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_tools = getattr(canvas, "set_draw_sidebar_shape_tools", None)
                if callable(set_tools):
                    set_tools(tools)

    def apply_draw_sidebar_always_visible(self, enabled: bool) -> None:
        """Push the "always visible" draw sidebar setting to every page's
        canvas(es)."""
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                set_always = getattr(canvas, "set_draw_sidebar_always_visible", None)
                if callable(set_always):
                    set_always(enabled)
