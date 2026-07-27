"""Declarative field specifications for the Pattern page form."""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_PATTERN_DIMENSION_MM = 20.0


@dataclass
class ParamField:
    attr: str  # instance attribute name set on the tab (e.g. "_hex_r")
    label: str  # display label in the grid
    default: str  # default value text
    tooltip: str = ""
    kind: str = "float"  # "float" | "int" | "checkbox" | "combobox"
    items: list[str] = field(default_factory=list)  # choices for "combobox"
    hint: str | None = None  # optional hint label appended after this field
    param_key: str = ""  # key in the generator params dict; defaults to attr[1:]
    minimum: float | None = None  # lower bound for numeric fields
    maximum: float | None = None  # upper bound for numeric fields

    def __post_init__(self) -> None:
        """Cap physical pattern controls for the app's small-format outlines."""
        if self.kind in {"float", "int"} and "(mm)" in self.label:
            self.maximum = (
                min(self.maximum, MAX_PATTERN_DIMENSION_MM)
                if self.maximum is not None
                else MAX_PATTERN_DIMENSION_MM
            )


# ── Parameter specs for each named pattern ────────────────────────────────────
# Each list entry maps directly to a row in the param grid widget.
# Fields marked hint="..." render a small muted label below them.

PARAM_SPECS: dict[str, list[ParamField]] = {
    "Flow Lines": [
        ParamField(
            "_flow_spacing",
            "Line spacing (mm)",
            "3",
            "Distance between neighboring flow lines",
            param_key="spacing",
            minimum=0.01,
            maximum=1000,
        ),
        ParamField(
            "_flow_amplitude",
            "Wave amplitude (mm)",
            "2",
            "Side-to-side movement of each flowing line",
            param_key="amplitude",
            minimum=0.0,
            maximum=1000,
        ),
        ParamField(
            "_flow_wavelength",
            "Wavelength (mm)",
            "18",
            "Distance over one complete wave",
            param_key="wavelength",
            minimum=0.01,
            maximum=10000,
        ),
        ParamField(
            "_flow_angle",
            "Direction (°)",
            "0",
            "Overall flow direction",
            param_key="angle",
        ),
    ],
    "Custom Tile": [
        ParamField(
            "_custom_tile_gap",
            "Tile gap (mm)",
            "0.5",
            "Spacing between repetitions of the selected custom geometry",
            param_key="gap",
            minimum=0.0,
            maximum=20,
        ),
        ParamField(
            "_custom_tile_repeat",
            "Repeat mode",
            "Straight",
            "How neighboring motif copies are offset or transformed",
            kind="combobox",
            items=[
                "Straight",
                "Half drop",
                "Brick offset",
                "Mirror rows",
                "Mirror columns",
                "Alternate 180°",
            ],
            param_key="repeat_mode",
        ),
        ParamField(
            "_custom_tile_origin_x",
            "Origin X (mm)",
            "0",
            "Horizontal phase offset for the repeat lattice",
            param_key="origin_x",
        ),
        ParamField(
            "_custom_tile_origin_y",
            "Origin Y (mm)",
            "0",
            "Vertical phase offset for the repeat lattice",
            param_key="origin_y",
        ),
    ],
    "Honeycomb": [
        ParamField(
            "_hex_r",
            "Hex size (mm)",
            "1.75",
            "Radius of each hexagonal cell",
            param_key="r",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_hex_gap",
            "Gap (mm)",
            "0.5",
            "Spacing between adjacent hexagons",
            param_key="gap",
            minimum=0.0,
            maximum=20,
        ),
    ],
    "Gradient Honeycomb": [
        ParamField(
            "_grad_r_min",
            "Min size (mm)",
            "0.8",
            "Smallest hex cell size at one end of the gradient",
            param_key="r_min",
            minimum=0.0,
            maximum=20,
        ),
        ParamField(
            "_grad_r_max",
            "Max size (mm)",
            "2.5",
            "Largest hex cell size at the other end",
            param_key="r_max",
            minimum=0.001,
            maximum=20,
        ),
        ParamField(
            "_grad_gap",
            "Gap (mm)",
            "0.5",
            "Spacing between hexagons",
            param_key="gap",
            minimum=0.0,
            maximum=1000,
        ),
        ParamField(
            "_grad_angle",
            "Direction (°)",
            "0",
            "Gradient direction in degrees (0 = left to right)",
            hint="0° = left→right  ·  90° = vertical",
            param_key="angle",
        ),
    ],
    "Basketweave": [
        ParamField(
            "_basket_strip_w",
            "Strip width (mm)",
            "2.0",
            "Width of each woven strip",
            param_key="strip_w",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_basket_strip_l",
            "Strip length (mm)",
            "8.0",
            "Length of each woven strip",
            param_key="strip_l",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_basket_gap",
            "Gap (mm)",
            "0.2",
            "Gap between woven strips",
            param_key="gap",
            minimum=0.0,
            maximum=1000,
        ),
    ],
    "Stipple Dots": [
        ParamField(
            "_stip_r",
            "Dot radius (mm)",
            "0.4",
            "Radius of each stipple dot",
            param_key="r",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_stip_spacing",
            "Spacing (mm)",
            "1.2",
            "Centre-to-centre distance between dots",
            param_key="spacing",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_stip_seed",
            "Seed",
            "42",
            "Deterministic random seed for repeatable stipple placement",
            kind="int",
            param_key="seed",
        ),
    ],
    "Brick": [
        ParamField(
            "_brick_w",
            "Brick width (mm)",
            "4.0",
            "Width of each brick",
            param_key="brick_w",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_brick_h",
            "Brick height (mm)",
            "2.0",
            "Height of each brick",
            param_key="brick_h",
            minimum=0.001,
            maximum=1000,
        ),
        ParamField(
            "_brick_gap",
            "Gap (mm)",
            "0.5",
            "Mortar gap between bricks",
            param_key="gap",
            minimum=0.0,
            maximum=1000,
        ),
    ],
    "Mesh": [
        ParamField(
            "_mesh_r",
            "Circle radius (mm)",
            "0.35",
            "Radius of each mesh circle",
            param_key="r",
            minimum=0.001,
            maximum=100,
        ),
        ParamField(
            "_mesh_spacing",
            "Grid spacing (mm)",
            "1.2",
            "Centre-to-centre distance between mesh circles",
            param_key="spacing",
            minimum=0.001,
            maximum=1000,
        ),
    ],
    "Voronoi": [
        ParamField(
            "_vor_cells",
            "Cell count",
            "60",
            "Number of random Voronoi cells to generate",
            kind="int",
            param_key="n_cells",
            minimum=2,
            maximum=10000,
        ),
        ParamField(
            "_vor_gap",
            "Gap (mm)",
            "0.15",
            "Inset distance between Voronoi cells",
            param_key="gap",
            minimum=0.0,
            maximum=1000,
        ),
        ParamField(
            "_vor_seed",
            "Seed",
            "42",
            "Random seed for reproducible cell placement",
            kind="int",
            param_key="seed",
        ),
    ],
    "Topographic": [
        ParamField(
            "_topo_spacing",
            "Contour spacing (mm)",
            "1.5",
            "Distance between successive contour lines",
            hint="Inward offset contours from the outline edge",
            param_key="spacing",
            minimum=0.1,
            maximum=500,
        ),
    ],
}
