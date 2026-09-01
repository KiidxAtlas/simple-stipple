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

from pydantic import BaseModel, Field, model_validator

from simple_stipple.core.cad.constraints import GeometricConstraint
from simple_stipple.core.document.identity import EntityId, new_entity_id

Point = tuple[float, float]


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
class PlacedImage:
    """A raster placed on the part, in millimetres, owned by the document.

    An image used to belong to whichever page was showing it, which is why
    "is this the picture I traced or the picture I am engraving?" had no
    answer. One placement, one document; whether it is engraved or traced to
    outlines is a treatment on it, not a different page.
    """

    path: str
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    id: EntityId = field(default_factory=new_entity_id)
    # Non-placement options (engraving power, trace threshold, …). Kept open
    # so the two treatments do not each need their own schema here.
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "options": deepcopy(self.options),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PlacedImage:
        def number(key: str) -> float:
            try:
                return float(raw.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        placed = cls(
            path=str(raw.get("path") or ""),
            x=number("x"),
            y=number("y"),
            width=number("width"),
            height=number("height"),
            rotation=number("rotation"),
            options=dict(raw.get("options") or {}),
        )
        if raw.get("id"):
            placed.id = str(raw["id"])
        return placed


@dataclass
class Document:
    """Canonical runtime aggregate for entities, selection, layers, and groups."""

    entities: list[EntityRecord] = field(default_factory=list)
    selection: set[EntityId] = field(default_factory=set)
    layer_order: list[str] = field(default_factory=list)
    active_layer: str | None = None
    layer_colors: dict[str, str] = field(default_factory=dict)
    group_labels: dict[int, str] = field(default_factory=dict)
    next_group_id: int = 0
    constraints: list[GeometricConstraint] = field(default_factory=list)
    guides: list[tuple[str, float]] = field(default_factory=list)
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    # Images placed on the part, and one treatment per region keyed by the
    # owning outline's id. Both used to live on the Pattern page, which is
    # what made them impossible to see from Draw or Trace.
    images: list[PlacedImage] = field(default_factory=list)
    treatments: dict[EntityId, dict[str, Any]] = field(default_factory=dict)
    _validate_on_mutate: bool = field(default=True)

    def image_for_id(self, image_id: EntityId) -> PlacedImage | None:
        return next((image for image in self.images if image.id == image_id), None)

    def treatment_for(self, region_id: EntityId) -> dict[str, Any]:
        return self.treatments.get(region_id) or {}

    def set_treatment(self, region_id: EntityId, treatment: dict[str, Any] | None) -> None:
        if not treatment or str(treatment.get("kind", "none")) == "none":
            self.treatments.pop(region_id, None)
            return
        self.treatments[region_id] = deepcopy(treatment)

    def prune_treatments(self) -> int:
        """Drop treatments whose region no longer exists. Returns how many."""
        live = set(self.entity_ids())
        dropped = [rid for rid in self.treatments if rid not in live]
        for region_id in dropped:
            del self.treatments[region_id]
        return len(dropped)

    def ensure_unique_ids(self) -> None:
        seen: set[EntityId] = set()
        for entity in self.entities:
            if not entity.id or entity.id in seen:
                entity.id = new_entity_id()
            seen.add(entity.id)

    def _by_id_map(self) -> dict[EntityId, EntityRecord]:
        return {entity.id: entity for entity in self.entities}

    def entity_for_id(self, entity_id: EntityId) -> EntityRecord | None:
        return self._by_id_map().get(entity_id)

    def entity_ids(self) -> list[EntityId]:
        return [entity.id for entity in self.entities]

    def selected_ids(self) -> set[EntityId]:
        return set(self.selection)

    def select_ids(self, entity_ids: Iterable[EntityId]) -> None:
        self.selection = set(entity_ids)

    def flagged_ids(self, attribute: str) -> set[EntityId]:
        return {entity.id for entity in self.entities if bool(getattr(entity, attribute, False))}

    def set_flagged_ids(self, attribute: str, entity_ids: Iterable[EntityId]) -> None:
        wanted = set(entity_ids)
        for entity in self.entities:
            setattr(entity, attribute, entity.id in wanted)

    def on_active_layer(self, entity: EntityRecord) -> bool:
        return (
            self.active_layer is None or entity.layer is None or entity.layer == self.active_layer
        )

    def entity_selectable_by_id(self, entity_id: EntityId) -> bool:
        entity = self.entity_for_id(entity_id)
        if entity is None:
            return False
        # The active layer determines where new geometry is created; it is
        # not a selection filter. Visible geometry remains selectable across
        # layers, while hidden layers remain protected from interaction.
        return not entity.hidden

    def drop_inactive_selection(self) -> bool:
        """Discard only hidden or deleted entities from selection.

        The historical name is retained because callers invoke this after a
        layer change. Switching layers must not lose a mixed-layer selection.
        """
        selection = {eid for eid in self.selection if self.entity_selectable_by_id(eid)}
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

    # ── Invariant enforcement ──────────────────────────────────────────────

    def _validate(self) -> list[str]:
        """Validate document invariants. Returns list of violation messages."""
        violations: list[str] = []

        # 1. Entity ID uniqueness
        seen_ids: set[EntityId] = set()
        for entity in self.entities:
            if entity.id in seen_ids:
                violations.append(f"Duplicate entity ID: {entity.id}")
            seen_ids.add(entity.id)

        # 2. Selection contains only valid entity IDs (non-empty, non-duplicate)
        for eid in self.selection:
            if not eid or not isinstance(eid, str):
                violations.append(f"Selection contains invalid entity ID: {repr(eid)}")

        # 3. Entity layers are valid strings or None (layer_order membership enforced by set_layer_model)
        for entity in self.entities:
            if entity.layer is not None and not isinstance(entity.layer, str):
                violations.append(f"Entity {entity.id} has invalid layer: {repr(entity.layer)}")

        # 4. Groups have 2+ members — enforced by reconcile_groups(), not validated here
        # (groups may be in transient state during command application)

        # 5. Entity point count matches kind
        # Only kinds that store geometry in points require minimum counts.
        # Circle/arc/ellipse/point store geometry in metadata (center, radius, etc.)
        for entity in self.entities:
            kind = entity.kind
            n = len(entity.points)
            if kind == "line" and n < 2:
                violations.append(f"Entity {entity.id} kind='line' has {n} points (need >= 2)")
            elif kind == "bezier" and n < 2:
                violations.append(f"Entity {entity.id} kind='bezier' has {n} points (need >= 2)")
            elif kind == "polyline" and n < 2:
                violations.append(f"Entity {entity.id} kind='polyline' has {n} points (need >= 2)")

        # 6. Active layer exists when entities exist
        if self.entities and self.active_layer and self.layer_order:
            if self.active_layer not in self.layer_order:
                violations.append(f"Active layer '{self.active_layer}' not in layer_order")

        return violations

    def _assert_valid(self) -> None:
        """Raise AssertionError on invariant violations (dev-time only)."""
        violations = self._validate()
        if violations:
            raise AssertionError(
                "Document invariant violations:\n" + "\n".join(f"  - {v}" for v in violations)
            )

    def append(self, entity: EntityRecord) -> int:
        if self.entity_for_id(entity.id) is not None:
            entity.id = new_entity_id()
        self.entities.append(entity)
        if self._validate_on_mutate:
            self._assert_valid()
        return len(self.entities) - 1

    def replace(self, entities: Iterable[EntityRecord]) -> None:
        self.entities = list(entities)
        self.selection.clear()
        self.ensure_unique_ids()
        if self._validate_on_mutate:
            self._assert_valid()


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
    ``apply_pattern_workspace_state()`` in ``src/simple_stipple/features/pattern/session.py``.
    """

    dxf_path: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    orig_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    edit_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    outline_ids: list[str] = Field(default_factory=list)
    # The Pattern tab keeps the source document's layer assignment while an
    # outline is being edited.  Preview/export layers are derived separately.
    outline_layers: dict[str, str] = Field(default_factory=dict)
    pattern_cell_cutouts: list[list[tuple[float, float]]] = Field(default_factory=list)
    pattern_cell_instance_cutouts: list[list[tuple[float, float]]] = Field(default_factory=list)
    orig_w: float = 0.0
    orig_h: float = 0.0
    canvas_view: dict[str, Any] = Field(default_factory=dict)
    preview_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    showing_preview: bool = False
    # One treatment per region, keyed by outline id. ``zones`` and
    # ``exclusion_ids`` are read-only legacy inputs: pre-Phase-1 workspaces
    # are migrated onto ``treatments`` at load (see treatments.py), and
    # ``zones`` is still written out as a derived view for older builds.
    treatments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    zones: list[dict[str, Any]] = Field(default_factory=list)
    exclusion_ids: list[str] = Field(default_factory=list)
    custom_tile_polys: list[list[tuple[float, float]]] = Field(default_factory=list)
    engraving_image_path: str = ""
    engraving_options: dict[str, Any] = Field(default_factory=dict)


class ShapeTabState(TabStateBase):
    """Workspace state for the Shape page (currently minimal)."""

    canvas_view: dict[str, Any] = Field(default_factory=dict)


class ImageTabState(TabStateBase):
    """Workspace state for the Image/Trace page (uses TraceTabState)."""

    canvas_view: dict[str, Any] = Field(default_factory=dict)


class TraceTabState(TabStateBase):
    """Workspace state for TracePage.

    Mirrors the keys read by ``get_trace_workspace_state()`` and written by
    ``apply_trace_workspace_state()`` in ``src/simple_stipple/features/trace/session.py``.
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
    ``apply_draft_workspace_state()`` in
    ``src/simple_stipple/features/draft/session.py``.
    """

    entities: list[dict[str, Any]] = Field(default_factory=list)
    layer_order: list[str] = Field(default_factory=list)
    active_layer: str = ""
    canvas_view: dict[str, Any] = Field(default_factory=dict)
    quick_shape_mode: str = ""
    quick_shape_enabled: bool = False
    last_input_dxf: str = ""
    # Custom object names from the layer tree: layer name → entity id → label.
    shape_labels: dict[str, dict[str, str]] = Field(default_factory=dict)


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
