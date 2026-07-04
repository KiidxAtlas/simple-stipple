"""Canvas layer runtime — a thin adapter between the layer tree and the
canvas's per-entity layer model.

Entities carry their layer, hidden, and locked state on ``EntityRecord``,
so there is no separate document graph to capture into or reload from:
switching layers is a repaint, and moving shapes between layers is a field
assignment. (This replaced a DocumentGraph + adapter + per-layer view-state
bucket stack that kept three copies of the same facts.)
"""

from __future__ import annotations

from typing import Any

from src.ui.widgets.layer_tree import (
    build_layer_row,
    describe_polyline,
    flatten_shape_keys,
)


class CanvasRuntime:
    """Coordinate layer state between a multi-layer canvas and its page UI."""

    def __init__(self, *, canvas: Any, default_layer: str = "Layer 1") -> None:
        self._canvas = canvas
        self.default_layer = default_layer
        canvas.set_layer_model([default_layer], default_layer)

    # ── Layer queries ─────────────────────────────────────────────────────

    def current_layer_name(self) -> str:
        return self._canvas.active_layer or self.default_layer

    def on_tree_selection_requested(self, indices: list[int]) -> None:
        idxs = flatten_shape_keys(indices)
        canvas = self._canvas
        # Clicking a shape row that lives on a non-active layer activates
        # that layer first, so tree selection always works.
        for i in idxs:
            if 0 <= i < len(canvas._entities):
                layer = canvas._entities[i].layer
                if layer and layer != canvas.active_layer:
                    canvas.set_active_layer(layer)
                break
        canvas.set_selection(idxs)

    def reload_active_layer(self, *, fit: bool = False) -> None:
        """Kept for API compatibility — entities never leave the canvas."""
        if fit:
            self._canvas.fit()
        else:
            self._canvas._redraw()

    # ── Layer operations ──────────────────────────────────────────────────

    def switch_active_layer(self, layer: str, *, fit: bool = False) -> bool:
        changed = self.current_layer_name() != layer
        self._canvas.set_active_layer(layer)
        if fit:
            self._canvas.fit()
        return changed

    def add_layer_and_activate(self, layer: str) -> None:
        self._canvas.add_layer(layer, activate=True)

    def shape_move_requested(
        self,
        source_layer: str,
        shape_key: object,
        target_layer: str,
    ) -> bool:
        return self.shapes_move_requested(source_layer, [shape_key], target_layer)

    def shapes_move_requested(
        self,
        source_layer: str,
        shape_keys: list,
        target_layer: str,
    ) -> bool:
        if source_layer == target_layer:
            return False
        indices = sorted(set(flatten_shape_keys(shape_keys)))
        if not indices:
            return False
        return self._canvas.move_indices_to_layer(indices, target_layer) > 0

    def move_selected_to_layer(self, target_layer: str) -> bool:
        indices = self._canvas.get_selection_indices()
        if not indices:
            return False
        return self._canvas.move_indices_to_layer(indices, target_layer) > 0

    def layer_renamed(self, old_name: str, new_name: str) -> None:
        self._canvas.rename_layer(old_name, new_name)
        if self.default_layer == old_name:
            self.default_layer = new_name

    def layer_deleted(self, layer: str) -> None:
        self._canvas.delete_layer(layer)

    def layer_moved(self, layer: str, new_index: int) -> None:
        self._canvas.move_layer(layer, new_index)

    def on_canvas_edit(self) -> None:
        """Kept for API compatibility — entity state is already the truth."""

    # ── Visibility (entity-native) ────────────────────────────────────────

    def set_shapes_hidden(self, keys: object, hidden: bool) -> None:
        canvas = self._canvas
        for idx in flatten_shape_keys(keys):
            if 0 <= idx < len(canvas._entities):
                canvas._entities[idx].hidden = hidden
        canvas._drop_inactive_selection()
        canvas._redraw()

    def set_layer_hidden(self, layer: str, hidden: bool) -> None:
        canvas = self._canvas
        for e in canvas._entities:
            if e.layer == layer:
                e.hidden = hidden
        canvas._drop_inactive_selection()
        canvas._redraw()

    def solo_layer(self, target_layer: str) -> None:
        canvas = self._canvas
        for e in canvas._entities:
            e.hidden = e.layer != target_layer
        canvas._drop_inactive_selection()
        canvas._redraw()

    def set_all_hidden(self, hidden: bool) -> None:
        canvas = self._canvas
        for e in canvas._entities:
            e.hidden = hidden
        canvas._drop_inactive_selection()
        canvas._redraw()

    # ── Shape labels ──────────────────────────────────────────────────────

    def rename_shape(self, layer_name: str, shape_key: object, new_label: str) -> None:
        """Persist a custom display label for a shape (or name a group)."""
        if isinstance(shape_key, (tuple, list)) and shape_key:
            first = shape_key[0]
            gid = self._canvas._group_of(first) if isinstance(first, int) else None
            if gid is not None:
                self._canvas.set_group_label(gid, new_label)
            return
        if not isinstance(shape_key, int):
            return
        ents = self._canvas._entities
        if not (0 <= shape_key < len(ents)):
            return
        e = ents[shape_key]
        label = str(new_label).strip()
        if label:
            e.meta = {**(e.meta or {}), "label": label}
        elif e.meta:
            e.meta.pop("label", None)

    # ── Layer tree rows ───────────────────────────────────────────────────

    def _shape_label(self, ordinal: int, e: Any) -> str:
        """Row label. ``ordinal`` is the shape's position within its layer
        (stable, human-friendly numbering — not the entity index)."""
        geo = describe_polyline(ordinal, e.points)
        custom = (e.meta or {}).get("label")
        if not custom:
            return geo
        parts = geo.split("  ", 1)
        geo_suffix = parts[1] if len(parts) > 1 else geo
        return f"{custom}  ·  {geo_suffix}"

    def build_layer_shape_rows(self, layer_name: str) -> list[dict[str, Any]]:
        """One row per shape (grouped shapes collapse into one row per
        group). Row keys are canvas entity indices."""
        canvas = self._canvas
        pairs = [
            (i, e) for i, e in enumerate(canvas._entities) if e.layer == layer_name
        ]
        group_labels = dict(getattr(canvas, "_group_labels", {}))
        members_by_gid: dict[int, list[int]] = {}
        for i, e in pairs:
            if e.group is not None:
                members_by_gid.setdefault(e.group, []).append(i)

        rows: list[dict[str, Any]] = []
        emitted: set[int] = set()
        ordinal = 0
        for i, e in pairs:
            gid = e.group
            if gid is None or len(members_by_gid.get(gid, [])) < 2:
                rows.append(
                    {
                        "key": i,
                        "label": self._shape_label(ordinal, e),
                        "visible": not e.hidden,
                        "editable": True,
                        "draggable": True,
                    }
                )
                ordinal += 1
                continue
            if gid in emitted:
                continue
            emitted.add(gid)
            members = members_by_gid[gid]
            title = group_labels.get(gid) or "Group"
            rows.append(
                {
                    "key": tuple(members),
                    "label": (
                        f"{ordinal + 1:02d}  {title}  ·  {len(members)} shapes"
                    ),
                    "visible": any(
                        not canvas._entities[m].hidden for m in members
                    ),
                    "editable": True,
                    "draggable": True,
                }
            )
            ordinal += 1
        return rows

    def build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[int]]] | None = None,
    ) -> list[dict[str, Any]]:
        canvas = self._canvas
        active = self.current_layer_name()
        rows: list[dict[str, Any]] = []
        for name in canvas.layer_names():
            shapes = self.build_layer_shape_rows(name)
            visible = not shapes or any(row["visible"] for row in shapes)
            rows.append(
                build_layer_row(
                    name=name,
                    display_name=name,
                    active=name == active,
                    visible=visible,
                    editable=True,
                    shapes=shapes,
                    color=canvas.layer_color(name),
                )
            )
        if not rows:
            rows = [
                build_layer_row(
                    name=self.default_layer,
                    display_name=self.default_layer,
                    active=True,
                    visible=True,
                    editable=True,
                    shapes=[],
                )
            ]
        return rows

    # ── Loading ───────────────────────────────────────────────────────────

    def load_polys(self, polys: list[list[tuple[float, float]]], *, fit: bool) -> None:
        self._canvas.set_layer_model([self.default_layer], self.default_layer)
        self._canvas.set_polylines_state(polys, fit=fit)
        self._canvas.set_mode("select")

    def load_polys_by_layer(
        self,
        by_layer: dict[str, list[list[tuple[float, float]]]],
        *,
        fit: bool,
    ) -> None:
        """Load polylines into named layers; first layer becomes active."""
        if not by_layer:
            self.load_polys([], fit=fit)
            return
        names = list(by_layer)
        self._canvas.set_layer_model(names, names[0])
        flat: list[list[tuple[float, float]]] = []
        layers: list[str] = []
        for name, polys in by_layer.items():
            for p in polys:
                flat.append(p)
                layers.append(name)
        self._canvas.set_polylines_state(flat, fit=fit)
        for e, layer in zip(self._canvas._entities, layers):
            e.layer = layer
        self._canvas.set_mode("select")
        self._canvas._redraw()

    def restore_graph_state(self, graph_state: dict) -> None:
        """Restore a legacy DocumentGraph session snapshot (pre-entity-layer
        workspaces) into the per-entity layer model."""
        from src.backend.document.graph import DocumentGraph

        graph = DocumentGraph()
        graph.restore(graph_state)

        # The legacy "geometry" sentinel layer only matters when it was the
        # active layer (single-layer sessions); its content lands on the
        # default layer. Otherwise it is an empty internal artifact.
        active_was_geometry = graph.active_layer == "geometry"
        records: list[dict] = []
        order: list[str] = []
        for name, layer in graph.iter_layers():
            if name == "geometry":
                if not active_was_geometry and not layer.polylines:
                    continue
                name = self.default_layer
            layer_records = layer.records
            if layer_records is None:
                layer_records = [
                    {"points": [list(pt) for pt in p], "kind": "polyline", "meta": None}
                    for p in layer.polylines
                ]
            for r in layer_records:
                r = dict(r)
                r["layer"] = name
                records.append(r)
            if name not in order:
                order.append(name)

        active = self.default_layer if active_was_geometry else graph.active_layer
        if not order:
            order = [self.default_layer]
        if active not in order:
            active = order[0]
        self._canvas.set_entity_records(records)
        self._canvas.set_layer_model(order, active)

    def reset_empty(self) -> None:
        self._canvas.set_layer_model([self.default_layer], self.default_layer)
        self._canvas.set_polylines_state([])
        self._canvas.set_mode("select")
