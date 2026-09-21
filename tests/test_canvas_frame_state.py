"""Characterization for the immutable canvas frame-state snapshot.

The renderer, hit-testing, and snap engine now read one ``CanvasFrameState``
per frame instead of the live view's private fields. These tests pin the
coordinate contract and the sampled read-model so future migrations of those
consumers cannot silently change transform behavior.
"""

from __future__ import annotations

import pytest

from simple_stipple.canvas.objects import (
    CanvasFrameState,
    CanvasInteractionState,
    CanvasViewportState,
)
from simple_stipple.core.document.model import CanvasDocument, EntityRecord


def _viewport() -> CanvasViewportState:
    return CanvasViewportState(scale=2.0, origin_x=100.0, origin_y=200.0, width=640, height=480)


def test_viewport_world_canvas_round_trip() -> None:
    viewport = _viewport()
    for world in ((0.0, 0.0), (12.5, -8.0), (250.0, 75.0)):
        cx, cy = viewport.world_to_canvas(*world)
        back = viewport.canvas_to_world(cx, cy)
        assert back[0] == pytest.approx(world[0])
        assert back[1] == pytest.approx(world[1])


def test_viewport_transform_matches_scale_and_origin() -> None:
    viewport = _viewport()
    # Canvas Y grows downward, so world +Y maps to smaller canvas Y.
    assert viewport.world_to_canvas(0.0, 0.0) == (100.0, 200.0)
    assert viewport.world_to_canvas(10.0, 5.0) == (120.0, 190.0)


def test_frame_state_defaults_are_empty_and_frozen() -> None:
    frame = CanvasFrameState(CanvasDocument(entities=[]), _viewport())
    assert frame.selection == frozenset()
    assert frame.interaction == CanvasInteractionState()
    with pytest.raises(AttributeError):
        frame.selection = frozenset({"x"})  # type: ignore[misc]


def test_frame_state_carries_selection_and_interaction() -> None:
    entity = EntityRecord(id="a", points=[(0.0, 0.0), (1.0, 1.0)])
    frame = CanvasFrameState(
        CanvasDocument(entities=[entity]),
        _viewport(),
        selection=frozenset({"a"}),
        interaction=CanvasInteractionState(
            mode="draw",
            draw_primitive="polyline",
            draw_points=((0.0, 0.0),),
            cursor_world=(3.0, 4.0),
        ),
    )
    assert frame.document.entity_for_id("a") is entity
    assert frame.selection == frozenset({"a"})
    assert frame.interaction.mode == "draw"
    assert frame.interaction.cursor_world == (3.0, 4.0)
