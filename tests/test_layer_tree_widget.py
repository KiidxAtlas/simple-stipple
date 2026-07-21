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
    assert item0 is not None and item1 is not None
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
    tree.layerColorChangeRequested.connect(lambda layer, color: received.append((layer, color)))
    tree.layerColorChangeRequested.emit("Layer 1", "#3fb950")
    assert received == [("Layer 1", "#3fb950")]


def _make_layer_row(name: str) -> dict:
    return {
        "name": name,
        "internal_name": name,
        "display_name": name,
        "visible": True,
        "active": True,
        "editable": True,
        "color": None,
        "shapes": [],
    }


def test_rename_that_triggers_a_synchronous_tree_rebuild_does_not_crash(qapp):
    """Regression: a real app commonly reacts to layerRenamed by rebuilding
    the whole tree (set_layers()) — which deletes the underlying C++
    QTreeWidgetItem while _handle_item_changed is still running on it,
    previously crashing with libshiboken's "Internal C++ object already
    deleted" as soon as it touched `item` again after the emit.

    Note: itemChanged is fired by Qt's C++ QTreeWidgetItem::setText, and an
    exception raised inside a slot invoked that way is caught and merely
    printed by PySide's default excepthook rather than propagated — so
    driving this through item.setText() would silently "pass" even with
    the bug present. Disconnecting the signal and calling the handler
    directly (a plain Python call, no C++ round-trip) is what actually lets
    the RuntimeError surface for pytest to catch.
    """
    tree = make_tree(qapp)
    layers = [_make_layer_row("Layer 1")]
    tree.set_layers(layers)

    renamed: list[tuple[str, str]] = []

    def _on_renamed(old: str, new: str) -> None:
        renamed.append((old, new))
        layers[0] = _make_layer_row(new)
        tree.set_layers(layers)  # rebuilds — old `item` becomes dangling

    tree.layerRenamed.connect(_on_renamed)
    tree._tree.itemChanged.disconnect(tree._handle_item_changed)

    item = tree._tree.topLevelItem(0)
    assert item is not None
    item.setText(0, "Renamed Layer")
    tree._handle_item_changed(item, 0)  # must not raise

    assert renamed == [("Layer 1", "Renamed Layer")]
    # The tree really was rebuilt with the new name — not left stale.
    rebuilt = tree._tree.topLevelItem(0)
    assert rebuilt is not None
    assert rebuilt.text(0).startswith("Renamed Layer")


def test_shape_rename_still_works_after_layer_rename_fix(qapp):
    """Guard against a regression in the shape-rename branch, which already
    had the same early-return pattern this fix adds to layer rename."""
    tree = make_tree(qapp)
    tree.set_layers(
        [
            {
                **_make_layer_row("Layer 1"),
                "shapes": [
                    {
                        "key": 0,
                        "label": "Shape 1",
                        "editable": True,
                        "visible": True,
                    }
                ],
            }
        ]
    )
    layer_item = tree._tree.topLevelItem(0)
    assert layer_item is not None
    shape_item = layer_item.child(0)
    assert shape_item is not None

    renamed: list[tuple] = []
    tree.shapeRenamed.connect(lambda layer, key, new: renamed.append((layer, key, new)))
    shape_item.setText(0, "Renamed Shape")
    assert renamed == [("Layer 1", 0, "Renamed Shape")]


def test_delete_key_on_selected_shape_requests_shape_deletion(qapp):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    tree = make_tree(qapp)
    tree.set_layers(
        [
            {
                **_make_layer_row("Layer 1"),
                "shapes": [
                    {"key": 7, "label": "Shape 1", "editable": True, "visible": True}
                ],
            }
        ]
    )
    shape = tree._tree.topLevelItem(0).child(0)
    shape.setSelected(True)
    tree._tree.setCurrentItem(shape)
    received: list[tuple[str, list]] = []
    tree.shapesDeleteRequested.connect(lambda layer, keys: received.append((layer, keys)))

    tree._tree.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    )

    assert received == [("Layer 1", [7])]
