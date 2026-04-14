"""Workspace document helpers for Simple Stipple."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

WORKSPACE_SCHEMA_VERSION = 3
WORKSPACE_FILE_SUFFIX = ".simple-stipple-project.json"


def _migrate_v1_to_v2(document: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(document)
    tabs = migrated.get("tabs")
    if not isinstance(tabs, dict):
        tabs = {}
    tabs.setdefault("sketch", {})
    migrated["tabs"] = tabs
    migrated["schema_version"] = 2
    return migrated


def _migrate_v2_to_v3(document: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(document)
    tabs = migrated.get("tabs")
    if not isinstance(tabs, dict):
        tabs = {}
    tabs.pop("sketch", None)
    migrated["tabs"] = tabs
    migrated["schema_version"] = WORKSPACE_SCHEMA_VERSION
    return migrated


def empty_workspace_document() -> dict[str, Any]:
    return {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "workspace_name": "Untitled Workspace",
        "app": {
            "current_tab": 0,
        },
        "tabs": {
            "utilities": {},
            "pattern": {},
            "shape": {},
            "image": {},
        },
        "presets": {
            "shape": {},
            "pattern": {},
        },
        "meta": {},
    }


def validate_workspace_document(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("Workspace file must contain a JSON object.")
    version = int(document.get("schema_version", 0))
    if version == 1:
        document = _migrate_v1_to_v2(document)
        version = 2
    if version == 2:
        document = _migrate_v2_to_v3(document)
        version = WORKSPACE_SCHEMA_VERSION

    if version != WORKSPACE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported workspace schema version: {version}. Expected {WORKSPACE_SCHEMA_VERSION}."
        )
    result = empty_workspace_document()
    result.update({k: deepcopy(v) for k, v in document.items() if k in result})
    if not isinstance(result.get("app"), dict):
        result["app"] = {"current_tab": 0}
    if not isinstance(result.get("tabs"), dict):
        result["tabs"] = empty_workspace_document()["tabs"]
    else:
        default_tabs = empty_workspace_document()["tabs"]
        for key, value in default_tabs.items():
            if key not in result["tabs"] or not isinstance(
                result["tabs"].get(key), dict
            ):
                result["tabs"][key] = deepcopy(value)
    if not isinstance(result.get("presets"), dict):
        result["presets"] = empty_workspace_document()["presets"]
    if not isinstance(result.get("meta"), dict):
        result["meta"] = {}
    return result


def build_workspace_document(
    workspace_name: str,
    app_state: dict[str, Any],
    tab_states: dict[str, Any],
    preset_state: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = empty_workspace_document()
    document["workspace_name"] = workspace_name or document["workspace_name"]
    document["app"] = deepcopy(app_state)
    document["tabs"] = deepcopy(tab_states)
    document["presets"] = deepcopy(preset_state)
    document["meta"] = deepcopy(meta or {})
    return validate_workspace_document(document)


def normalize_workspace_path(path: str | Path) -> Path:
    file_path = Path(path)
    if str(file_path).endswith(WORKSPACE_FILE_SUFFIX):
        return file_path
    # Strip any existing extension(s) and append the canonical suffix
    stem = file_path.stem
    if file_path.suffix:
        stem = file_path.with_suffix("").stem
    return file_path.with_name(stem + WORKSPACE_FILE_SUFFIX)
