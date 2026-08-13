from __future__ import annotations

from types import SimpleNamespace

from simple_stipple.features.trace import session as dxf_export
from simple_stipple.features.trace.page import TracePage


class _Canvas:
    def __init__(self, records: list[dict], selected_ids: list[str] | None = None) -> None:
        self.records = records
        self.selected_ids = selected_ids or []

    def get_export_dxf_state(self) -> list[dict]:
        return self.records

    def get_selected_ids(self) -> list[str]:
        return self.selected_ids


def _page(records: list[dict], selected_ids: list[str] | None = None) -> SimpleNamespace:
    statuses: list[tuple[str, str]] = []
    return SimpleNamespace(
        _canvas=_Canvas(records, selected_ids),
        _last_out=None,
        _reveal_action=SimpleNamespace(setEnabled=lambda _value: None),
        _set_status=lambda text, color: statuses.append((text, color)),
        statuses=statuses,
    )


def test_trace_page_preserves_its_dxf_export_action_surface() -> None:
    assert TracePage._export_all is dxf_export.export_all
    assert TracePage._export_selected is dxf_export.export_selected


def test_trace_dxf_export_preserves_native_metadata_and_selection(
    monkeypatch,
) -> None:
    records = [
        {
            "entity_id": "arc-1",
            "polyline": [(0.0, 0.0), (2.0, 2.0)],
            "kind": "arc",
            "meta": {"radius": 2.0},
        },
        {
            "entity_id": "line-1",
            "polyline": [(3.0, 0.0), (5.0, 2.0)],
            "kind": "line",
            "meta": {"start": (3.0, 0.0)},
        },
    ]
    page = _page(records, ["arc-1"])
    written: list[tuple] = []
    monkeypatch.setattr(dxf_export, "export_preflight", lambda *_args, **_kwargs: (True, {}))
    monkeypatch.setattr(dxf_export, "get_save_path", lambda *_args: "/tmp/trace.dxf")
    monkeypatch.setattr(
        dxf_export.DxfService,
        "write_polylines_dxf",
        lambda paths, output, **kwargs: written.append((paths, output, kwargs)),
    )

    dxf_export.export_selected(page)

    assert written == [
        (
            [[(0.0, 0.0), (2.0, 2.0)]],
            "/tmp/trace.dxf",
            {
                "close": False,
                "entity_kinds": ["arc"],
                "entity_meta": [{"radius": 2.0}],
            },
        )
    ]
    assert page._last_out == "/tmp/trace.dxf"
    assert "Exported 1 selected shapes" in page.statuses[-1][0]
