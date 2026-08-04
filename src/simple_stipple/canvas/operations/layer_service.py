"""Layer state and entity-layer operations for a canvas."""

from __future__ import annotations


class LayerService:
    def __init__(self, host) -> None:
        self._host = host

    @property
    def active_layer(self) -> str | None:
        return self._host._active_layer

    def names(self) -> list[str]:
        names = list(self._host._layer_order)
        for entity in self._host._entities:
            if entity.layer is not None and entity.layer not in names:
                names.append(entity.layer)
        return names

    def set_model(self, order: list[str], active: str | None) -> None:
        host = self._host
        host._layer_order = [str(name) for name in order if str(name)]
        if active is not None and str(active) not in host._layer_order:
            host._layer_order.append(str(active))
        host._active_layer = str(active) if active is not None else None
        if host._active_layer is not None:
            for entity in host._entities:
                if entity.layer is None:
                    entity.layer = host._active_layer
        self.drop_inactive_selection()
        host._redraw()

    def set_active(self, name: str) -> None:
        host = self._host
        name = str(name)
        if host._active_layer == name:
            return

        def mutate(document) -> None:
            if name not in document.layer_order:
                document.layer_order.append(name)
            document.active_layer = name
            document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._reset_edit_interaction_state()
        host._redraw()
        host._notify()

    def add(self, name: str, *, activate: bool = False) -> None:
        host = self._host
        name = str(name)
        if name in host._layer_order and not activate:
            return

        def mutate(document) -> None:
            if name not in document.layer_order:
                document.layer_order.append(name)
            if activate:
                document.active_layer = name
                document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._redraw()

    def rename(self, old: str, new: str) -> None:
        host = self._host
        old, new = str(old), str(new).strip()
        if not new or old == new or new in host._layer_order:
            return

        def mutate(document) -> None:
            document.layer_order = [new if name == old else name for name in document.layer_order]
            for entity in document.entities:
                if entity.layer == old:
                    entity.layer = new
            if document.active_layer == old:
                document.active_layer = new
            color = document.layer_colors.pop(old, None)
            if color is not None:
                document.layer_colors[new] = color

        host._canvas_service.update_document(mutate)
        host._redraw()

    def delete(self, name: str) -> None:
        host = self._host
        name = str(name)
        dropped = sum(entity.layer == name for entity in host._entities)

        def mutate(document) -> None:
            document.entities = [entity for entity in document.entities if entity.layer != name]
            document.layer_order = [layer for layer in document.layer_order if layer != name]
            if not document.layer_order:
                document.layer_order = [
                    document.active_layer
                    if document.active_layer is not None and document.active_layer != name
                    else "Layer 1"
                ]
            if document.active_layer == name:
                document.active_layer = document.layer_order[0]
            document.layer_colors.pop(name, None)
            document.selection.clear()

        host._canvas_service.update_document(mutate)
        host._redraw()
        host._notify()
        if dropped:
            host._fire_poly_change()

    def color(self, name: str) -> str | None:
        return self._host._layer_colors.get(str(name))

    def set_color(self, name: str, color: str | None) -> None:
        name = str(name)

        def mutate(document) -> None:
            if color is None:
                document.layer_colors.pop(name, None)
            else:
                document.layer_colors[name] = str(color)

        self._host._canvas_service.update_document(mutate)
        self._host._redraw()

    def consolidate(self, sources: list[str], target: str) -> int:
        host = self._host
        target = str(target)
        source_set = {str(source) for source in sources if str(source) and str(source) != target}
        if not source_set:
            return 0
        moved = sum(entity.layer in source_set for entity in host._entities)

        def mutate(document) -> None:
            if target not in document.layer_order:
                document.layer_order.append(target)
            for entity in document.entities:
                if entity.layer in source_set:
                    entity.layer = target
            document.layer_order = [
                name for name in document.layer_order if name not in source_set
            ] or [target]
            if document.active_layer in source_set:
                document.active_layer = target
            for name in source_set:
                document.layer_colors.pop(name, None)
            document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._redraw()
        host._notify()
        if moved:
            host._fire_poly_change()
        return moved

    def move(self, name: str, new_index: int) -> None:
        names = self.names()
        if str(name) not in names:
            return
        names.remove(str(name))
        names.insert(max(0, min(int(new_index), len(names))), str(name))
        self._host._canvas_service.update_document(
            lambda document: setattr(document, "layer_order", names)
        )
        self._host._redraw()

    def move_entities(self, entity_ids: list[str], layer: str) -> int:
        host = self._host
        layer = str(layer)
        moved = [
            eid
            for eid in entity_ids
            if (entity := host._entities_by_id.get(eid)) is not None and entity.layer != layer
        ]
        if not moved:
            return 0

        def mutate(document) -> None:
            if layer not in document.layer_order:
                document.layer_order.append(layer)
            for entity in document.entities:
                if entity.id in moved:
                    entity.layer = layer
            document.drop_inactive_selection()

        host._canvas_service.update_document(mutate)
        host._redraw()
        host._notify()
        host._fire_poly_change()
        return len(moved)

    def on_active(self, entity) -> bool:
        return self._host._document.on_active_layer(entity)

    def selectable(self, entity_id: str) -> bool:
        return self._host._document.entity_selectable_by_id(entity_id)

    def selectable_by_id(self, entity_id: str) -> bool:
        return self._host._document.entity_selectable_by_id(entity_id)

    def noninteractive_indices(self) -> set[str]:
        return {
            entity.id
            for entity in self._host._entities
            if entity.hidden
        }

    def drop_inactive_selection(self) -> None:
        if self._host._document.drop_inactive_selection():
            self._host._notify()
