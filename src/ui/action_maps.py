"""Cross-tab action registries for UX consistency audit."""

from __future__ import annotations

SHAPE_ACTION_MAP: dict[str, dict[str, object]] = {
    "create_shape": {
        "semantic": "create_entities",
        "primitives": ["Point", "Segment"],
        "dependencies": ["layer:geometry"],
    },
    "select": {
        "semantic": "select_entities",
        "primitives": ["selection"],
        "dependencies": ["layer:active"],
    },
    "delete": {
        "semantic": "delete_entities",
        "primitives": ["Point", "Segment"],
        "dependencies": ["layer:geometry"],
    },
    "fit": {
        "semantic": "fit_view",
        "primitives": ["view"],
        "dependencies": ["layer:active"],
    },
    "export": {
        "semantic": "export_layer",
        "primitives": ["layer:geometry"],
        "dependencies": ["serializer:dxf"],
    },
}

IMAGE_ACTION_MAP: dict[str, dict[str, object]] = {
    "trace": {
        "semantic": "derive_layer",
        "primitives": ["source:image", "params:trace"],
        "dependencies": ["layer:trace_preview"],
    },
    "select": {
        "semantic": "select_entities",
        "primitives": ["selection"],
        "dependencies": ["layer:trace_preview"],
    },
    "delete": {
        "semantic": "delete_entities",
        "primitives": ["layer:trace_preview"],
        "dependencies": ["layer:trace_preview"],
    },
    "fit": {
        "semantic": "fit_view",
        "primitives": ["view"],
        "dependencies": ["layer:trace_preview"],
    },
    "export": {
        "semantic": "export_layer",
        "primitives": ["layer:trace_preview"],
        "dependencies": ["serializer:dxf"],
    },
}

PATTERN_ACTION_MAP: dict[str, dict[str, object]] = {
    "preview": {
        "semantic": "derive_layer",
        "primitives": ["layer:outline", "params:pattern"],
        "dependencies": ["layer:pattern_preview"],
    },
    "select": {
        "semantic": "select_entities",
        "primitives": ["selection"],
        "dependencies": ["layer:outline"],
    },
    "delete": {
        "semantic": "delete_entities",
        "primitives": ["layer:outline"],
        "dependencies": ["layer:outline"],
    },
    "fit": {
        "semantic": "fit_view",
        "primitives": ["view"],
        "dependencies": ["layer:active"],
    },
    "export": {
        "semantic": "export_layer",
        "primitives": ["layer:pattern_preview"],
        "dependencies": ["serializer:dxf"],
    },
}

UTILITIES_ACTION_MAP: dict[str, dict[str, object]] = {
    "import": {
        "semantic": "derive_layer",
        "primitives": ["source:file"],
        "dependencies": ["layer:preview"],
    },
    "fit": {
        "semantic": "fit_view",
        "primitives": ["view"],
        "dependencies": ["layer:preview"],
    },
    "export": {
        "semantic": "export_layer",
        "primitives": ["layer:preview"],
        "dependencies": ["serializer:dxf", "serializer:svg"],
    },
}
