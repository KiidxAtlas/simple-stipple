"""Workspace document/session helpers extracted from the app shell."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from src.backend.document import (
    WORKSPACE_FILE_SUFFIX,
    build_workspace_document,
    validate_workspace_document,
)


def workspace_default_dir(settings: dict) -> str:
    return settings.get(
        "workspace_dir",
        settings.get("last_workspace_dir", str(Path.home())),
    )


def collect_workspace_document(
    *,
    workspace_path: Path | None,
    current_tab_index: int,
    workspace_pages: Sequence[tuple[str, Any]],
    preset_pages: Sequence[tuple[str, Any]],
) -> dict:
    workspace_name = (
        workspace_path.stem.replace(WORKSPACE_FILE_SUFFIX.replace(".json", ""), "")
        if workspace_path
        else "Untitled Workspace"
    )
    return build_workspace_document(
        workspace_name=workspace_name,
        app_state={"current_tab": current_tab_index},
        tab_states={page_id: page.get_workspace_state() for page_id, page in workspace_pages},
        preset_state={page_id: page.get_preset_state() for page_id, page in preset_pages},
        meta={"workspace_path": str(workspace_path) if workspace_path else ""},
    )


def apply_workspace_document(
    *,
    document: dict,
    workspace_pages: Sequence[tuple[str, Any]],
    preset_pages: Sequence[tuple[str, Any]],
    tab_count: int,
    set_current_tab_index: Callable[[int], None],
) -> None:
    data = validate_workspace_document(document)
    presets = data.get("presets", {})
    for page_id, page in preset_pages:
        page.apply_preset_state(presets.get(page_id, {}))

    tabs = data.get("tabs", {})
    for page_id, page in workspace_pages:
        page.apply_workspace_state(tabs.get(page_id, {}))

    idx = int(data.get("app", {}).get("current_tab", 0))
    set_current_tab_index(max(0, min(idx, tab_count - 1)))


def clear_workspace_state(
    *,
    workspace_pages: Sequence[tuple[str, Any]],
    set_current_tab_index: Callable[[int], None],
) -> None:
    for _, page in workspace_pages:
        page.clear_workspace_state()
    set_current_tab_index(0)


def remember_workspace_path(
    *,
    settings: dict,
    path: Path,
    max_recent: int = 8,
) -> None:
    settings["last_workspace_dir"] = str(path.parent)
    settings["current_workspace"] = str(path)
    recent = [p for p in settings.get("recent_workspaces", []) if p != str(path)]
    recent.insert(0, str(path))
    settings["recent_workspaces"] = recent[:max_recent]


def recent_workspace_paths(settings: dict) -> list[Path]:
    return [Path(path) for path in settings.get("recent_workspaces", []) if Path(path).exists()]


def workspace_title(workspace_path: Path | None, workspace_dirty: bool) -> str:
    name = workspace_path.name if workspace_path else "Untitled Workspace"
    dirty = " *" if workspace_dirty else ""
    return f"AA Laser Studio — {name}{dirty}"
