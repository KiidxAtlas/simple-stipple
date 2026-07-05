"""Runtime orchestration for declarative page composition."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, cast

from PySide6.QtWidgets import QTabWidget, QWidget

from src.ui.shell.registry import PageSpec


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
            if all(
                hasattr(page, method)
                for method in ("get_preset_state", "apply_preset_state")
            ):
                items.append((page_id, cast(PresetPage, page)))
        return items

    def has_workspace_content(self) -> bool:
        for spec in self._specs:
            page = self.get(spec.page_id)
            if page is None:
                continue
            for canvas_attr in spec.content_canvas_attrs:
                canvas = getattr(page, canvas_attr, None)
                if canvas is not None and bool(getattr(canvas, "poly_count", 0)):
                    return True
        return False

    def apply_settings(self, settings: dict) -> None:
        self._settings = settings
        for page in self._page_by_id.values():
            page_any = cast(Any, page)
            page_any._settings = settings

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
