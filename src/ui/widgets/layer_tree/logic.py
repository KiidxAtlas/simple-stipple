"""Layer tree shared types/helpers + the sidebar controller that wires a
DXF layer tree to a canvas.

Two previously-separate modules merged here — the controller only exists to
consume this module's types and helper functions, with no independent
reason to be a separate file.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

# ── Type aliases ──────────────────────────────────────────────────────────────

LayerTreeState = dict[str, dict[str, set[str]]]
LayerTreeRow = dict[str, Any]
LayerRowsBuilder = Callable[[LayerTreeState], list[dict[str, Any]]]

# ── Layer tree helper functions ───────────────────────────────────────────────


def hidden_bucket(state: LayerTreeState, layer_name: str) -> set[str]:
    bucket = state.setdefault(layer_name, {"hidden": set[str]()})
    hidden = bucket.get("hidden")
    if hidden is None:
        hidden = set[str]()
        bucket["hidden"] = hidden
    return hidden


def apply_layer_visibility(hidden: set[str], count: int, visible: bool) -> None:
    if visible:
        hidden.clear()
    else:
        hidden.clear()
        hidden.update(str(i) for i in range(max(0, count)))


def apply_shape_visibility(hidden: set[str], entity_id: str, visible: bool) -> None:
    if visible:
        hidden.discard(entity_id)
    else:
        hidden.add(entity_id)


def build_shape_rows(
    entity_ids: list[str],
    polylines: list[list[tuple[float, float]]],
    hidden: set[str],
    label_builder: Callable[[str, list[tuple[float, float]]], str],
    *,
    editable: bool,
    draggable: bool,
    groups: dict[str, int] | None = None,
    group_labels: dict[int, str] | None = None,
) -> list[LayerTreeRow]:
    """Build one tree row per shape — except grouped shapes, which collapse
    into a single row per group (like an SVG ``<g``)). A group row's key is
    the tuple of member entity IDs; use :func:`flatten_shape_keys` to resolve
    keys back to entity IDs."""
    rows: list[LayerTreeRow] = []
    groups = groups or {}
    members_by_gid: dict[int, list[str]] = {}
    for eid in entity_ids:
        gid = groups.get(eid)
        if gid is not None:
            members_by_gid.setdefault(gid, []).append(eid)

    emitted: set[int] = set()
    for eid, poly in zip(entity_ids, polylines):
        gid = groups.get(eid)
        if gid is None:
            rows.append(
                {
                    "key": eid,
                    "label": label_builder(eid, poly),
                    "visible": eid not in hidden,
                    "editable": editable,
                    "draggable": draggable,
                }
            )
            continue
        if gid in emitted:
            continue
        emitted.add(gid)
        members = members_by_gid[gid]
        custom = (group_labels or {}).get(gid)
        title = custom or "Group"
        rows.append(
            {
                "key": tuple(members),
                "label": f"{title}  ·  {len(members)} shapes",
                "visible": any(e not in hidden for e in members),
                "editable": editable,
                "draggable": draggable,
            }
        )
    return rows


def flatten_shape_keys(keys: object) -> list[str]:
    """Resolve tree shape keys (str entity IDs or group tuples) to entity IDs."""
    if not isinstance(keys, (list, tuple, set)):
        keys = [keys]
    out: list[str] = []
    for k in keys:
        if isinstance(k, str):
            out.append(k)
        elif isinstance(k, (tuple, list)):
            out.extend(str(i) for i in k if isinstance(i, (str, int)))
    return out


def build_layer_row(
    *,
    name: str,
    display_name: str,
    active: bool,
    visible: bool,
    editable: bool,
    shapes: list[LayerTreeRow],
    color: str | None = None,
) -> LayerTreeRow:
    return {
        "name": name,
        "internal_name": name,
        "display_name": display_name,
        "visible": visible,
        "active": active,
        "editable": editable,
        "shapes": shapes,
        "color": color,
    }


def describe_polyline(entity_id: str, poly: list[tuple[float, float]]) -> str:
    if not poly:
        return f"{entity_id}  Empty"
    xs, ys = zip(*poly)
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    point_count = len(poly)
    if len(poly) > 1 and math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1]) < 0.01:
        point_count -= 1
        kind = "Closed"
    else:
        kind = "Open"
    return f"{entity_id}  {kind}  ·  {point_count} pts  ·  {width:.1f} × {height:.1f} mm"


# ════════════════════════════════════════════════════════════════════════════
# Controller — wires a layer tree widget to a canvas
# ════════════════════════════════════════════════════════════════════════════


class CanvasLayerSidebarController:
    """Own layer visibility state and keep tree/canvas interaction in sync."""

    def __init__(
        self,
        *,
        canvas: Any,
        layers_tree: Any,
        get_active_layer_name: Callable[[], str],
        build_rows: LayerRowsBuilder,
        on_selection_requested: Callable[[list[int]], None],
        on_fit_requested: Callable[[], None],
        on_visibility_changed: Callable[[], None],
        visibility_adapter: Any | None = None,
    ) -> None:
        self._canvas = canvas
        self._layers_tree = layers_tree
        self._get_active_layer_name = get_active_layer_name
        self._build_rows = build_rows
        self._on_visibility_changed = on_visibility_changed
        # Entity-native visibility: when set, eye toggles write hidden flags
        # straight onto canvas entities (via set_shapes_hidden /
        # set_layer_hidden / solo_layer / set_all_hidden) instead of being
        # tracked in per-layer index buckets here.
        self._visibility_adapter = visibility_adapter
        self._layer_view_state: LayerTreeState = {}
        self._shape_counts: dict[str, int] = {}

        self._layers_tree.selectionRequested.connect(on_selection_requested)
        self._layers_tree.fitRequested.connect(on_fit_requested)
        self._layers_tree.layerVisibilityChanged.connect(self.on_layer_visibility_changed)
        self._layers_tree.shapeVisibilityChanged.connect(self.on_shape_visibility_changed)
        # New optional signals — guarded with hasattr so older trees still work.
        if hasattr(self._layers_tree, "bulkVisibilityRequested"):
            self._layers_tree.bulkVisibilityRequested.connect(self.on_bulk_visibility_requested)
        if hasattr(self._layers_tree, "layerSoloRequested"):
            self._layers_tree.layerSoloRequested.connect(self.on_solo_requested)
        # Shape operation signals — group, ungroup, merge, copy.
        if hasattr(self._layers_tree, "shapesGroupRequested"):
            self._layers_tree.shapesGroupRequested.connect(
                lambda layer, keys: self._handle_shape_operation("group", layer, keys)
            )
        if hasattr(self._layers_tree, "shapesUngroupRequested"):
            self._layers_tree.shapesUngroupRequested.connect(
                lambda layer, keys: self._handle_shape_operation("ungroup", layer, keys)
            )
        if hasattr(self._layers_tree, "shapesMergeRequested"):
            self._layers_tree.shapesMergeRequested.connect(
                lambda layer, keys: self._handle_shape_operation("merge", layer, keys)
            )
        if hasattr(self._layers_tree, "shapesCopyRequested"):
            self._layers_tree.shapesCopyRequested.connect(
                lambda layer, keys: self._handle_shape_operation("copy", layer, keys)
            )

    def _handle_shape_operation(self, op: str, layer: str, keys: list) -> None:
        """Dispatch shape operations from the layer tree to the canvas."""
        entity_ids = flatten_shape_keys(keys)
        if not entity_ids:
            return
        canvas = self._canvas
        if op == "group":
            if hasattr(canvas, "group_entities"):
                canvas.group_entities(entity_ids)
        elif op == "ungroup":
            if hasattr(canvas, "ungroup_entities"):
                canvas.ungroup_entities(entity_ids)
        elif op == "merge":
            if hasattr(canvas, "merge_selected_segments_to_objects"):
                # Temporarily select, merge, then restore selection.
                canvas.set_selection(entity_ids)
                canvas.merge_selected_segments_to_objects()
        elif op == "copy" and hasattr(canvas, "_clipboard"):
            clip_items = [
                {
                    "points": list(canvas._entities_by_id[eid].points),
                    "kind": canvas._entities_by_id[eid].kind,
                    "meta": canvas._entities_by_id[eid].meta,
                }
                for eid in entity_ids
                if eid in canvas._entities_by_id
            ]
            canvas._clipboard = clip_items

    @property
    def state(self) -> LayerTreeState:
        return self._layer_view_state

    def clear(self) -> None:
        self._layer_view_state = {}
        self._shape_counts = {}

    def hidden_for(self, layer_name: str) -> set[str]:
        return hidden_bucket(self._layer_view_state, layer_name)

    def apply_current_visibility(self) -> None:
        if self._visibility_adapter is not None:
            return  # hidden flags live on the entities themselves
        active_name = self._get_active_layer_name()
        hidden = sorted(self.hidden_for(active_name))
        self._canvas.set_hidden_ids(hidden)

    def refresh_tree(self) -> None:
        rows = self._build_rows(self._layer_view_state)
        self._shape_counts = {
            str(row.get("name", "")): len(list(row.get("shapes", [])))
            for row in rows
            if isinstance(row, dict)
        }
        self._layers_tree.set_layers(rows)
        # Rebuilding the tree wipes its row selection — reapply whatever is
        # currently selected on the canvas so the highlight survives status
        # refreshes (e.g. clicking a shape on a non-active layer switches
        # the active layer, which triggers a refresh right after the shape
        # selection was set; without this, the tree would show only the
        # newly-active LAYER highlighted, not the actual selected shape).
        get_indices = getattr(self._canvas, "get_selected_ids", None)
        select_keys = getattr(self._layers_tree, "select_shape_keys", None)
        if callable(get_indices) and callable(select_keys):
            select_keys(get_indices())

    def on_layer_visibility_changed(self, layer: str, visible: bool) -> None:
        if self._visibility_adapter is not None:
            self._visibility_adapter.set_layer_hidden(layer, not visible)
            self._on_visibility_changed()
            return
        hidden = self.hidden_for(layer)
        apply_layer_visibility(hidden, self._shape_counts.get(layer, 0), visible)
        self.apply_current_visibility()
        self._on_visibility_changed()

    def on_shape_visibility_changed(
        self,
        layer: str,
        shape_key: object,
        visible: bool,
    ) -> None:
        entity_ids = flatten_shape_keys(shape_key)
        if not entity_ids:
            return
        if self._visibility_adapter is not None:
            self._visibility_adapter.set_shapes_hidden(entity_ids, not visible)
            self._on_visibility_changed()
            return
        hidden = self.hidden_for(layer)
        for entity_id in entity_ids:
            apply_shape_visibility(hidden, entity_id, visible)
        self.apply_current_visibility()
        self._on_visibility_changed()

    def on_bulk_visibility_requested(self, visible: bool) -> None:
        """Toggle every known layer's visibility at once."""
        if self._visibility_adapter is not None:
            self._visibility_adapter.set_all_hidden(not visible)
            self.refresh_tree()
            self._on_visibility_changed()
            return
        if not self._shape_counts:
            return
        for layer_name, count in self._shape_counts.items():
            hidden = self.hidden_for(layer_name)
            apply_layer_visibility(hidden, count, visible)
        self.apply_current_visibility()
        self.refresh_tree()
        self._on_visibility_changed()

    def on_solo_requested(self, target_layer: str) -> None:
        """Show only *target_layer*; hide every other known layer."""
        if self._visibility_adapter is not None:
            self._visibility_adapter.solo_layer(target_layer)
            self.refresh_tree()
            self._on_visibility_changed()
            return
        if not self._shape_counts:
            return
        for layer_name, count in self._shape_counts.items():
            hidden = self.hidden_for(layer_name)
            apply_layer_visibility(hidden, count, layer_name == target_layer)
        self.apply_current_visibility()
        self.refresh_tree()
        self._on_visibility_changed()
