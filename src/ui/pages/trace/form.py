"""Image trace form components, run-configuration logic, and persistent
user-configurable defaults for new Trace sessions.

Merged from the former ``defaults.py`` — small and tightly related to the
form fields it configures. The defaults are the values a freshly-opened
image (or a cleared workspace) starts with — e.g. "Max resolution" or
"Simplify tolerance" — as opposed to per-workspace saved state
(``src/ui/pages/trace/session.py``), which restores whatever was last used
on a specific, already-traced workspace file. Editable from the Settings
dialog under "Trace Defaults"; stored in ``settings["trace_defaults"]`` as
the same string a ``QLineEdit`` holds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from src.ui.components import CollapsibleSection


class TextField(QWidget):
    """Compact labeled text field with required/optional affordance."""

    def __init__(
        self,
        label: str,
        *,
        entry: QLineEdit | None = None,
        default: str = "",
        required: bool = True,
        width: int = 80,
        placeholder: str = "",
        tooltip: str = "",
    ) -> None:
        super().__init__()
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._marker = QLabel(label)
        self._marker.setProperty("role", "hint")
        self._marker.setWordWrap(True)
        self.entry = entry or QLineEdit(default)
        if entry is None:
            self.entry.setText(default)
        self.entry.setFixedWidth(width)
        self.entry.setPlaceholderText(placeholder)
        self.entry.setAccessibleName(label)
        self._marker.setBuddy(self.entry)
        if tooltip:
            self.entry.setToolTip(tooltip)
        self._layout.addWidget(self._marker, stretch=1)
        self._layout.addWidget(self.entry)

    def resizeEvent(self, event) -> None:
        """Stack labels when the containing Trace inspector is below 340 px."""
        super().resizeEvent(event)
        stacked = event.size().width() < 310  # 340 px sidebar minus card margins
        self._layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if stacked
            else QBoxLayout.Direction.LeftToRight
        )


class SliderField(QWidget):
    """Labeled slider paired with the existing editable numeric field."""

    def __init__(
        self,
        label: str,
        *,
        entry: QLineEdit,
        minimum: float,
        maximum: float,
        step: float = 1.0,
        empty_at_minimum: bool = False,
        tooltip: str = "",
    ) -> None:
        super().__init__()
        self.entry = entry
        self._minimum = minimum
        self._step = step
        self._empty_at_minimum = empty_at_minimum
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(TextField(label, entry=entry, required=not empty_at_minimum, tooltip=tooltip))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName(label)
        self.slider.setRange(0, round((maximum - minimum) / step))
        self.slider.setToolTip(tooltip)
        layout.addWidget(self.slider)
        self.slider.valueChanged.connect(self._slider_changed)
        entry.textChanged.connect(self._entry_changed)
        self._entry_changed(entry.text())

    def _slider_changed(self, position: int) -> None:
        if self._empty_at_minimum and position == 0:
            text = ""
        else:
            value = self._minimum + position * self._step
            text = str(int(round(value))) if self._step >= 1 else f"{value:.1f}"
        if self.entry.text() != text:
            self.entry.setText(text)

    def _entry_changed(self, text: str) -> None:
        try:
            value = float(text)
            position = round((value - self._minimum) / self._step)
        except ValueError:
            if not (self._empty_at_minimum and not text.strip()):
                return
            position = 0
        self.slider.blockSignals(True)
        self.slider.setValue(max(self.slider.minimum(), min(position, self.slider.maximum())))
        self.slider.blockSignals(False)


class PathField(QWidget):
    """Path input + browse action for file-based workflows."""

    def __init__(
        self,
        placeholder: str,
        browse_label: str,
        on_browse: Callable[[], None],
        *,
        tooltip: str = "",
    ) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.entry = QLineEdit()
        self.entry.setAccessibleName("Image file")
        self.entry.setPlaceholderText(placeholder)
        if tooltip:
            self.entry.setToolTip(tooltip)
        browse_btn = QPushButton(browse_label)
        browse_btn.setAccessibleName(f"Browse for {placeholder.removesuffix('…')}")
        browse_btn.setFixedWidth(64)
        browse_btn.clicked.connect(on_browse)
        lay.addWidget(self.entry, stretch=1)
        lay.addWidget(browse_btn)


def build_lazy_section(
    title: str,
    build_content: Callable[[QVBoxLayout], None],
    *,
    expanded: bool,
) -> CollapsibleSection:
    """Create a collapsible section whose content is instantiated on first expand."""
    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(6)
    section = CollapsibleSection(title, content, expanded=expanded)

    built = {"done": False}

    def _ensure_built(checked: bool) -> None:
        if checked and not built["done"]:
            build_content(content_layout)
            built["done"] = True

    if isinstance(section._toggle, QToolButton):
        section._toggle.toggled.connect(_ensure_built)
    _ensure_built(expanded)
    return section


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
    """Parse all form values and return trace kwargs."""
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
        canny_l = parse_float_field(fields.canny_low, "Canny low", minimum=1.0, maximum=255.0)
        canny_h = parse_float_field(fields.canny_high, "Canny high", minimum=1.0, maximum=255.0)
        assert canny_l is not None
        assert canny_h is not None
        canny_low_val = int(canny_l)
        canny_high_val = int(canny_h)

    max_res = parse_float_field(fields.max_res, "Max resolution", minimum=64.0, maximum=8000.0)
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


# ══════════════════════════════════════════════════════════════════════════
# Persistent, user-configurable defaults for new Trace sessions
# ══════════════════════════════════════════════════════════════════════════

#: (settings key, display label, tooltip) for every editable numeric default.
TRACE_DEFAULT_FIELDS: tuple[tuple[str, str, str], ...] = (
    (
        "max_res",
        "Max resolution (px)",
        "Maximum pixel dimension when loading the image.",
    ),
    (
        "blur",
        "Blur radius",
        "Gaussian blur radius applied before thresholding / edge detection.",
    ),
    (
        "simplify",
        "Simplify tolerance",
        "Tolerance for polygon simplification (higher = fewer points).",
    ),
    ("min_area", "Min area (px²)", "Discard contours smaller than this area."),
    ("close_r", "Closing radius", "Morphological closing to fill small gaps in edges."),
    (
        "width_mm",
        "Default width (mm)",
        "Target output width in millimetres for a newly loaded image.",
    ),
)

#: Built-in fallback used when a key has never been set in trace_defaults.
TRACE_DEFAULTS: dict[str, str] = {
    "blur": "1.0",
    "threshold": "128",
    "canny_low": "50",
    "canny_high": "150",
    "simplify": "0.7",
    "min_area": "10",
    "max_area": "",
    "close_r": "1",
    "width_mm": "50.0",
    "height_mm": "50.0",
    "max_res": "2200",
}


def trace_default(settings: dict | None, key: str) -> str:
    """Return the configured default for *key*, falling back to the
    built-in value if the user has never overridden it."""
    overrides = (settings or {}).get("trace_defaults") or {}
    if key in overrides:
        return str(overrides[key])
    return TRACE_DEFAULTS[key]


__all__ = [
    "PathField",
    "TRACE_DEFAULTS",
    "TRACE_DEFAULT_FIELDS",
    "TextField",
    "TraceFieldBindings",
    "build_lazy_section",
    "build_trace_kwargs",
    "trace_default",
]
