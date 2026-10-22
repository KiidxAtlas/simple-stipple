"""Preserve StarFX artwork object identity at the document boundary."""
from copy import deepcopy

from simple_stipple.core.document.kinds import EntityKind
from simple_stipple.core.document.model import Document, EntityRecord
from simple_stipple.core.formats.starfx import StarfxDocument


def import_starfx_objects(
    document: Document, artwork: StarfxDocument, *, append: bool
) -> None:
    """Mutate a candidate document for one undoable job import.

    A word (including glyph holes) or vector object is one group. Native
    circles/rectangles stay single shapes and never join a text group.
    Leave the imported entities deselected so the first drag cannot move the
    entire import as a previously selected multi-object selection.
    """
    if not append:
        document.entities = []
        document.selection.clear()
        document.group_labels.clear()
        document.constraints.clear()
        document.dimensions.clear()
        document.treatments.clear()
        document.next_group_id = 0
        document.layer_order = ["Layer 1"]
        document.active_layer = "Layer 1"
        document.layer_colors.clear()
    layer = document.active_layer or "Layer 1"
    if layer not in document.layer_order:
        document.layer_order.append(layer)
    document.active_layer = layer
    document.reconcile_groups()
    for obj in artwork.objects:
        group = None
        if len(obj.paths) > 1:
            group = document.next_group_id
            document.next_group_id += 1
            document.group_labels[group] = str(obj.meta.get("name") or obj.name)
        for poly in obj.paths:
            document.append(EntityRecord(
                points=list(poly), kind=EntityKind.coerce(obj.kind), meta=deepcopy(obj.meta),
                group=group, layer=layer,
            ))
    document.select_ids([])
