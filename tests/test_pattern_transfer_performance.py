"""Region UI refreshes must not repeat expensive geometry work per outline."""

from types import SimpleNamespace

from PySide6.QtWidgets import QLabel, QListWidget

from simple_stipple.core.patterns.tiling import build_region_tree
from simple_stipple.features.pattern.export import document_operations
from simple_stipple.features.pattern.layout import refresh_pattern_properties_panel


def _page():
    ids = ["outer", "inner", "open"]
    polys = [
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
        [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)],
        [(20, 0), (21, 0)],
    ]
    calls = []

    def region_tree():
        calls.append(None)
        return build_region_tree(ids, polys)

    page = SimpleNamespace(
        _outline_ids=ids,
        _treatments={"inner": {"kind": "fill"}},
        _region_tree=region_tree,
    )
    return page, calls


def test_output_builds_region_geometry_once_and_preserves_operation_names():
    page, calls = _page()

    operations = document_operations(page)

    assert len(calls) == 1
    assert [(op.key, op.subject, op.target) for op in operations] == [
        ("mark:inner", "Fill", "Region 2"),
        ("cut:boundary:outer", "Region 1 boundary", "outline"),
        ("cut:remaining", "1 untreated outline", "outline"),
    ]


def test_inspector_builds_region_geometry_once(qtbot):
    page, calls = _page()
    page._pattern_props_scope = QLabel()
    page._zone_list = QListWidget()
    qtbot.addWidget(page._pattern_props_scope)
    qtbot.addWidget(page._zone_list)
    page._zone_list.addItems(["outer", "inner"])
    page._zone_list.setCurrentRow(1)

    refresh_pattern_properties_panel(page)

    assert len(calls) == 1
    assert page._pattern_props_scope.text().startswith("Region 2")
    assert page._pattern_props_scope.property("editing") is True
