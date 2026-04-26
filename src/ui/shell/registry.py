"""Declarative page registry for the application shell."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from src.ui.pages.convert.tab import ConvertPage
from src.ui.pages.draft.tab import DraftPage
from src.ui.pages.pattern.tab import PatternPage
from src.ui.pages.repo.tab import RepoPage
from src.ui.pages.trace.tab import TracePage

PageFactory = Callable[[dict], QWidget]


@dataclass(frozen=True)
class PageSpec:
    """Registration metadata for an app page."""

    page_id: str
    title: str
    shortcut_id: str
    command_title: str
    command_keywords: str
    factory: PageFactory
    content_canvas_attrs: tuple[str, ...] = ()


def _build_draft_page(settings: dict) -> QWidget:
    return DraftPage(settings=settings)


def _build_pattern_page(settings: dict) -> QWidget:
    return PatternPage(settings=settings)


def _build_trace_page(settings: dict) -> QWidget:
    return TracePage(settings=settings)


def _build_convert_page(settings: dict) -> QWidget:
    return ConvertPage(settings=settings)


def _build_repo_page(settings: dict) -> QWidget:
    return RepoPage(settings=settings)


def default_page_specs() -> tuple[PageSpec, ...]:
    """Return default page registrations in UI order."""
    return (
        PageSpec(
            page_id="draft",
            title="Draft",
            shortcut_id="tab.draft",
            command_title="Page: Draft",
            command_keywords="page draft",
            factory=_build_draft_page,
            content_canvas_attrs=("_canvas",),
        ),
        PageSpec(
            page_id="pattern",
            title="Pattern Fill",
            shortcut_id="tab.pattern",
            command_title="Page: Pattern Fill",
            command_keywords="page pattern",
            factory=_build_pattern_page,
            content_canvas_attrs=("_canvas",),
        ),
        PageSpec(
            page_id="trace",
            title="Trace",
            shortcut_id="tab.trace",
            command_title="Page: Trace",
            command_keywords="page trace",
            factory=_build_trace_page,
            content_canvas_attrs=("_canvas",),
        ),
        PageSpec(
            page_id="convert",
            title="Convert",
            shortcut_id="tab.convert",
            command_title="Page: Convert",
            command_keywords="page convert utilities",
            factory=_build_convert_page,
            content_canvas_attrs=("_preview_canvas",),
        ),
        PageSpec(
            page_id="repo",
            title="Repo",
            shortcut_id="tab.repo",
            command_title="Page: Repo",
            command_keywords="page repo git",
            factory=_build_repo_page,
        ),
    )
