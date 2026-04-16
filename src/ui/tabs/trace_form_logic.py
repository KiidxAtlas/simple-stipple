"""Trace form parsing and run-configuration logic."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QCheckBox, QLineEdit


@dataclass(frozen=True)
class TraceFieldBindings:
    blur: QLineEdit
    simplify: QLineEdit
    min_area: QLineEdit
    max_area: QLineEdit
    close_r: QLineEdit
    width_mm: QLineEdit
    max_res: QLineEdit
    threshold: QLineEdit
    canny_low: QLineEdit
    canny_high: QLineEdit
    auto_thresh_cb: QCheckBox
    invert_cb: QCheckBox
    edge_mode_cb: QCheckBox
    outer_only_cb: QCheckBox


def build_trace_kwargs(
    fields: TraceFieldBindings,
    *,
    parse_float_field,
    on_progress,
) -> dict | None:
    """Parse all form values and return trace kwargs preserving existing rules."""
    blur_radius = parse_float_field(fields.blur, "Blur radius", minimum=0.0)
    simplify = parse_float_field(fields.simplify, "Simplify", minimum=0.0)
    min_area = parse_float_field(fields.min_area, "Min area", minimum=0.0)
    max_area = parse_float_field(
        fields.max_area,
        "Max area",
        minimum=0.0,
        allow_empty=True,
    )
    close_r = parse_float_field(fields.close_r, "Closing radius", minimum=0.0)
    width_mm = parse_float_field(fields.width_mm, "Width", minimum=0.001)

    auto_thresh = fields.auto_thresh_cb.isChecked()
    thresh: float | None = None
    if not auto_thresh:
        thresh = parse_float_field(
            fields.threshold,
            "Threshold",
            minimum=0.0,
            maximum=255.0,
        )

    required = [blur_radius, simplify, min_area, close_r, width_mm]
    if not auto_thresh:
        required.append(thresh)
    if any(value is None for value in required):
        return None

    assert blur_radius is not None
    assert simplify is not None
    assert min_area is not None
    assert close_r is not None
    assert width_mm is not None

    edge_mode = fields.edge_mode_cb.isChecked()
    canny_low_val = 50
    canny_high_val = 150
    if edge_mode:
        canny_l = parse_float_field(
            fields.canny_low, "Canny low", minimum=1.0, maximum=255.0
        )
        canny_h = parse_float_field(
            fields.canny_high, "Canny high", minimum=1.0, maximum=255.0
        )
        assert canny_l is not None
        assert canny_h is not None
        canny_low_val = int(canny_l)
        canny_high_val = int(canny_h)

    max_res = parse_float_field(
        fields.max_res, "Max resolution", minimum=64.0, maximum=8000.0
    )
    assert max_res is not None

    return {
        "blur_radius": blur_radius,
        "threshold": int(max(0, min(255, thresh))) if thresh is not None else None,
        "invert": fields.invert_cb.isChecked(),
        "simplify_tol": simplify,
        "min_area_px": min_area,
        "max_area_px": max_area,
        "close_radius": max(0, int(close_r)),
        "width_mm": width_mm,
        "max_px": int(max_res),
        "edge_mode": edge_mode,
        "canny_low": canny_low_val,
        "canny_high": canny_high_val,
        "outer_only": fields.outer_only_cb.isChecked(),
        "on_progress": on_progress,
    }
