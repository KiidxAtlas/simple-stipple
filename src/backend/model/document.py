"""Workspace document model: Pydantic schema + persistence.

- Pydantic models (``WorkspaceDocument`` and friends) — schema-validated,
  typed models for all workspace state persisted to and loaded from JSON.
  Every model has ``to_dict()`` and ``model_validate()`` (Pydantic v2's
  from_dict equivalent) for round-tripping to/from the raw dict format the
  UI and file I/O expect. The high-severity issue this addresses: prior to
  this module, workspace state was a large nested ``dict`` persisted as
  JSON with no schema — a typo'd or missing key failed silently or with a
  bare ``KeyError`` deep in a page's ``apply_workspace_state``. These models
  catch missing/malformed keys at the boundary (load/save) instead.
- Dict-based API (``empty_workspace_document``, ``validate_workspace_document``,
  ``build_workspace_document``) for existing UI code, delegating to the
  Pydantic models internally.

Only the current schema version is supported — a workspace file saved by
an older app version does not migrate forward; it is rejected in favor of
a fresh empty document.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from src.backend.cad.constraints import GeometricConstraint

Point = tuple[float, float]
EntityId = str


def new_entity_id() -> EntityId:
    return uuid4().hex


@dataclass
class EntityRecord:
    """Runtime domain entity with stable identity and editor attributes."""

    points: list[Point]
    id: EntityId = field(default_factory=new_entity_id)
    kind: str = "polyline"
    meta: dict[str, Any] | None = None
    construction: bool = False
    hidden: bool = False
    locked: bool = False
    group: int | None = None
    layer: str | None = None


@dataclass
class Document:
    """Canonical runtime aggregate for entities, selection, layers, and groups."""

    entities: list[EntityRecord] = field(default_factory=list)
    selection: set[int] = field(default_factory=set)
    layer_order: list[str] = field(default_factory=list)
    active_layer: str | None = None
    layer_colors: dict[str, str] = field(default_factory=dict)
    group_labels: dict[int, str] = field(default_factory=dict)
    next_group_id: int = 0
    constraints: list[GeometricConstraint] = field(default_factory=list)

    def replace(self, entities: Iterable[EntityRecord]) -> None:
        self.entities = list(entities)
        self.selection.clear()
        self.ensure_unique_ids()

    def append(self, entity: EntityRecord) -> int:
        if self.index_for_id(entity.id) is not None:
            entity.id = new_entity_id()
        self.entities.append(entity)
        return len(self.entities) - 1

    def ensure_unique_ids(self) -> None:
        seen: set[EntityId] = set()
        for entity in self.entities:
            if not entity.id or entity.id in seen:
                entity.id = new_entity_id()
            seen.add(entity.id)

    def index_for_id(self, entity_id: EntityId) -> int | None:
        return next(
            (index for index, entity in enumerate(self.entities) if entity.id == entity_id), None
        )

    def entity_for_id(self, entity_id: EntityId) -> EntityRecord | None:
        index = self.index_for_id(entity_id)
        return self.entities[index] if index is not None else None

    def selected_ids(self) -> set[EntityId]:
        return {
            self.entities[index].id for index in self.selection if 0 <= index < len(self.entities)
        }

    def select_ids(self, entity_ids: Iterable[EntityId]) -> None:
        wanted = set(entity_ids)
        self.selection = {
            index for index, entity in enumerate(self.entities) if entity.id in wanted
        }

    def flagged_indices(self, attribute: str) -> set[int]:
        return {
            index
            for index, entity in enumerate(self.entities)
            if bool(getattr(entity, attribute, False))
        }

    def set_flagged_indices(self, attribute: str, indices: Iterable[int]) -> None:
        wanted = {index for index in indices if isinstance(index, int)}
        for index, entity in enumerate(self.entities):
            setattr(entity, attribute, index in wanted)

    def on_active_layer(self, entity: EntityRecord) -> bool:
        return (
            self.active_layer is None or entity.layer is None or entity.layer == self.active_layer
        )

    def entity_selectable(self, index: int) -> bool:
        if not 0 <= index < len(self.entities):
            return False
        entity = self.entities[index]
        return not entity.hidden and self.on_active_layer(entity)

    def drop_inactive_selection(self) -> bool:
        selection = {index for index in self.selection if self.entity_selectable(index)}
        changed = selection != self.selection
        self.selection = selection
        return changed

    def reconcile_groups(self) -> None:
        """Keep group allocation and labels consistent with current entities."""
        counts: dict[int, int] = {}
        for entity in self.entities:
            if entity.group is not None:
                counts[entity.group] = counts.get(entity.group, 0) + 1
        for entity in self.entities:
            if entity.group is not None and counts[entity.group] < 2:
                entity.group = None
        groups = {group for group, count in counts.items() if count >= 2}
        self.group_labels = {
            group: label for group, label in self.group_labels.items() if group in groups
        }
        self.next_group_id = max(self.next_group_id, max(groups, default=-1) + 1)


CanvasDocument = Document


@dataclass(frozen=True)
class OperationResult:
    """Outcome of a document-changing operation using stable entity IDs."""

    changed: bool
    message: str = ""
    created_ids: tuple[str, ...] = ()
    removed_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def unchanged(cls, message: str, *warnings: str) -> OperationResult:
        return cls(False, message=message, warnings=tuple(warnings))


# ══════════════════════════════════════════════════════════════════════════
# Pydantic models — schema-validated workspace state
# ══════════════════════════════════════════════════════════════════════════

# ── Workspace-level models ────────────────────────────────────────────────


class AppWorkspaceState(BaseModel):
    """Per-workspace app state: which tab is active."""

    current_tab: int = Field(default=0, ge=0)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppWorkspaceState:
        return cls.model_validate(data)


class PresetState(BaseModel):
    """Per-type preset storage (shape presets, pattern presets)."""

    shape: dict[str, Any] = Field(default_factory=dict)
    pattern: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PresetState:
        return cls.model_validate(data)


class MetaState(BaseModel):
    """Arbitrary metadata attached to a workspace."""

    data: dict[str, Any] = Field(default_factory=dict, alias="data")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetaState:
        return cls.model_validate(data)


# ── Per-page tab state models (each page defines its own) ─────────────────────


class TabStateBase(BaseModel):
    """Base for all per-page tab states. Subclasses define their own fields."""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, by_alias=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TabStateBase:
        return cls.model_validate(data)


class UtilitiesTabState(TabStateBase):
    """Placeholder for the utilities tab (currently no persisted state)."""

    pass


class PatternTabState(TabStateBase):
    """Workspace state for PatternPage.

    Mirrors the keys read by ``get_pattern_workspace_state()`` and written by
    ``apply_pattern_workspace_state()`` in ``src/ui/pages/pattern/session.py``.
    """

    dxf_path: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    orig_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    edit_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    outline_ids: list[str] = Field(default_factory=list)
    outline_roles: dict[str, str] = Field(default_factory=dict)
    pattern_cell_cutouts: list[list[tuple[float, float]]] = Field(default_factory=list)
    orig_w: float = 0.0
    orig_h: float = 0.0
    canvas_view: dict[str, Any] = Field(default_factory=dict)
    preview_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    showing_preview: bool = False
    zones: list[dict[str, Any]] = Field(default_factory=list)
    exclusion_ids: list[str] = Field(default_factory=list)
    custom_tile_polys: list[list[tuple[float, float]]] = Field(default_factory=list)


class ShapeTabState(TabStateBase):
    """Workspace state for the Shape page (currently minimal)."""

    canvas_view: dict[str, Any] = Field(default_factory=dict)


class ImageTabState(TabStateBase):
    """Workspace state for the Image/Trace page (uses TraceTabState)."""

    canvas_view: dict[str, Any] = Field(default_factory=dict)


class TraceTabState(TabStateBase):
    """Workspace state for TracePage.

    Mirrors the keys read by ``get_trace_workspace_state()`` and written by
    ``apply_trace_workspace_state()`` in ``src/ui/pages/trace/session.py``.
    """

    image_path: str = ""
    blur: str = ""
    threshold: str = ""
    auto_threshold: bool = True
    invert: bool = False
    edge_mode: bool = False
    canny_low: str = ""
    canny_high: str = ""
    outer_only: bool = False
    simplify: str = ""
    min_area: str = ""
    max_area: str = ""
    close_r: str = ""
    width_mm: str = ""
    height_mm: str = ""
    max_res: str = ""
    aspect_locked: bool = True
    bg_visible: bool = True
    img_w_px: int = 0
    img_h_px: int = 0
    img_aspect: float = 1.0
    last_width_mm: float = 0.0
    last_height_mm: float = 0.0
    canvas_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    canvas_view: dict[str, Any] = Field(default_factory=dict)


class DraftTabState(TabStateBase):
    """Workspace state for DraftPage (multi-layer canvas).

    Mirrors the keys read by ``get_draft_workspace_state()`` and written by
    ``apply_draft_workspace_state()`` in ``src/ui/pages/draft/session.py``.
    """

    entities: list[dict[str, Any]] = Field(default_factory=list)
    layer_order: list[str] = Field(default_factory=list)
    active_layer: str = ""
    canvas_view: dict[str, Any] = Field(default_factory=dict)
    quick_shape_mode: str = ""
    quick_shape_enabled: bool = False
    last_input_dxf: str = ""


# ── Tab registry (maps tab name -> model class) ──────────────────────────────

TAB_STATE_MAP: dict[str, type[TabStateBase]] = {
    "utilities": UtilitiesTabState,
    "pattern": PatternTabState,
    "shape": ShapeTabState,
    "image": ImageTabState,
}

# Draft is special — it's not in the default tab list but is used when a
# multi-layer canvas is active; the tab name is "draft" in some paths.
TAB_STATE_MAP["draft"] = DraftTabState


# ── Full workspace document model ─────────────────────────────────────────────


class WorkspaceDocument(BaseModel):
    """Top-level validated workspace document.

    This is the schema-validated replacement for the raw ``dict`` that was
    previously persisted as JSON with no validation. All fields are required
    at the schema level; missing keys from old files are filled with defaults
    during migration (see ``validate_workspace_document`` below).
    """

    schema_version: int = Field(default=3, ge=1)
    workspace_name: str = Field(default="Untitled Workspace", min_length=1)
    app: AppWorkspaceState
    # Concrete tab-state subclasses carry different fields. Keeping the
    # values as Any preserves the subclass instances installed by the
    # pre-validator instead of re-validating them as TabStateBase and
    # silently discarding page-specific data.
    tabs: dict[str, Any]
    presets: PresetState
    meta: MetaState

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the raw dict format expected by file I/O."""
        return {
            "schema_version": self.schema_version,
            "workspace_name": self.workspace_name,
            "app": self.app.to_dict(),
            "tabs": {name: tab.to_dict() for name, tab in self.tabs.items()},
            "presets": self.presets.to_dict(),
            "meta": self.meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceDocument:
        """Validate and construct from a raw dict (e.g. loaded from disk).

        This is the boundary where malformed or incomplete data is caught —
        if a required field is missing or has the wrong type, Pydantic raises
        a ``ValidationError`` here rather than letting it propagate silently
        through page session code.
        """
        return cls.model_validate(data)

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        """Pre-validation coercion: ensure nested dicts are dicts, not strings."""
        if not isinstance(data, dict):
            raise ValueError("Workspace document must be a JSON object.")
        # Never mutate the caller's document while coercing nested models.
        # Validation may still fail on another field; the permissive fallback
        # relies on the original raw dictionaries remaining intact.
        data = dict(data)
        # Ensure app is a dict (not None or missing).
        if "app" not in data or not isinstance(data.get("app"), dict):
            data = {**data, "app": {"current_tab": 0}}
        # Ensure tabs is a dict with string keys.
        if "tabs" not in data or not isinstance(data.get("tabs"), dict):
            data = {**data, "tabs": {}}
        # Ensure presets is a dict.
        if "presets" not in data or not isinstance(data.get("presets"), dict):
            data = {**data, "presets": {"shape": {}, "pattern": {}}}
        # Ensure meta is a dict.
        if "meta" not in data or not isinstance(data.get("meta"), dict):
            data = {**data, "meta": {}}
        # Coerce each tab's value to the correct model class.
        tabs: dict[str, Any] = dict(data.get("tabs", {}))
        coerced_tabs: dict[str, Any] = {}
        for name, tab_data in tabs.items():
            if isinstance(tab_data, TabStateBase):
                coerced_tabs[name] = tab_data
                continue
            if not isinstance(tab_data, dict):
                tab_data = {}
            tab_model = TAB_STATE_MAP.get(name)
            if tab_model is not None:
                try:
                    coerced_tabs[name] = tab_model.from_dict(tab_data)
                except Exception:
                    # If a tab's data is malformed, store a minimal valid
                    # instance so the rest of the document can still load.
                    coerced_tabs[name] = tab_model()
            else:
                # Unknown tab — store as a generic dict-backed TabStateBase.
                coerced_tabs[name] = TabStateBase.model_validate(tab_data)
        data["tabs"] = coerced_tabs
        # Coerce presets.
        if isinstance(data.get("presets"), PresetState):
            pass
        elif isinstance(data.get("presets"), dict):
            data["presets"] = PresetState.from_dict(data["presets"])
        else:
            data["presets"] = PresetState()
        # Coerce meta.
        if isinstance(data.get("meta"), MetaState):
            pass
        elif isinstance(data.get("meta"), dict):
            data["meta"] = MetaState.from_dict(data["meta"])
        else:
            data["meta"] = MetaState()
        # Coerce app.
        if isinstance(data.get("app"), AppWorkspaceState):
            pass
        elif isinstance(data.get("app"), dict):
            data["app"] = AppWorkspaceState.from_dict(data["app"])
        else:
            data["app"] = AppWorkspaceState()
        return data


# ══════════════════════════════════════════════════════════════════════════
# Dict-based API — delegates to the Pydantic models above
# ══════════════════════════════════════════════════════════════════════════

WORKSPACE_SCHEMA_VERSION = 3
WORKSPACE_FILE_SUFFIX = ".simple-stipple-project.json"


def empty_workspace_document() -> dict[str, Any]:
    """Return a new default workspace document in the current schema version."""
    doc = WorkspaceDocument(
        schema_version=WORKSPACE_SCHEMA_VERSION,
        workspace_name="Untitled Workspace",
        app=AppWorkspaceState(current_tab=0),
        tabs={
            "utilities": UtilitiesTabState(),
            "pattern": PatternTabState(),
            "shape": ShapeTabState(),
            "image": ImageTabState(),
        },
        presets=PresetState(),
        meta=MetaState(),
    )
    return doc.to_dict()


def validate_workspace_document(document: dict[str, Any]) -> dict[str, Any]:
    """Validate a workspace document into normalized in-memory form.

    Only the current schema version is accepted — a file saved by an older
    app version is rejected rather than migrated forward.

    If the Pydantic model construction fails (e.g. a tab's data is
    completely malformed), a minimal valid document is returned instead of
    crashing — the worst case is that one page's state is lost, but the
    rest of the workspace remains intact.
    """
    if not isinstance(document, dict):
        raise ValueError("Workspace file must contain a JSON object.")
    version = int(document.get("schema_version", 0))
    if version < 1 or version > WORKSPACE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported workspace schema version: {version}. "
            f"Supported versions are 1–{WORKSPACE_SCHEMA_VERSION}."
        )
    normalized = deepcopy(document)
    # Versions 1 and 2 used the same page dictionaries but did not always
    # nest the active tab under ``app``. Defaults on the current models fill
    # fields introduced since then without discarding unknown future fields.
    if version < 3 and "app" not in normalized:
        normalized["app"] = {"current_tab": normalized.pop("current_tab", 0)}
    normalized["schema_version"] = WORKSPACE_SCHEMA_VERSION
    name = normalized.get("workspace_name", "Untitled Workspace")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Workspace name must be a non-empty string.")
    normalized["workspace_name"] = name

    app = normalized.get("app", {"current_tab": 0})
    if not isinstance(app, dict):
        raise ValueError("Workspace app state must be an object.")
    normalized["app"] = {**app, **AppWorkspaceState.from_dict(app).to_dict()}

    tabs = normalized.get("tabs", {})
    if not isinstance(tabs, dict):
        raise ValueError("Workspace tabs state must be an object.")
    validated_tabs: dict[str, Any] = {}
    for name, state in tabs.items():
        if not isinstance(name, str) or not isinstance(state, dict):
            raise ValueError(f"Workspace tab {name!r} must contain an object.")
        model = TAB_STATE_MAP.get(name)
        validated_tabs[name] = (
            {**state, **model.from_dict(state).to_dict()} if model is not None else deepcopy(state)
        )
    for name, default in empty_workspace_document()["tabs"].items():
        validated_tabs.setdefault(name, default)
    normalized["tabs"] = validated_tabs

    for key in ("presets", "meta"):
        value = normalized.get(key, {})
        if not isinstance(value, dict):
            raise ValueError(f"Workspace {key} state must be an object.")
        normalized[key] = value
    return normalized


def build_workspace_document(
    workspace_name: str,
    app_state: dict[str, Any],
    tab_states: dict[str, Any],
    preset_state: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a persisted workspace document from already-validated live state.

    Page session models validate their own state at their boundaries. Avoid a
    second polymorphic Pydantic round-trip here: coercing every concrete tab
    through the shared base model can discard page-specific fields.
    """
    document = empty_workspace_document()
    document["workspace_name"] = workspace_name or document["workspace_name"]
    document["app"] = deepcopy(app_state)
    document["tabs"] = deepcopy(tab_states)
    document["presets"] = deepcopy(preset_state)
    document["meta"] = deepcopy(meta or {})
    return document


def normalize_workspace_path(path: str | Path) -> Path:
    """Normalize a workspace path to use the canonical workspace file suffix."""
    file_path = Path(path)
    if str(file_path).endswith(WORKSPACE_FILE_SUFFIX):
        return file_path
    # Strip any existing extension(s) and append the canonical suffix
    stem = file_path.stem
    if file_path.suffix:
        stem = file_path.with_suffix("").stem
    return file_path.with_name(stem + WORKSPACE_FILE_SUFFIX)
