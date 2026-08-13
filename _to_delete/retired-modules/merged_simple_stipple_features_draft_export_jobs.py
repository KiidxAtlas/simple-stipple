"""UI-independent task assembly for Draft DXF exports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DraftDxfExportPlan:
    """Layer-preserving records ready for ``DxfService.write_polylines_dxf``."""

    records: list[dict[str, Any]]
    first_layer_name: str
    first_layer_records: list[dict[str, Any]]
    extra_layer_records: dict[str, list[dict[str, Any]]] | None


def build_dxf_export_plan(
    records: Iterable[Mapping[str, Any]],
    dimensions: Iterable[Mapping[str, Any]],
    *,
    active_layer_name: str,
    layer_names: Iterable[str],
) -> DraftDxfExportPlan:
    """Add dimension entities and preserve document-layer ordering for export."""
    export_records = [dict(record) for record in records]
    for dimension in dimensions:
        p1 = tuple(dimension["p1"])
        p2 = tuple(dimension["p2"])
        export_records.append(
            {
                "polyline": [p1, p2],
                "kind": "dimension",
                "meta": dict(dimension),
                "layer": str(dimension.get("layer") or active_layer_name),
            }
        )

    by_layer: dict[str, list[dict[str, Any]]] = {}
    for record in export_records:
        by_layer.setdefault(str(record.get("layer") or active_layer_name), []).append(record)
    ordered_names = [name for name in layer_names if name in by_layer]
    if not ordered_names:
        ordered_names = list(by_layer)

    first_layer_name = ordered_names[0] if ordered_names else (active_layer_name or "Layer")
    first_layer_records = by_layer.get(first_layer_name, [])
    extra_layer_records = {name: by_layer[name] for name in ordered_names[1:]} or None
    return DraftDxfExportPlan(
        records=export_records,
        first_layer_name=first_layer_name,
        first_layer_records=first_layer_records,
        extra_layer_records=extra_layer_records,
    )


__all__ = ["DraftDxfExportPlan", "build_dxf_export_plan"]
