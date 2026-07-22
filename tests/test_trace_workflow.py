from __future__ import annotations

import pytest
from PIL import Image

pytest.importorskip("PySide6")

from src.ui.components import CollapsibleSection
from src.ui.pages.trace.tab import TracePage


def test_trace_advanced_numeric_controls_use_synchronized_sliders(qapp):
    from src.ui.pages.trace.form import SliderField
    page = TracePage()
    try:
        advanced = next(
            section
            for section in page.findChildren(CollapsibleSection)
            if section._toggle.text() == "Advanced"
        )
        advanced.set_expanded(True)
        fields = advanced.findChildren(SliderField)
        assert len(fields) == 5

        simplify = next(field for field in fields if field.entry is page._simplify)
        simplify.slider.setValue(25)
        assert page._simplify.text() == "2.5"

        maximum = next(field for field in fields if field.entry is page._max_area)
        maximum.slider.setValue(0)
        assert page._max_area.text() == ""
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


@pytest.fixture()
def trace_page(qapp):
    page = TracePage(None, {"trace_next_action": "pattern"})
    yield page
    page.shutdown()
    page.deleteLater()
    qapp.processEvents()


def test_trace_remembers_next_action_and_hands_off_all_closed_results(trace_page):
    page = trace_page
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    page._canvas.set_polylines_state([square])
    received: list[list[list[tuple[float, float]]]] = []
    page.sendSelectedToPatternRequested.connect(received.append)

    assert page._next_btn.text() == "Next — Use in Pattern"
    page._run_remembered_next()

    assert received == [[square]]


def test_successful_trace_collapses_source_and_keeps_next_available(trace_page):
    page = trace_page
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    page._needs_view_fit = False

    page._handle_trace_done((0, Image.new("L", (20, 20)), [square], 20, 20, 50.0))

    assert not page._source_section.is_expanded()
    assert page._thumb_lbl.maximumHeight() == 64
    assert page._next_btn.isEnabled()
    assert not page._trace_result_stale


def test_empty_retrace_preserves_last_valid_geometry(trace_page):
    page = trace_page
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]
    page._canvas.set_polylines_state([square])

    page._handle_trace_done((0, Image.new("L", (20, 20)), [], 20, 20, 50.0))

    assert page._canvas.get_polylines_state() == [square]
    assert "previous result is retained" in page._status.text()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("cannot allocate memory", "Reduce Max resolution"),
        ("no foreground", "Try Invert"),
        ("unsupported image decode", "Convert the source to PNG"),
        ("geometry invalid", "Increase Min area"),
    ],
)
def test_trace_failures_offer_specific_recovery(message, expected):
    guidance = TracePage._trace_failure_guidance(message)
    assert expected in guidance
    assert "previous result is retained" in guidance


def test_smoothing_uses_nonmodal_canvas_hud(trace_page, monkeypatch):
    page = trace_page
    page._canvas.set_polylines_state(
        [[(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 0.0)]]
    )
    prompts: list[tuple] = []
    monkeypatch.setattr(
        page._canvas,
        "_show_hud_prompt",
        lambda *args, **kwargs: prompts.append((args, kwargs)),
    )

    page._smooth_traced_curves()

    assert len(prompts) == 1
    args, kwargs = prompts[0]
    assert "Enter applies · Esc cancels" in args[0]
    assert kwargs == {"minimum": 0.01, "maximum": 10.0}


def test_trace_recipe_name_is_inline_and_saved_without_modal(trace_page, monkeypatch):
    page = trace_page
    saved = []
    monkeypatch.setattr(
        "src.ui.pages.trace.tab.save_settings", lambda settings: saved.append(settings)
    )
    page._recipe_name_edit.setText("Fine detail")

    page._save_trace_recipe()

    assert "Fine detail" in page._settings["trace_recipes"]
    assert page._recipe_combo.currentText() == "Fine detail"
    assert page._recipe_name_edit.text() == ""
    assert len(saved) == 1
