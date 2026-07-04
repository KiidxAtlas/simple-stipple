"""DxfLayersTree: color swatch rendering and the color-change signal."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def make_tree(qapp):
    from src.ui.widgets.layer_tree.widget import DxfLayersTree

    return DxfLayersTree("Layers", editable=True)


def test_set_layers_accepts_color_and_builds_swatch_icon(qapp):
    tree = make_tree(qapp)
    tree.set_layers(
        [
            {
                "name": "Layer 1",
                "internal_name": "Layer 1",
                "display_name": "Layer 1",
                "visible": True,
                "active": True,
                "editable": True,
                "color": "#f0883e",
                "shapes": [],
            },
            {
                "name": "Cut",
                "internal_name": "Cut",
                "display_name": "Cut",
                "visible": True,
                "active": False,
                "editable": True,
                "color": None,
                "shapes": [],
            },
        ]
    )
    item0 = tree._tree.topLevelItem(0)
    item1 = tree._tree.topLevelItem(1)
    assert item0.data(0, tree._ROLE_COLOR) == "#f0883e"
    assert item1.data(0, tree._ROLE_COLOR) is None
    assert not item0.icon(0).isNull()
    assert not item1.icon(0).isNull()  # empty-swatch icon still renders


def test_layer_color_change_signal_emits(qapp):
    tree = make_tree(qapp)
    tree.set_layers(
        [
            {
                "name": "Layer 1",
                "internal_name": "Layer 1",
                "display_name": "Layer 1",
                "visible": True,
                "active": True,
                "editable": True,
                "color": None,
                "shapes": [],
            }
        ]
    )
    received = []
    tree.layerColorChangeRequested.connect(
        lambda layer, color: received.append((layer, color))
    )
    tree.layerColorChangeRequested.emit("Layer 1", "#3fb950")
    assert received == [("Layer 1", "#3fb950")]
