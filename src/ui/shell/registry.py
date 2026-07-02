"""Declarative page registry for the application shell."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from src.ui.pages.convert.tab import ConvertPage
from src.ui.pages.draft.tab import DraftPage
from src.ui.pages.pattern.tab import PatternPage
from src.ui.pages.trace.tab import TracePage

PageFactory = Callable[[dict], QWidget]


@dataclass(frozen=True)
class PageSpec:
    """Registration metadata for an app page."""

    page_id: str
    title: str
    command_keywords: str
    factory: PageFactory
    content_canvas_attrs: tuple[str, ...] = ()

    @property
    def shortcut_id(self) -> str:
        """Keyboard shortcut action id, derived from page_id."""
        return f"tab.{self.page_id}"

    @property
    def command_title(self) -> str:
        """Command palette title, derived from title."""
        return f"Page: {self.title}"


def _build_draft_page(settings: dict) -> QWidget:
    return DraftPage(settings=settings)


def _build_pattern_page(settings: dict) -> QWidget:
    return PatternPage(settings=settings)


def _build_trace_page(settings: dict) -> QWidget:
    return TracePage(settings=settings)


def _build_convert_page(settings: dict) -> QWidget:
    return ConvertPage(settings=settings)


def default_page_specs() -> tuple[PageSpec, ...]:
    """Return default page registrations in UI order."""
    return (
        PageSpec(
            page_id="draft",
            title="Draft",
            command_keywords="page draft",
            factory=_build_draft_page,
            content_canvas_attrs=("_canvas",),
        ),
        PageSpec(
            page_id="pattern",
            title="Pattern Fill",
            command_keywords="page pattern",
            factory=_build_pattern_page,
            content_canvas_attrs=("_canvas",),
        ),
        PageSpec(
            page_id="trace",
            title="Trace",
            command_keywords="page trace",
            factory=_build_trace_page,
            content_canvas_attrs=("_canvas",),
        ),
        PageSpec(
            page_id="convert",
            title="Convert",
            command_keywords="page convert utilities",
            factory=_build_convert_page,
            content_canvas_attrs=("_preview_canvas",),
        ),
    )
