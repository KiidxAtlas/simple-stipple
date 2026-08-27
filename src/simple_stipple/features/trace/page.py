# pyright: reportAttributeAccessIssue=false
"""Image to Outline page."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.canvas.runtime import (
    CanvasGridModule,
    CanvasLayerTreeModule,
    CanvasToolbarModule,
)
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.canvas.widgets.toolbar import CanvasStatusStrip
from simple_stipple.core.cad.preflight import analyze_geometry
from simple_stipple.core.imaging import RasterEngravingSpec, export_raster_job, image_to_outlines
from simple_stipple.features.base import BasePage
from simple_stipple.features.trace.canvas_runtime import TraceCanvasPageRuntime
from simple_stipple.features.trace.form import (
    PathField,
    SliderField,
    TextField,
    TraceFieldBindings,
    build_lazy_section,
    build_trace_kwargs,
    trace_default,
)
from simple_stipple.features.trace.model import TraceModel
from simple_stipple.features.trace.session import (
    apply_trace_workspace_state,
    clear_trace_workspace_state,
    get_trace_workspace_state,
    run_trace_job,
)
from simple_stipple.features.trace.session import (
    export_all as _export_all,
)
from simple_stipple.features.trace.session import (
    export_selected as _export_selected,
)
from simple_stipple.features.trace.session import (
    get_save_path as _get_save_path,
)
from simple_stipple.platform.settings import save_settings
from simple_stipple.ui.components.feedback import parse_float_field_with_feedback, show_error
from simple_stipple.ui.components.inputs import NoWheelSlider, make_resettable_line_edit
from simple_stipple.ui.components.layout import (
    CollapsibleSection,
    content_splitter,
    sidebar_panel,
    surface_frame,
)
from simple_stipple.ui.components.recent import KIND_IMAGE, RecentFilesButton, record_recent
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.dialogs.files import pick_open_file, pick_save_file, reveal_label
from simple_stipple.ui.style import STATUS_ERR, STATUS_NEUTRAL, STATUS_OK, STATUS_WARN

TRACE_BG_COLOR = (0x16, 0x21, 0x3E)
TRACE_BG_BLEND_ALPHA = 0.7

# ── Page default settings ────────────────────────────────────────────────
# Detection defaults (blur, threshold, simplify, …) live in
# ``simple_stipple.features.trace.form.TRACE_DEFAULTS`` and are user-editable in
# Settings — only the widgets' mechanical config is defined here.
TRACE_DEBOUNCE_MS = 220  # retrace delay after a control changes
BLUR_SLIDER_MAX = 50  # slider is 0..50 = 0.0..5.0 (×10 fixed-point)
BLUR_SLIDER_SCALE = 10
THRESHOLD_SLIDER_MAX = 255
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0

LOGGER = logging.getLogger(__name__)


class TracePage(BasePage):
    """Image → outline tracing page."""

    _trace_done = Signal(object)  # (display_img, polys, img_w_px, img_h_px, width_mm)
    _trace_error = Signal(object)
    _trace_progress = Signal(int, str)  # (percent, label)
    _trace_cancelled = Signal(int)  # (trace_token)
    sendSelectedToDraftRequested = Signal(object)
    sendSelectedToPatternRequested = Signal(object)
    customTileRequested = Signal(object)

    _MODEL_STATE_FIELDS = {
        "_img_path": "image_path",
        "_running": "running",
        "_trace_pending": "trace_pending",
        "_cancel_event": "cancel_event",
        "_trace_thread": "trace_thread",
        "_shutting_down": "shutting_down",
        "_last_out": "last_output",
        "_last_display_img": "last_display_image",
        "_last_width_mm": "last_width_mm",
        "_last_height_mm": "last_height_mm",
        "_img_w_px": "image_width_px",
        "_img_h_px": "image_height_px",
        "_img_aspect": "image_aspect",
        "_aspect_locked": "aspect_locked",
        "_trace_revision": "trace_revision",
        "_needs_view_fit": "needs_view_fit",
        "_trace_result_stale": "trace_result_stale",
    }

    def __getattr__(self, name: str) -> Any:
        field = self._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            return getattr(model, field)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        field = self._MODEL_STATE_FIELDS.get(name)
        model = self.__dict__.get("_model")
        if field is not None and model is not None:
            setattr(model, field, value)
            return
        super().__setattr__(name, value)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)
        self._model = TraceModel()
        self._img_path: str | None = None
        self._running: bool = False
        self._trace_pending: bool = False
        self._cancel_event = threading.Event()
        self._trace_thread: threading.Thread | None = None
        self._shutting_down = False

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._start_trace_thread)

        self._trace_done.connect(self._handle_trace_done)
        self._trace_error.connect(self._handle_trace_error)
        self._trace_progress.connect(self._on_trace_progress)
        self._trace_cancelled.connect(self._handle_trace_cancelled)
        self._last_out: str | None = None
        self._last_display_img: Image.Image | None = None
        self._last_width_mm: float = 0.0
        self._last_height_mm: float = 0.0
        self._img_w_px: int = 0
        self._img_h_px: int = 0
        self._img_aspect: float = 1.0
        self._aspect_locked: bool = True
        self._trace_revision: int = 0
        # Only the trace right after a *new* image is chosen should
        # re-frame the view — every later retrace while tweaking sliders
        # must leave the user's current zoom/pan alone.
        self._needs_view_fit: bool = True
        self._trace_result_stale: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(12, 12, 12, 12)
        left.setSpacing(8)

        right_w = surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(8)

        sidebar_width = max(260, min(320, int(self._settings.get("trace_sidebar_width", 320))))
        self._left_panel = sidebar_panel(left_w, min_width=260, max_width=320)
        self._splitter = content_splitter(
            self._left_panel,
            right_w,
            sizes=(sidebar_width, 950),
        )
        self._splitter.setCollapsible(0, True)
        self._splitter.set_responsive_secondary(0, "Trace controls")
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.splitterMoved.connect(self._remember_sidebar_width)
        root.addWidget(self._splitter, stretch=1)

        self._build_left(left)
        self._build_right(right)
        self._update_trace_action_states()

        self.setAcceptDrops(True)

    def sizeHint(self) -> QSize:
        """Prefer a width that fits the smallest supported application window."""
        hint = super().sizeHint()
        return QSize(min(900, hint.width()), hint.height())

    def _remember_sidebar_width(self, position: int, _index: int) -> None:
        if position <= 0:
            return
        width = max(260, min(320, position))
        if self._settings.get("trace_sidebar_width") == width:
            return
        self._settings["trace_sidebar_width"] = width
        save_settings(self._settings)

    _IMAGE_EXTENSIONS = (
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tif",
        ".tiff",
        ".gif",
        ".webp",
    )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(self._IMAGE_EXTENSIONS):
                    event.acceptProposedAction()
                    return
        # Qt withholds dropEvent entirely once dragEnterEvent rejects, so
        # this is the only chance to say why.
        if event.mimeData().hasUrls():
            self._set_status("Trace accepts image files (PNG, JPG, etc.)", STATUS_WARN)
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(self._IMAGE_EXTENSIONS):
                self._img_edit.setText(path)
                self._img_path = path
                self._needs_view_fit = True
                self._load_thumbnail(path)
                record_recent(self._settings, KIND_IMAGE, path)
                self._schedule_trace()
                self._emit_state_changed()
                event.acceptProposedAction()
                return
        event.ignore()

    # ── Left panel ────────────────────────────────────────────────────────────

    def _build_left(self, layout: QVBoxLayout) -> None:
        self._init_trace_form_fields()

        # ── Source section ────────────────────────────────────────────────────
        source_content = QWidget()
        source_layout = QVBoxLayout(source_content)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(8)
        self._source_field = PathField(
            "Select image…",
            "Browse",
            self._browse_image,
            tooltip="Path to a raster image file (drag-and-drop supported)",
        )
        self._img_edit = self._source_field.entry
        self._img_edit.editingFinished.connect(self._commit_typed_image_path)
        self._recent_btn = RecentFilesButton(
            self._settings,
            KIND_IMAGE,
            empty_message="No recent images.",
        )
        self._recent_btn.setToolTip("Pick from recently opened images")
        self._recent_btn.fileSelected.connect(self._load_image_from_recent)
        # File path, Browse, and Recent previously competed for one 260 px
        # row, producing the clipped controls in the Trace inspector. Keep
        # source selection sequential: enter/browse first, then choose a
        # recent file as the alternate path.
        source_layout.addWidget(self._source_field)
        source_layout.addWidget(self._recent_btn)
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setMaximumHeight(120)
        self._thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._thumb_lbl.setVisible(False)
        source_layout.addWidget(self._thumb_lbl)
        self._img_info_lbl = QLabel("")
        self._img_info_lbl.setProperty("role", "hint")
        self._img_info_lbl.setWordWrap(True)
        source_layout.addWidget(self._img_info_lbl)
        self._bg_visible_cb = QCheckBox("Show image in background")
        self._bg_visible_cb.setChecked(True)
        self._bg_visible_cb.setToolTip("Display the source image behind the traced outlines")
        self._bg_visible_cb.stateChanged.connect(self._on_bg_visible_changed)
        source_layout.addWidget(self._bg_visible_cb)
        self._source_section = CollapsibleSection("Source", source_content, expanded=True)
        layout.addWidget(self._source_section)

        # ── Trace Settings section ────────────────────────────────────────────
        self._trace_settings_section = build_lazy_section(
            "Trace Settings",
            self._build_essential_fields,
            expanded=True,
        )
        layout.addWidget(self._trace_settings_section)

        # Primary actions sit directly under Trace Settings — they act on
        # the live preview those settings drive.
        action_row = QVBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)
        self._reload_btn = QPushButton("Refresh Preview")
        self._reload_btn.setProperty("role", "primary")
        self._reload_btn.setToolTip("Retrace immediately with the current settings.")
        self._reload_btn.clicked.connect(self._force_reload_trace)
        action_row.addWidget(self._reload_btn)
        self._smooth_btn = QPushButton("Smooth Curves…")
        self._smooth_btn.setProperty("role", "secondary")
        self._smooth_btn.setToolTip(
            "Fit every traced outline to a smooth bezier curve, rounding "
            "off pixel-staircase noise while keeping real corners sharp.\n"
            "Applies once to the current trace — tweak a setting and "
            "retrace to start over from the raw outline. Undo (Ctrl+Z) "
            "reverts it."
        )
        self._smooth_btn.clicked.connect(self._smooth_traced_curves)
        action_row.addWidget(self._smooth_btn)
        layout.addLayout(action_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)  # only shown while tracing
        layout.addWidget(self._progress)

        # ── Advanced section ──────────────────────────────────────────────────
        advanced = build_lazy_section(
            "Advanced",
            self._build_advanced_fields,
            expanded=False,
        )
        layout.addWidget(advanced)

        # ── Export section ────────────────────────────────────────────────────
        export_content = QWidget()
        export_layout = QVBoxLayout(export_content)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(4)
        self._export_all_btn = QPushButton("Export Traced Outlines DXF…")
        self._export_all_btn.setMinimumHeight(36)
        self._export_all_btn.setProperty("role", "primary")
        self._export_all_btn.setToolTip("Save all traced outlines as a DXF file")
        self._export_all_btn.setEnabled(False)
        self._export_all_btn.clicked.connect(self._export_all)
        _export_overflow_btn = QToolButton()
        _export_overflow_btn.setText("Options")
        _export_overflow_btn.setProperty("role", "overflow")
        _export_overflow_btn.setMinimumWidth(72)
        _export_overflow_btn.setMinimumHeight(36)
        _export_overflow_btn.setToolTip("More export options")
        _export_overflow_btn.setAccessibleName("More export options")
        _export_overflow_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        _overflow_menu = QMenu(_export_overflow_btn)
        self._export_sel_action = _overflow_menu.addAction(
            "Export Selected as DXF…", self._export_selected
        )
        self._export_sel_action.setEnabled(False)
        _overflow_menu.addAction("Export Raster Engraving…", self._export_raster_engraving)
        _overflow_menu.addSeparator()
        self._reveal_action = _overflow_menu.addAction(reveal_label(), self._reveal_in_finder)
        self._reveal_action.setEnabled(False)
        _export_overflow_btn.setMenu(_overflow_menu)
        export_row = QHBoxLayout()
        export_row.setContentsMargins(0, 0, 0, 0)
        export_row.setSpacing(4)
        export_row.addWidget(self._export_all_btn, stretch=1)
        export_row.addWidget(_export_overflow_btn)
        export_layout.addLayout(export_row)
        self._next_btn = QPushButton("Next — Edit in Draft")
        self._next_btn.setProperty("role", "primary")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._run_remembered_next)
        self._next_more = QToolButton()
        self._next_more.setText("Options")
        self._next_more.setAccessibleName("Choose trace next action")
        self._next_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        next_menu = QMenu(self._next_more)
        for key, label in (
            ("draft", "Edit in Draft"),
            ("pattern", "Use in Pattern"),
            ("export", "Export DXF"),
        ):
            action = next_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, choice=key: self._select_next_action(choice)
            )
        self._next_more.setMenu(next_menu)
        next_row = QHBoxLayout()
        next_row.setSpacing(4)
        next_row.addWidget(self._next_btn, 1)
        next_row.addWidget(self._next_more)
        export_layout.addLayout(next_row)
        remembered_next = str(self._settings.get("trace_next_action", "draft"))
        self._next_btn.setText(
            {
                "draft": "Next — Edit in Draft",
                "pattern": "Next — Use in Pattern",
                "export": "Next — Export DXF",
            }.get(remembered_next, "Next — Edit in Draft")
        )
        # _build_right reparents this into the bottom of the right inspector.
        self._export_footer = export_content

        layout.addStretch()

    def _init_trace_form_fields(self) -> None:
        self._blur = self._mk_entry(
            trace_default(self._settings, "blur"),
            "Gaussian blur radius applied before thresholding / edge detection",
            on_change=self._on_blur_text,
        )
        self._thresh_entry = self._mk_entry(
            trace_default(self._settings, "threshold"),
            "Brightness cutoff: pixels darker than this become outlines",
            on_change=self._on_thresh_text,
        )
        self._canny_low = self._mk_entry(
            trace_default(self._settings, "canny_low"),
            "Lower hysteresis threshold for Canny edge detection.\nEdges below this value are discarded.",
        )
        self._canny_high = self._mk_entry(
            trace_default(self._settings, "canny_high"),
            "Upper hysteresis threshold for Canny edge detection.\nEdges above this value are always kept.",
        )
        self._simplify = self._mk_entry(
            trace_default(self._settings, "simplify"),
            "Tolerance for polygon simplification (higher = fewer points)",
        )
        self._min_area = self._mk_entry(
            trace_default(self._settings, "min_area"),
            "Discard contours smaller than this area",
        )
        self._max_area = self._mk_entry(
            trace_default(self._settings, "max_area"),
            "Discard contours larger than this area (leave empty for no limit)",
            placeholder="none",
        )
        self._close_r = self._mk_entry(
            trace_default(self._settings, "close_r"),
            "Morphological closing to fill small gaps in edges",
        )
        self._width_mm = self._mk_entry(
            trace_default(self._settings, "width_mm"),
            "Target output width in millimetres",
            on_change=self._on_width_changed,
        )
        self._height_mm = self._mk_entry(
            "",
            "Target output height in millimetres",
            on_change=self._on_height_changed,
            placeholder="auto",
        )
        self._width_mm.blockSignals(True)
        self._width_mm.setText(f"{float(self._width_mm.text() or 50.0):.2f}")
        self._width_mm.blockSignals(False)
        self._max_res = self._mk_entry(
            trace_default(self._settings, "max_res"),
            "Maximum pixel dimension when loading the image.\nHigher values give finer detail but are slower.",
        )

        self._edge_mode_cb = QCheckBox("Edge mode  (line art / Canny)")
        self._edge_mode_cb.setToolTip(
            "Use Canny edge detection instead of threshold masking.\n"
            "Better for sketches, line drawings, and images with thin strokes."
        )
        self._edge_mode_cb.stateChanged.connect(self._on_edge_mode_changed)

        self._auto_thresh_cb = QCheckBox("Auto threshold (Otsu)")
        self._auto_thresh_cb.setChecked(True)
        self._auto_thresh_cb.setToolTip(
            "Automatically select the best threshold using Otsu's method.\n"
            "Uncheck to set a manual threshold value."
        )
        self._auto_thresh_cb.stateChanged.connect(self._on_auto_thresh_changed)

        self._invert_cb = QCheckBox("Invert  (dark background → light foreground)")
        self._invert_cb.setToolTip("Swap foreground/background before tracing")
        self._invert_cb.stateChanged.connect(self._schedule_trace)

        self._outer_only_cb = QCheckBox("Outer contours only (skip holes)")
        self._outer_only_cb.setChecked(False)
        self._outer_only_cb.setToolTip(
            "Only extract the outermost outlines of shapes, discarding\n"
            "interior holes — e.g. the counters inside letters A, B, O, a,\n"
            "p, d. Leave unchecked to trace lettering faithfully."
        )
        self._outer_only_cb.stateChanged.connect(self._schedule_trace)

        self._lock_cb = QCheckBox("Lock aspect ratio")
        self._lock_cb.setChecked(True)
        self._lock_cb.setToolTip("Keep width and height proportional when resizing")
        self._lock_cb.stateChanged.connect(self._on_aspect_lock_changed)

        self._blur_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self._blur_slider.setRange(0, BLUR_SLIDER_MAX)
        # Initial position derives from the field default (TRACE_DEFAULTS or
        # the user's Settings override) so slider and text never start out
        # of sync.
        try:
            blur_default = float(trace_default(self._settings, "blur"))
        except ValueError:
            blur_default = 0.0
        self._blur_slider.setValue(int(blur_default * BLUR_SLIDER_SCALE))
        self._blur_slider.setToolTip("Drag to adjust the blur radius (0.0 – 5.0)")
        self._blur_slider.valueChanged.connect(self._on_blur_slider)

        self._thresh_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self._thresh_slider.setRange(0, THRESHOLD_SLIDER_MAX)
        try:
            thresh_default = int(float(trace_default(self._settings, "threshold")))
        except ValueError:
            thresh_default = THRESHOLD_SLIDER_MAX // 2
        self._thresh_slider.setValue(thresh_default)
        self._thresh_slider.setToolTip("Drag to adjust the brightness threshold")
        self._thresh_slider.valueChanged.connect(self._on_thresh_slider)

    def _on_blur_text(self, text: str) -> None:
        try:
            val = float(text)
            if 0.0 <= val <= BLUR_SLIDER_MAX / BLUR_SLIDER_SCALE:
                self._blur_slider.blockSignals(True)
                self._blur_slider.setValue(int(val * BLUR_SLIDER_SCALE))
                self._blur_slider.blockSignals(False)
        except ValueError:
            pass
        self._schedule_trace()

    def _on_blur_slider(self, value: int) -> None:
        self._blur.blockSignals(True)
        self._blur.setText(f"{value / BLUR_SLIDER_SCALE:.1f}")
        self._blur.blockSignals(False)
        self._schedule_trace()

    def _mk_entry(
        self,
        default: str,
        tooltip: str,
        *,
        placeholder: str = "",
        on_change=None,
    ) -> QLineEdit:
        entry = QLineEdit(default)
        make_resettable_line_edit(entry, default)
        entry.setToolTip(tooltip)
        entry.setPlaceholderText(placeholder)
        if on_change is None:
            entry.textChanged.connect(self._schedule_trace)
        else:
            entry.textChanged.connect(on_change)
        return entry

    def _build_essential_fields(self, layout: QVBoxLayout) -> None:
        detection_label = QLabel("Detection")
        detection_label.setProperty("role", "section-label")
        layout.addWidget(detection_label)
        layout.addWidget(
            TextField(
                "Blur radius",
                entry=self._blur,
                tooltip=self._blur.toolTip(),
            )
        )
        layout.addWidget(self._blur_slider)
        layout.addWidget(self._edge_mode_cb)

        self._thresh_widget = QWidget()
        tw_layout = QVBoxLayout(self._thresh_widget)
        tw_layout.setContentsMargins(0, 0, 0, 0)
        tw_layout.setSpacing(4)
        tw_layout.addWidget(self._auto_thresh_cb)
        tw_layout.addWidget(
            TextField(
                "Threshold (0-255)",
                entry=self._thresh_entry,
                tooltip=self._thresh_entry.toolTip(),
            )
        )
        tw_layout.addWidget(self._thresh_slider)
        tw_layout.addWidget(self._invert_cb)
        layout.addWidget(self._thresh_widget)

        self._canny_widget = QWidget()
        self._canny_widget.setVisible(False)
        cw_layout = QVBoxLayout(self._canny_widget)
        cw_layout.setContentsMargins(0, 0, 0, 0)
        cw_layout.setSpacing(4)
        cw_layout.addWidget(
            TextField("Canny low", entry=self._canny_low, tooltip=self._canny_low.toolTip())
        )
        cw_layout.addWidget(
            TextField(
                "Canny high",
                entry=self._canny_high,
                tooltip=self._canny_high.toolTip(),
            )
        )
        layout.addWidget(self._canny_widget)

        size_label = QLabel("Output size")
        size_label.setProperty("role", "section-label")
        layout.addWidget(size_label)
        layout.addWidget(
            TextField("Width (mm)", entry=self._width_mm, tooltip=self._width_mm.toolTip())
        )
        layout.addWidget(
            TextField(
                "Height (mm)",
                entry=self._height_mm,
                required=False,
                tooltip=self._height_mm.toolTip(),
            )
        )
        layout.addWidget(self._lock_cb)
        self._size_info_lbl = QLabel("")
        self._size_info_lbl.setProperty("role", "hint-sm")
        self._size_info_lbl.setWordWrap(True)
        layout.addWidget(self._size_info_lbl)
        self._update_thresh_controls()

    def _build_advanced_fields(self, layout: QVBoxLayout) -> None:
        layout.addWidget(
            SliderField(
                "Simplify (px)",
                entry=self._simplify,
                minimum=0,
                maximum=10,
                step=0.1,
                tooltip=self._simplify.toolTip(),
            )
        )
        layout.addWidget(
            SliderField(
                "Min area (px²)",
                entry=self._min_area,
                minimum=0,
                maximum=1000,
                tooltip=self._min_area.toolTip(),
            )
        )
        layout.addWidget(
            SliderField(
                "Max area (px²)",
                entry=self._max_area,
                minimum=0,
                maximum=1_000_000,
                step=100,
                empty_at_minimum=True,
                tooltip=self._max_area.toolTip(),
            )
        )
        layout.addWidget(
            SliderField(
                "Closing radius",
                entry=self._close_r,
                minimum=0,
                maximum=20,
                tooltip=self._close_r.toolTip(),
            )
        )
        layout.addWidget(self._outer_only_cb)
        layout.addWidget(
            SliderField(
                "Max resolution",
                entry=self._max_res,
                minimum=64,
                maximum=8000,
                step=16,
                tooltip=self._max_res.toolTip(),
            )
        )

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_right(self, layout: QVBoxLayout) -> None:
        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_geometry_change,
            on_send_selected_to_draft=self._on_send_selected_to_draft,
            on_send_selected_to_pattern=self._on_send_selected_to_pattern,
            on_use_selected_as_custom_tile=self.customTileRequested.emit,
            draft_profile=True,
        )
        self._canvas.set_context_menu_profile("trace")
        self._canvas.set_context_menu_profiles(self._settings.get("context_menu_profiles", {}))
        self._canvas.set_empty_message(
            "Start a trace\nOpen an image, adjust cleanup, then export or send the result"
        )
        self._canvas.set_empty_actions(
            [
                ("Open image…", self._browse_image),
                ("Recent images", self._recent_btn.click),
            ]
        )
        self._canvas.set_grid_visible(DEFAULT_GRID_VISIBLE)
        self._canvas.set_grid_snap(False)
        self._canvas.set_grid_spacing(DEFAULT_GRID_SPACING_MM)

        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._canvas.fit,
        )
        layout.addWidget(self._toolbar_module)

        self._grid_module = CanvasGridModule(
            canvas=self._canvas,
            on_changed=self._refresh_canvas_panels,
        )
        layout.addWidget(self._grid_module)
        self._precision_bar = self._grid_module

        # Placed at the bottom of the page (after the splitter) so every
        # canvas page keeps the same anatomy: toolbars up top, canvas in
        # the middle, status strip along the bottom — same as Draft.
        self._canvas_status = CanvasStatusStrip()
        self._canvas_status.set_zoom_callback(self._on_zoom_preset)
        self._canvas_status.bind_canvas(self._canvas)

        canvas_shell = QWidget()
        canvas_layout = QVBoxLayout(canvas_shell)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(8)
        canvas_layout.addWidget(self._canvas, stretch=1)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._layer_module = CanvasLayerTreeModule(
            canvas=self._canvas,
            title="Layers",
            editable=False,
            get_active_layer_name=lambda: "trace_preview",
            build_layer_rows=self._build_layer_tree_rows,
            on_selection_requested=self._on_browser_selection_requested,
            on_fit_requested=self._fit_selection,
            on_visibility_changed=self._refresh_canvas_panels,
        )
        self._layers_tree = self._layer_module.tree
        self._layer_sidebar = self._layer_module.controller
        side_layout.addWidget(self._layer_module, stretch=1)
        side_layout.addWidget(self._export_footer)

        self._canvas_runtime = TraceCanvasPageRuntime(
            canvas=self._canvas,
            toolbar_module=self._toolbar_module,
            layer_sidebar=self._layer_sidebar,
            canvas_status=self._canvas_status,
            precision_bar=self._precision_bar,
            is_running=lambda: self._running,
            has_image=lambda: bool(self._img_path),
        )

        splitter = content_splitter(canvas_shell, side_panel, sizes=(860, 260))
        splitter.set_responsive_secondary(1, "Layers")
        self._canvas_splitter = splitter
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(self._canvas_status)

        self._refresh_canvas_panels()

    def _on_zoom_preset(self, value) -> None:
        if value == "fit":
            self._canvas.fit()
        else:
            self._canvas.set_zoom_percent(float(value))
        self._refresh_canvas_panels()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = STATUS_NEUTRAL) -> None:
        set_status_label(self._status, text, color)

    def _reset_trace_runtime_state(self) -> None:
        self._running = False
        self._trace_pending = False
        self._cancel_event.set()
        self._cancel_event = threading.Event()
        self._last_out = None
        self._last_display_img = None
        self._last_width_mm = 0.0
        self._last_height_mm = 0.0
        self._img_w_px = 0
        self._img_h_px = 0
        self._img_aspect = 1.0
        if hasattr(self, "_img_info_lbl"):
            self._img_info_lbl.setText("")
        if hasattr(self, "_thumb_lbl"):
            self._thumb_lbl.setVisible(False)
        if hasattr(self, "_size_info_lbl"):
            self._size_info_lbl.setText("")
        if hasattr(self, "_progress"):
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress.setVisible(False)

    def _update_trace_action_states(self) -> None:
        has_image = bool(self._img_path or self._last_display_img is not None)
        has_polys = bool(self._canvas.poly_count) if hasattr(self, "_canvas") else False
        has_selection = bool(self._canvas.sel_count) if hasattr(self, "_canvas") else False
        self._bg_visible_cb.setEnabled(has_image)
        self._export_all_btn.setEnabled(has_polys)
        self._export_sel_action.setEnabled(has_selection)
        self._reveal_action.setEnabled(bool(self._last_out))
        self._reload_btn.setEnabled(has_image)
        self._smooth_btn.setEnabled(has_polys)
        self._next_btn.setEnabled(has_polys)
        self._next_more.setEnabled(has_polys)

    def _parse_float_field(
        self,
        entry: QLineEdit,
        label: str,
        **kw,
    ) -> float | None:
        return parse_float_field_with_feedback(entry, label, self._set_status, **kw)

    def _on_sel_change(self, count: int) -> None:
        if hasattr(self, "_canvas_runtime"):
            self._canvas_runtime.on_selection_change(count)
        if count == 0:
            self._refresh_canvas_panels()
        self._update_trace_action_states()

    # ── Image loading ─────────────────────────────────────────────────────────

    def _browse_image(self) -> None:
        path = pick_open_file(
            self,
            self._settings,
            "trace_image",
            "Select image",
            "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp);;All files (*)",
            recent_kind=KIND_IMAGE,
        )
        if path:
            self._img_edit.setText(path)
            self._img_path = path
            self._needs_view_fit = True
            self._load_thumbnail(path)
            self._schedule_trace()
            self._emit_state_changed()

    def _load_image_from_recent(self, path: str) -> None:
        self._img_edit.setText(path)
        self._img_path = path
        self._needs_view_fit = True
        self._load_thumbnail(path)
        record_recent(self._settings, KIND_IMAGE, path)
        self._schedule_trace()
        self._emit_state_changed()

    def _commit_typed_image_path(self) -> None:
        path = self._img_edit.text().strip()
        if not path or path == self._img_path:
            return
        candidate = Path(path).expanduser()
        if not candidate.is_file() or candidate.suffix.casefold() not in self._IMAGE_EXTENSIONS:
            self._set_status("Choose an existing supported image file.", STATUS_ERR)
            return
        self._load_image_from_recent(str(candidate))

    def has_workspace_content(self) -> bool:
        return bool(self._img_edit.text().strip() or self._canvas.poly_count)

    def _load_thumbnail(self, path: str) -> None:
        try:
            with Image.open(path) as src:
                self._img_w_px = src.width
                self._img_h_px = src.height
                self._img_aspect = src.width / max(src.height, 1)
                thumb = src.copy()
                thumb.thumbnail((280, 120), Image.Resampling.LANCZOS)
                if thumb.mode != "RGB":
                    thumb = thumb.convert("RGB")
            data = thumb.tobytes("raw", "RGB")
            qimg = QImage(
                data,
                thumb.width,
                thumb.height,
                thumb.width * 3,
                QImage.Format.Format_RGB888,
            )
            self._thumb_lbl.setPixmap(
                QPixmap.fromImage(qimg).scaledToHeight(
                    min(120, thumb.height),
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._thumb_lbl.setVisible(True)
            self._img_info_lbl.setText(
                f"{Path(path).name}  ·  {self._img_w_px}×{self._img_h_px} px"
            )
            self._update_height_from_width()
            self._set_status("Image loaded — adjust settings to trace outlines.")
            self._update_trace_action_states()
        except (OSError, ValueError) as exc:
            self._reset_trace_runtime_state()
            self._img_path = None
            self._img_edit.setText("")
            self._img_info_lbl.setText("")
            self._update_trace_action_states()
            QMessageBox.warning(self, "Image Error", f"Could not load image:\n{exc}")

    def _update_height_from_width(self) -> None:
        if self._img_aspect <= 0:
            return
        try:
            w = float(self._width_mm.text() or "50.0")
            h = w / self._img_aspect
            self._height_mm.blockSignals(True)
            self._height_mm.setText(f"{h:.2f}")
            self._height_mm.blockSignals(False)
            if self._img_w_px and self._img_h_px:
                self._size_info_lbl.setText(
                    f"{self._img_w_px}×{self._img_h_px} px → {w:.2f}×{h:.2f} mm"
                )
        except ValueError:
            pass

    def _on_aspect_lock_changed(self, state: int) -> None:
        self._aspect_locked = bool(state)
        if self._aspect_locked:
            self._update_height_from_width()

    def _on_width_changed(self, *_) -> None:
        if self._aspect_locked:
            self._update_height_from_width()
        self._schedule_trace()

    def _on_height_changed(self, *_) -> None:
        if self._aspect_locked and self._img_aspect > 0:
            try:
                h = float(self._height_mm.text() or "0")
                w = h * self._img_aspect
                self._width_mm.blockSignals(True)
                self._width_mm.setText(f"{w:.2f}")
                self._width_mm.blockSignals(False)
            except ValueError:
                pass
        self._schedule_trace()

    # ── Tracing ───────────────────────────────────────────────────────────────

    def _schedule_trace(self, *_) -> None:
        if self._suspend_state:
            return
        if not self._img_path:
            return
        self._trace_revision += 1
        self._trace_result_stale = bool(self._canvas.poly_count)
        if self._trace_result_stale:
            self._canvas.setToolTip("Updating trace — the last completed result remains visible")
        if self._running:
            self._trace_pending = True
            self._cancel_event.set()
        self._set_status("Preview out of date — refreshing…", STATUS_WARN)
        self._preview_timer.start(TRACE_DEBOUNCE_MS)
        self._emit_state_changed()

    def _force_reload_trace(self) -> None:
        """Manual escape hatch: bypass the live-preview debounce and any
        stuck in-flight/cancelled state, then start a fresh retrace right
        away with the current settings."""
        if self._running:
            self._trace_revision += 1
            self._trace_pending = False
            self._cancel_event.set()
            self._set_status("Cancelling trace…")
            return
        if not self._img_path:
            self._set_status("No image loaded.", STATUS_ERR)
            return
        self._trace_revision += 1
        self._cancel_event.set()
        self._preview_timer.stop()
        self._running = False
        self._trace_pending = False
        self._start_trace_thread()

    def _smooth_traced_curves(self) -> None:
        """Fit every traced outline to a smooth bezier curve, on demand.

        Deliberately manual/one-shot rather than automatic on every live-
        preview retrace: an earlier attempt at auto-smoothing on every
        settings tweak ran this synchronously on the GUI thread after each
        retrace (causing UI freezes) and used a single fixed tolerance
        regardless of the image (producing a triangulated mess on complex
        shapes). Now that the raw trace itself is far cleaner (mask
        supersampling + illumination-corrected thresholding), a one-shot
        manual pass at a user-chosen tolerance gives much better results,
        and Ctrl+Z reverts it if it doesn't look right.
        """
        if not self._canvas.get_polylines_state():
            self._set_status("Nothing to smooth yet — trace an image first.", STATUS_ERR)
            return

        def apply_smoothing(tolerance: float) -> None:
            self._canvas.select_all()
            count = self._canvas.fit_selected_to_curve(tolerance)
            self._canvas.deselect_all()
            if count:
                self._set_status(f"Smoothed {count} shape(s).", STATUS_OK)
            else:
                self._set_status("Nothing could be smoothed.", STATUS_ERR)

        self._canvas._show_hud_prompt(
            "Smooth tolerance (mm) · Enter applies · Esc cancels",
            0.3,
            apply_smoothing,
            minimum=0.01,
            maximum=10.0,
        )

    def _start_trace_thread(self) -> None:
        if self._shutting_down:
            return
        if not self._img_path:
            return
        if self._running:
            self._trace_pending = True
            return
        fields = TraceFieldBindings(
            blur=self._blur,
            simplify=self._simplify,
            min_area=self._min_area,
            max_area=self._max_area,
            close_r=self._close_r,
            width_mm=self._width_mm,
            max_res=self._max_res,
            threshold=self._thresh_entry,
            canny_low=self._canny_low,
            canny_high=self._canny_high,
            auto_thresh_cb=self._auto_thresh_cb,
            invert_cb=self._invert_cb,
            edge_mode_cb=self._edge_mode_cb,
            outer_only_cb=self._outer_only_cb,
        )
        try:
            kwargs = build_trace_kwargs(
                fields,
                parse_float_field=self._parse_float_field,
                on_progress=lambda pct, lbl: self._trace_progress.emit(pct, lbl),
            )
        except ValueError:
            # _parse_float_field (like every other page's) raises on an
            # invalid/empty field rather than returning None — a field can
            # go transiently empty mid-edit (e.g. select-all then retype)
            # right when the debounce timer fires. The status bar already
            # got the error message from parse_float_field_with_feedback;
            # just skip this retrace instead of crashing the app.
            return
        if kwargs is None:
            return

        self._running = True
        self._trace_pending = False
        # Cancel any in-flight worker BEFORE swapping out the event so its
        # reference (captured by the worker thread) is signalled. The new
        # event is freshly unset and only the new worker thread observes it.
        old_event = self._cancel_event
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        old_event.set()
        trace_token = self._trace_revision
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # indeterminate
        self._set_status("Tracing…")
        self._reload_btn.setText("Cancel Trace")
        self._reload_btn.setToolTip("Stop the trace and keep the last completed result")
        self._trace_thread = threading.Thread(
            target=self._run_trace,
            args=(self._img_path, kwargs, trace_token, cancel_event),
            daemon=True,
        )
        self._trace_thread.start()

    def _run_trace(
        self,
        img_path: str | None,
        kwargs: dict,
        trace_token: int,
        cancel_event: threading.Event | None = None,
    ) -> None:
        def emit(signal, payload) -> bool:
            """Ignore a late worker result after Qt has destroyed the page."""
            try:
                signal.emit(payload)
            except RuntimeError:
                return False
            return True

        outcome = run_trace_job(
            img_path,
            kwargs,
            trace_token,
            cancel_event,
            trace_pipeline=image_to_outlines,
        )
        if outcome.cancelled:
            emit(self._trace_cancelled, trace_token)
        elif outcome.error is not None:
            emit(self._trace_error, (trace_token, outcome.error))
        elif outcome.result is not None:
            emit(self._trace_done, (trace_token, *outcome.result, kwargs["width_mm"]))

    def _handle_trace_done(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        trace_token, _display_img, polys, img_w_px, img_h_px, width_mm_val = payload
        if trace_token != self._trace_revision:
            return
        width_mm_val = float(width_mm_val)
        height_mm_val = img_h_px / max(img_w_px, 1) * width_mm_val
        count = len(polys)
        diagnostics = analyze_geometry(polys)

        self._running = False
        self._reload_btn.setText("Refresh Preview")
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._canvas.set_image_bounds(width_mm_val, height_mm_val)
        self._last_display_img = _display_img
        self._last_width_mm = width_mm_val
        self._last_height_mm = height_mm_val
        if hasattr(self, "_trace_settings_section"):
            self._trace_settings_section.set_subtitle(
                f"{count} contour(s) · {width_mm_val:.0f}×{height_mm_val:.0f} mm"
            )
        if _display_img is not None and self._bg_visible_cb.isChecked():
            try:
                bg_layer = Image.new("RGB", _display_img.size, TRACE_BG_COLOR)
                faded = Image.blend(
                    _display_img.convert("RGB"),
                    bg_layer,
                    TRACE_BG_BLEND_ALPHA,
                )
                self._canvas.set_background_image(faded, width_mm_val, height_mm_val)
            except (OSError, ValueError) as exc:
                LOGGER.debug("Failed to apply traced background image: %s", exc)
        if polys:
            self._canvas.set_polylines_state(polys, fit=self._needs_view_fit)
            self._trace_result_stale = False
            self._canvas.setToolTip("")
            self._needs_view_fit = False
            self._source_section.set_expanded(False)
            self._thumb_lbl.setMaximumHeight(64)
            self._set_status(
                f"{count} contour(s) extracted  ·  "
                f"{img_w_px}×{img_h_px} px → "
                f"{width_mm_val:.2f}×{height_mm_val:.2f} mm · "
                f"{sum(len(poly) for poly in polys)} vertices · "
                f"{diagnostics.closed} closed/{diagnostics.open} open · "
                f"{diagnostics.tiny_paths} tiny",
                STATUS_OK,
            )
        else:
            self._set_status(
                "No foreground contours found; the previous result is retained. "
                "Try Invert or disable Auto threshold, then retry.",
                STATUS_ERR,
            )
        self._update_trace_action_states()
        if self._trace_pending and self._img_path:
            self._trace_pending = False
            self._preview_timer.start(0)

    def _handle_trace_error(self, payload: tuple) -> None:
        if self._shutting_down:
            return
        trace_token, msg = payload
        if trace_token != self._trace_revision:
            return
        self._running = False
        self._reload_btn.setText("Refresh Preview")
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._set_status(self._trace_failure_guidance(msg), STATUS_ERR)
        self._update_trace_action_states()
        if self._trace_pending and self._img_path:
            self._trace_pending = False
            self._preview_timer.start(0)

    @staticmethod
    def _trace_failure_guidance(message: str) -> str:
        text = message.casefold()
        if "memory" in text or "alloc" in text:
            remedy = "Reduce Max resolution and retry."
        elif "foreground" in text or "contour" in text:
            remedy = "Try Invert or adjust Threshold, then retry."
        elif "unsupported" in text or "decode" in text or "image" in text:
            remedy = "Convert the source to PNG or JPEG and choose it again."
        elif "detail" in text or "complex" in text:
            remedy = "Increase Simplify or reduce Max resolution and retry."
        elif "geometry" in text or "invalid" in text:
            remedy = "Increase Min area or Simplify and retry."
        else:
            remedy = "Review Trace Settings and choose Refresh Preview to retry."
        return f"Trace failed; the previous result is retained. {remedy} Details: {message}"

    def _select_next_action(self, choice: str) -> None:
        self._settings["trace_next_action"] = choice
        labels = {
            "draft": "Next — Edit in Draft",
            "pattern": "Next — Use in Pattern",
            "export": "Next — Export DXF",
        }
        self._next_btn.setText(labels.get(choice, labels["draft"]))
        self._run_remembered_next()

    def _run_remembered_next(self) -> None:
        choice = str(self._settings.get("trace_next_action", "draft"))
        if choice == "export":
            self._export_all()
            return
        polys = self._canvas.get_selected() or self._canvas.get_polylines_state()
        if not polys:
            self._set_status("Trace an image before continuing.", STATUS_WARN)
            return
        if choice == "pattern":
            closed = [poly for poly in polys if len(poly) >= 4 and poly[0] == poly[-1]]
            if not closed:
                self._set_status("Pattern needs one or more closed trace outlines.", STATUS_WARN)
                return
            self.sendSelectedToPatternRequested.emit(closed)
        else:
            self.sendSelectedToDraftRequested.emit(polys)

    def _handle_trace_cancelled(self, _trace_token: int) -> None:
        if self._shutting_down:
            return
        """A stale, superseded trace bailed out early instead of running to
        completion (see cancel_check in image_to_outlines). This still has
        to reset _running and drain _trace_pending exactly like the done/
        error handlers do — without it, _running never clears and every
        future retrace request just silently sets _trace_pending and
        returns forever, since _start_trace_thread refuses to start a new
        thread while _running is (permanently, wrongly) True. That's the
        actual "stops updating after enough rapid changes" freeze.
        """
        self._running = False
        self._reload_btn.setText("Refresh Preview")
        self._set_status("Trace cancelled; previous result retained.")
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        if self._trace_pending and self._img_path:
            self._trace_pending = False
            self._preview_timer.start(0)

    # ── Canvas actions ────────────────────────────────────────────────────────

    def _on_auto_thresh_changed(self, _state: int) -> None:
        self._update_thresh_controls()
        self._schedule_trace()

    def _update_thresh_controls(self) -> None:
        manual = not self._auto_thresh_cb.isChecked()
        self._thresh_entry.setEnabled(manual)
        self._thresh_slider.setEnabled(manual)

    def _on_edge_mode_changed(self) -> None:
        edge = self._edge_mode_cb.isChecked()
        self._thresh_widget.setVisible(not edge)
        self._canny_widget.setVisible(edge)
        self._schedule_trace()

    def _on_trace_progress(self, percent: int, label: str) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(percent)
        if percent < 100:
            self._set_status(label)

    def _on_thresh_text(self, text: str) -> None:
        """Sync the slider to match the text field value, then retrace."""
        try:
            val = int(text)
        except (ValueError, TypeError):
            self._schedule_trace()
            return
        val = max(0, min(255, val))
        self._thresh_slider.blockSignals(True)
        self._thresh_slider.setValue(val)
        self._thresh_slider.blockSignals(False)
        self._schedule_trace()

    def _on_thresh_slider(self, value: int) -> None:
        self._thresh_entry.blockSignals(True)
        self._thresh_entry.setText(str(value))
        self._thresh_entry.blockSignals(False)
        self._schedule_trace()

    def _on_bg_visible_changed(self, state: int) -> None:
        if state and self._last_display_img is not None:
            try:
                bg_layer = Image.new("RGB", self._last_display_img.size, TRACE_BG_COLOR)
                faded = Image.blend(
                    self._last_display_img.convert("RGB"),
                    bg_layer,
                    TRACE_BG_BLEND_ALPHA,
                )
                self._canvas.set_background_image(faded, self._last_width_mm, self._last_height_mm)
            except (OSError, ValueError) as exc:
                LOGGER.debug("Failed to toggle background image visibility: %s", exc)
        elif not state:
            self._canvas.clear_background_image()
        self._update_trace_action_states()

    def _on_toolbar_mode(self, value: str) -> None:
        self._canvas_runtime.on_toolbar_mode(value)
        self._refresh_canvas_panels()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._canvas_runtime.on_canvas_mode_change(mode)
        self._refresh_canvas_panels()

    def _on_canvas_geometry_change(self) -> None:
        self._refresh_canvas_panels()
        self._emit_state_changed()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        self._canvas_runtime.on_tree_selection_requested(indices)
        self._refresh_canvas_panels()

    def _build_layer_tree_rows(
        self,
        layer_view_state: dict[str, dict[str, set[str]]],
    ) -> list[dict[str, Any]]:
        return self._canvas_runtime.build_layer_tree_rows(layer_view_state)

    def _on_send_selected_to_draft(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if polys:
            self.sendSelectedToDraftRequested.emit(polys)

    def _on_send_selected_to_pattern(
        self,
        polys: list[list[tuple[float, float]]],
    ) -> None:
        if polys:
            self.sendSelectedToPatternRequested.emit(polys)

    def _fit_selection(self) -> None:
        if self._canvas_runtime.fit_selection():
            self._refresh_canvas_panels()

    def _refresh_canvas_panels(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return
        self._canvas_runtime.refresh_canvas_panels()

    def _export_raster_engraving(self) -> None:
        if not self._img_path or not Path(self._img_path).exists():
            QMessageBox.information(self, "Raster Engraving", "Choose an image first.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Raster Engraving")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Preserves grayscale detail as variable laser power. Position and size are stored "
            "in millimetres beside the exported PNG."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()

        def number(value, minimum, maximum, decimals=2, step=1.0):
            field = QDoubleSpinBox()
            field.setRange(minimum, maximum)
            field.setDecimals(decimals)
            field.setSingleStep(step)
            field.setValue(value)
            return field

        x = number(0, -100000, 100000)
        y = number(0, -100000, 100000)
        width = number(max(self._last_width_mm, 100.0), 0.01, 100000)
        height = number(max(self._last_height_mm, 100.0), 0.01, 100000)
        interval = number(0.10, 0.025, 2.0, 3, 0.025)
        min_power = number(0, 0, 100, 1)
        max_power = number(80, 0, 100, 1)
        speed = number(100, 0.1, 10000, 1, 10)
        gamma = number(1, 0.1, 5, 2, 0.05)
        contrast = number(1, 0.1, 5, 2, 0.05)
        brightness = number(1, 0.1, 5, 2, 0.05)
        passes = QSpinBox()
        passes.setRange(1, 100)
        invert = QCheckBox("Invert light and dark")
        for label, field in (
            ("X position (mm)", x),
            ("Y position (mm)", y),
            ("Width (mm)", width),
            ("Height (mm)", height),
            ("Line interval (mm)", interval),
            ("Minimum power (%)", min_power),
            ("Maximum power (%)", max_power),
            ("Gamma / shadow detail", gamma),
            ("Speed (mm/s)", speed),
            ("Contrast", contrast),
            ("Brightness", brightness),
            ("Passes", passes),
        ):
            form.addRow(label, field)
        form.addRow("Tone", invert)
        layout.addLayout(form)
        warning = QLabel(
            "Depth cannot be predicted from an image alone. Test power, speed, interval, and "
            "passes on scrap material before engraving the final workpiece."
        )
        warning.setWordWrap(True)
        warning.setProperty("role", "status-warn")
        layout.addWidget(warning)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        out = pick_save_file(
            self,
            self._settings,
            "raster_output",
            "Export raster engraving",
            f"{Path(self._img_path).stem}_engraving.png",
            "PNG image (*.png)",
        )
        if not out:
            return
        try:
            spec = RasterEngravingSpec(
                x_mm=x.value(),
                y_mm=y.value(),
                width_mm=width.value(),
                height_mm=height.value(),
                line_interval_mm=interval.value(),
                min_power_percent=min_power.value(),
                max_power_percent=max_power.value(),
                gamma=gamma.value(),
                contrast=contrast.value(),
                speed_mm_s=speed.value(),
                brightness=brightness.value(),
                passes=passes.value(),
                invert=invert.isChecked(),
            )
            png, metadata, positioned = export_raster_job(self._img_path, out, spec)
            self._last_out = str(png)
            self._reveal_action.setEnabled(True)
            self._set_status(
                f"Raster engraving exported → {png.name} + positioned SVG + settings",
                STATUS_OK,
            )
        except (OSError, ValueError) as exc:
            show_error(self, "Raster Export Error", exc)

    def _reveal_in_finder(self) -> None:
        if self._last_out:
            p = Path(self._last_out)
            if not p.exists():
                QMessageBox.warning(
                    self,
                    "File Not Found",
                    f"The file no longer exists:\n{self._last_out}",
                )
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def _restore_background_from_path(self, path: str) -> None:
        try:
            with Image.open(path) as src:
                display_img = src.convert("RGB")
            bg_layer = Image.new("RGB", display_img.size, TRACE_BG_COLOR)
            faded = Image.blend(display_img, bg_layer, TRACE_BG_BLEND_ALPHA)
            self._last_display_img = display_img
            self._canvas.set_background_image(
                faded,
                self._last_width_mm,
                self._last_height_mm,
            )
        except (OSError, ValueError) as exc:
            LOGGER.debug("Failed to restore background image from path '%s': %s", path, exc)
            self._canvas.clear_background_image()

    def shutdown(self) -> None:
        """Called by ``App.closeEvent`` before the window tears down.

        Signal the in-flight trace worker (if any) to stop and give it a
        short window to actually exit, instead of leaving it to run to
        completion (or crash) against a page that's already being destroyed.
        """
        self._shutting_down = True
        self._preview_timer.stop()
        self._trace_revision += 1
        self._cancel_event.set()
        self.blockSignals(True)
        if self._trace_thread is not None and self._trace_thread.is_alive():
            self._trace_thread.join(timeout=2.0)

    def get_workspace_state(self) -> dict:
        return get_trace_workspace_state(self)

    def apply_workspace_state(self, state: dict | None) -> None:
        apply_trace_workspace_state(self, state)

    def clear_workspace_state(self) -> None:
        clear_trace_workspace_state(self)


# Keep TracePage's established private action methods as the UI connection and
# test patch surface; DXF workflow implementation lives with the Trace feature.
TracePage._get_save_path = _get_save_path
TracePage._export_all = _export_all
TracePage._export_selected = _export_selected
