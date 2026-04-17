"""Image to Outline tab."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.constants import DIM, SEL
from src.backend.document.graph import DocumentGraph
from src.backend.document.migration import graph_from_polylines, polylines_from_graph
from src.backend.dxf.io import write_polylines_dxf
from src.backend.io import image_to_outlines
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components.action_maps import IMAGE_ACTION_MAP
from src.ui.components.containers import (
    CanvasObjectBrowser,
    CanvasPrecisionBar,
    CanvasStatusStrip,
    DxfLayersTree,
)
from src.ui.components.factories import (
    _canvas_toolbar,
    _content_splitter,
    _section_label,
    _sidebar_panel,
    _surface_frame,
    clear_line_edit_error,
    parse_float_field,
    set_line_edit_error,
)
from src.ui.components.trace_form import (
    PathField,
    TextField,
    TraceFieldBindings,
    build_lazy_section,
    build_trace_kwargs,
)

ACTION_MAP = IMAGE_ACTION_MAP
TRACE_BG_COLOR = (0x16, 0x21, 0x3E)
TRACE_BG_BLEND_ALPHA = 0.7

LOGGER = logging.getLogger(__name__)


class ImageTab(QWidget):
    """Image → outline tracing tab."""

    _trace_done = Signal(object)  # (display_img, polys, img_w_px, img_h_px, width_mm)
    _trace_error = Signal(object)
    _trace_progress = Signal(int, str)  # (percent, label)
    stateChanged = Signal()
    sendSelectedToDraftRequested = Signal(object)
    sendSelectedToPatternRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._img_path: str | None = None
        self._running: bool = False
        self._trace_pending: bool = False
        self._cancel_event = threading.Event()
        self._suspend_state_changes: bool = False

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._start_trace_thread)

        self._trace_done.connect(self._handle_trace_done)
        self._trace_error.connect(self._handle_trace_error)
        self._trace_progress.connect(self._on_trace_progress)
        self._last_out: str | None = None
        self._last_display_img = None
        self._last_width_mm: float = 0.0
        self._last_height_mm: float = 0.0
        self._img_w_px: int = 0
        self._img_h_px: int = 0
        self._img_aspect: float = 1.0
        self._aspect_locked: bool = True
        self._trace_revision: int = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(12, 10, 12, 10)
        left.setSpacing(6)

        right_w = _surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

        self._left_panel = _sidebar_panel(left_w, min_width=320, max_width=360)
        self._splitter = _content_splitter(
            self._left_panel,
            right_w,
            sizes=(320, 950),
        )
        root.addWidget(self._splitter)

        self._build_left(left)
        self._build_right(right)
        self._update_trace_action_states()

        self.setAcceptDrops(True)

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
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(self._IMAGE_EXTENSIONS):
                self._img_edit.setText(path)
                self._img_path = path
                self._load_thumbnail(path)
                self._schedule_trace()
                self._emit_state_changed()
                event.acceptProposedAction()
                return
        event.ignore()

    # ── Left panel ────────────────────────────────────────────────────────────

    def _build_left(self, layout: QVBoxLayout) -> None:
        self._init_trace_form_fields()

        _section_label(layout, "Source")
        self._source_field = PathField(
            "Select image…",
            "Browse",
            self._browse_image,
            tooltip="Path to a raster image file (drag-and-drop supported)",
        )
        self._img_edit = self._source_field.entry
        layout.addWidget(self._source_field)

        self._img_info_lbl = QLabel("")
        self._img_info_lbl.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        layout.addWidget(self._img_info_lbl)

        essentials = build_lazy_section(
            "Essential Fields",
            self._build_essential_fields,
            expanded=True,
        )
        layout.addWidget(essentials)

        advanced = build_lazy_section(
            "Advanced Fields",
            self._build_advanced_fields,
            expanded=False,
        )
        layout.addWidget(advanced)

        self._status = QLabel("No image loaded.")
        self._status.setStyleSheet(f"color: {DIM};")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._bg_visible_cb = QCheckBox("Show image in background")
        self._bg_visible_cb.setChecked(True)
        self._bg_visible_cb.setToolTip(
            "Display the source image behind the traced outlines"
        )
        self._bg_visible_cb.stateChanged.connect(self._on_bg_visible_changed)
        layout.addWidget(self._bg_visible_cb)

        _section_label(layout, "Export")
        self._export_all_btn = QPushButton("Export All as DXF…")
        self._export_all_btn.setMinimumHeight(36)
        self._export_all_btn.setProperty("role", "primary")
        self._export_all_btn.setToolTip("Save all traced outlines as a DXF file")
        self._export_all_btn.setEnabled(False)
        self._export_all_btn.clicked.connect(self._export_all)
        layout.addWidget(self._export_all_btn)

        self._export_sel_btn = QPushButton("Export Selected as DXF…")
        self._export_sel_btn.setMinimumHeight(36)
        self._export_sel_btn.setToolTip("Save only the selected outlines as a DXF file")
        self._export_sel_btn.setEnabled(False)
        self._export_sel_btn.clicked.connect(self._export_selected)
        layout.addWidget(self._export_sel_btn)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setMinimumHeight(26)
        self._reveal_btn.setToolTip("Open the exported file location in Finder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal_in_finder)
        layout.addWidget(self._reveal_btn)

        layout.addStretch()

    def _init_trace_form_fields(self) -> None:
        self._blur = self._mk_entry(
            "1.5", "Gaussian blur radius applied before thresholding / edge detection"
        )
        self._thresh_entry = self._mk_entry(
            "128",
            "Brightness cutoff: pixels darker than this become outlines",
            on_change=self._on_thresh_text,
        )
        self._canny_low = self._mk_entry(
            "50",
            "Lower hysteresis threshold for Canny edge detection.\nEdges below this value are discarded.",
        )
        self._canny_high = self._mk_entry(
            "150",
            "Upper hysteresis threshold for Canny edge detection.\nEdges above this value are always kept.",
        )
        self._simplify = self._mk_entry(
            "2.0", "Tolerance for polygon simplification (higher = fewer points)"
        )
        self._min_area = self._mk_entry(
            "100", "Discard contours smaller than this area"
        )
        self._max_area = self._mk_entry(
            "",
            "Discard contours larger than this area (leave empty for no limit)",
            placeholder="none",
        )
        self._close_r = self._mk_entry(
            "1", "Morphological closing to fill small gaps in edges"
        )
        self._width_mm = self._mk_entry(
            "50.0",
            "Target output width in millimetres",
            on_change=self._on_width_changed,
        )
        self._height_mm = self._mk_entry(
            "---",
            "Target output height in millimetres",
            on_change=self._on_height_changed,
        )
        self._max_res = self._mk_entry(
            "1200",
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

        self._outer_only_cb = QCheckBox("Outer contours only")
        self._outer_only_cb.setChecked(True)
        self._outer_only_cb.setToolTip(
            "Only extract the outermost outlines of shapes.\n"
            "Prevents inner holes (e.g. inside letters A, B, O) from\n"
            "appearing as extra separate outlines."
        )
        self._outer_only_cb.stateChanged.connect(self._schedule_trace)

        self._lock_cb = QCheckBox("Lock aspect ratio")
        self._lock_cb.setChecked(True)
        self._lock_cb.setToolTip("Keep width and height proportional when resizing")
        self._lock_cb.stateChanged.connect(self._on_aspect_lock_changed)

        self._thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self._thresh_slider.setRange(0, 255)
        self._thresh_slider.setValue(128)
        self._thresh_slider.setToolTip("Drag to adjust the brightness threshold")
        self._thresh_slider.valueChanged.connect(self._on_thresh_slider)

    def _mk_entry(
        self,
        default: str,
        tooltip: str,
        *,
        placeholder: str = "",
        on_change=None,
    ) -> QLineEdit:
        entry = QLineEdit(default)
        entry.setToolTip(tooltip)
        entry.setPlaceholderText(placeholder)
        if on_change is None:
            entry.textChanged.connect(self._schedule_trace)
        else:
            entry.textChanged.connect(on_change)
        return entry

    def _build_essential_fields(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Trace")
        layout.addWidget(
            TextField(
                "Blur radius",
                entry=self._blur,
                tooltip=self._blur.toolTip(),
            )
        )
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
            TextField(
                "Canny low", entry=self._canny_low, tooltip=self._canny_low.toolTip()
            )
        )
        cw_layout.addWidget(
            TextField(
                "Canny high",
                entry=self._canny_high,
                tooltip=self._canny_high.toolTip(),
            )
        )
        layout.addWidget(self._canny_widget)

        _section_label(layout, "Scale")
        layout.addWidget(
            TextField(
                "Width (mm)", entry=self._width_mm, tooltip=self._width_mm.toolTip()
            )
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
        self._size_info_lbl.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        layout.addWidget(self._size_info_lbl)
        self._update_thresh_controls()

    def _build_advanced_fields(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Refine")
        layout.addWidget(
            TextField(
                "Simplify (px)", entry=self._simplify, tooltip=self._simplify.toolTip()
            )
        )
        layout.addWidget(
            TextField(
                "Min area (px²)", entry=self._min_area, tooltip=self._min_area.toolTip()
            )
        )
        layout.addWidget(
            TextField(
                "Max area (px²)",
                entry=self._max_area,
                required=False,
                placeholder="none",
                tooltip=self._max_area.toolTip(),
            )
        )
        layout.addWidget(
            TextField(
                "Closing radius", entry=self._close_r, tooltip=self._close_r.toolTip()
            )
        )
        layout.addWidget(self._outer_only_cb)
        _section_label(layout, "Performance")
        layout.addWidget(
            TextField(
                "Max resolution", entry=self._max_res, tooltip=self._max_res.toolTip()
            )
        )

    # ── Right panel ───────────────────────────────────────────────────────────

    def _build_right(self, layout: QVBoxLayout) -> None:
        toolbar, self._mode_btns, self._sel_label = _canvas_toolbar(
            self._on_toolbar_mode,
            lambda: self._canvas.fit(),
        )
        layout.addWidget(toolbar)

        self._precision_bar = CanvasPrecisionBar(
            None, on_changed=self._refresh_canvas_panels
        )
        layout.addWidget(self._precision_bar)

        self._canvas_status = CanvasStatusStrip(show_readiness=False)
        layout.addWidget(self._canvas_status)

        canvas_shell = QWidget()
        canvas_layout = QVBoxLayout(canvas_shell)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(8)

        self._canvas = DxfCanvas(
            selectable=True,
            on_change=self._on_sel_change,
            on_mode_change=self._on_canvas_mode_change,
            on_poly_change=self._on_canvas_geometry_change,
            on_send_selected_to_draft=self._on_send_selected_to_draft,
            on_send_selected_to_pattern=self._on_send_selected_to_pattern,
            draft_profile=True,
        )
        self._canvas.set_grid_visible(True)
        self._canvas.set_grid_snap(False)
        self._canvas.set_grid_spacing(1.0)
        self._precision_bar.bind_canvas(self._canvas)
        canvas_layout.addWidget(self._canvas, stretch=1)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)

        self._object_browser = CanvasObjectBrowser("Extracted Objects")
        self._object_browser.selectionRequested.connect(
            self._on_browser_selection_requested
        )
        self._object_browser.fitRequested.connect(self._fit_selection)
        side_layout.addWidget(self._object_browser, stretch=3)

        self._layers_tree = DxfLayersTree("DXF Layers")
        side_layout.addWidget(self._layers_tree, stretch=2)

        splitter = _content_splitter(canvas_shell, side_panel, sizes=(860, 260))
        layout.addWidget(splitter, stretch=1)
        self._refresh_canvas_panels()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str = DIM) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

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
        if hasattr(self, "_size_info_lbl"):
            self._size_info_lbl.setText("")
        if hasattr(self, "_progress"):
            self._progress.setRange(0, 100)
            self._progress.setValue(0)

    def _update_trace_action_states(self) -> None:
        has_image = bool(self._img_path or self._last_display_img is not None)
        has_polys = bool(self._canvas.poly_count) if hasattr(self, "_canvas") else False
        has_selection = (
            bool(self._canvas.sel_count) if hasattr(self, "_canvas") else False
        )
        self._bg_visible_cb.setEnabled(has_image)
        self._export_all_btn.setEnabled(has_polys)
        self._export_sel_btn.setEnabled(has_selection)
        self._reveal_btn.setEnabled(bool(self._last_out))

    def _emit_state_changed(self) -> None:
        if not self._suspend_state_changes:
            self.stateChanged.emit()

    def _parse_float_field(
        self,
        entry: QLineEdit,
        label: str,
        **kw,
    ) -> float | None:
        try:
            value = parse_float_field(entry.text(), **kw)
        except ValueError as exc:
            message = f"{label} {exc}"
            set_line_edit_error(entry, message)
            self._set_status(message, "#f85149")
            raise ValueError(message) from exc
        clear_line_edit_error(entry)
        return value

    def _on_sel_change(self, count: int) -> None:
        if count:
            self._sel_label.setText(f"{count} selected")
            self._sel_label.setStyleSheet(f"color: {SEL};")
        else:
            self._sel_label.setText("")
            self._sel_label.setStyleSheet(f"color: {DIM};")
            self._refresh_canvas_panels()
        self._update_trace_action_states()

    # ── Image loading ─────────────────────────────────────────────────────────

    def _browse_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select image",
            "",
            "Image files (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All files (*)",
        )
        if path:
            self._img_edit.setText(path)
            self._img_path = path
            self._load_thumbnail(path)
            self._schedule_trace()
            self._emit_state_changed()

    def _load_thumbnail(self, path: str) -> None:
        try:
            with Image.open(path) as src:
                self._img_w_px = src.width
                self._img_h_px = src.height
                self._img_aspect = src.width / max(src.height, 1)
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
                    f"{self._img_w_px}×{self._img_h_px} px → {w:.1f}×{h:.1f} mm"
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
        if self._suspend_state_changes:
            return
        if not self._img_path:
            return
        self._trace_revision += 1
        if self._running:
            self._trace_pending = True
            self._cancel_event.set()
        self._preview_timer.start(220)
        self._emit_state_changed()

    def _start_trace_thread(self) -> None:
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
        kwargs = build_trace_kwargs(
            fields,
            parse_float_field=self._parse_float_field,
            on_progress=lambda pct, lbl: self._trace_progress.emit(pct, lbl),
        )
        if kwargs is None:
            return

        self._running = True
        self._trace_pending = False
        self._cancel_event.set()
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        trace_token = self._trace_revision
        self._progress.setRange(0, 0)  # indeterminate
        self._set_status("Tracing…")
        threading.Thread(
            target=self._run_trace,
            args=(self._img_path, kwargs, trace_token, cancel_event),
            daemon=True,
        ).start()

    def _run_trace(
        self,
        img_path: str | None,
        kwargs: dict,
        trace_token: int,
        cancel_event: threading.Event | None = None,
    ) -> None:
        try:
            if cancel_event and cancel_event.is_set():
                return
            if not img_path:
                raise RuntimeError("No image selected.")
            result = image_to_outlines(img_path, **kwargs)
            if cancel_event and cancel_event.is_set():
                return
            self._trace_done.emit((trace_token, *result, kwargs["width_mm"]))
        except (RuntimeError, OSError, ValueError, TypeError) as exc:
            if cancel_event and cancel_event.is_set():
                return
            self._trace_error.emit((trace_token, str(exc)))

    def _handle_trace_done(self, payload: tuple) -> None:
        trace_token, _display_img, polys, img_w_px, img_h_px, width_mm_val = payload
        if trace_token != self._trace_revision:
            return
        width_mm_val = float(width_mm_val)
        height_mm_val = img_h_px / max(img_w_px, 1) * width_mm_val
        count = len(polys)

        self._running = False
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._canvas.set_image_bounds(width_mm_val, height_mm_val)
        self._last_display_img = _display_img
        self._last_width_mm = width_mm_val
        self._last_height_mm = height_mm_val
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
            self._canvas.load(polys)
            self._set_status(
                f"{count} contour(s) extracted  ·  "
                f"{img_w_px}×{img_h_px} px → "
                f"{width_mm_val:.1f}×{height_mm_val:.1f} mm",
                "#3fb950",
            )
        else:
            self._canvas.load([])
            self._set_status(
                "No contours found. Try adjusting threshold or inverting.",
                "#f85149",
            )
        self._update_trace_action_states()
        if self._trace_pending and self._img_path:
            self._trace_pending = False
            self._preview_timer.start(0)

    def _handle_trace_error(self, payload: tuple) -> None:
        trace_token, msg = payload
        if trace_token != self._trace_revision:
            return
        self._running = False
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._set_status(f"Error: {msg}", "#f85149")
        self._update_trace_action_states()
        if self._trace_pending and self._img_path:
            self._trace_pending = False
            self._preview_timer.start(0)

    # ── Canvas actions ────────────────────────────────────────────────────────

    def _delete_selected(self) -> None:
        n = self._canvas.delete_selected()
        if n:
            self._set_status(f"Deleted {n} polyline(s). Use ↩ Undo to restore.")
            self._refresh_canvas_panels()
            self._emit_state_changed()
        self._update_trace_action_states()

    def _undo_delete(self) -> None:
        if self._canvas.undo_delete():
            self._set_status("Undo: polylines restored.")
            self._refresh_canvas_panels()
            self._emit_state_changed()
        else:
            self._set_status("Nothing to undo.")
        self._update_trace_action_states()

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
                self._canvas.set_background_image(
                    faded, self._last_width_mm, self._last_height_mm
                )
            except (OSError, ValueError) as exc:
                LOGGER.debug("Failed to toggle background image visibility: %s", exc)
        elif not state:
            self._canvas.clear_background_image()
        self._update_trace_action_states()

    def _set_active_mode_btn(self, value: str) -> None:
        v = value.lower()
        for k, b in self._mode_btns.items():
            b.setProperty("active", k.lower() == v)
            b.style().unpolish(b)
            b.style().polish(b)

    def _on_toolbar_mode(self, value: str) -> None:
        self._set_active_mode_btn(value)
        self._canvas.set_mode(value.lower())
        self._refresh_canvas_panels()

    def _on_canvas_mode_change(self, mode: str) -> None:
        self._set_active_mode_btn(mode)
        self._refresh_canvas_panels()

    def _on_canvas_geometry_change(self) -> None:
        self._refresh_canvas_panels()
        self._emit_state_changed()

    def _on_browser_selection_requested(self, indices: list[int]) -> None:
        self._canvas.set_selection(indices)
        self._refresh_canvas_panels()

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
        if self._canvas.fit_selection():
            self._refresh_canvas_panels()

    def _refresh_canvas_panels(self) -> None:
        if not hasattr(self, "_canvas_status"):
            return
        summary = self._canvas.get_status_summary()
        if self._running:
            readiness_text = "Tracing"
            readiness_tone = "warn"
        elif self._canvas.poly_count:
            readiness_text = "Trace ready"
            readiness_tone = "success"
        elif self._img_path:
            readiness_text = "Ready to trace"
            readiness_tone = "accent"
        else:
            readiness_text = "No image"
            readiness_tone = "warn"
        zoom = (
            self._canvas.get_zoom_percent()
            if hasattr(self._canvas, "get_zoom_percent")
            else 100
        )
        cursor = (
            self._canvas.get_cursor_world_pos()
            if hasattr(self._canvas, "get_cursor_world_pos")
            else None
        )
        self._canvas_status.set_snapshot(
            mode=str(summary["mode"]),
            selected_count=self._canvas.sel_count,
            object_count=self._canvas.poly_count,
            precision_text=str(summary["precision"]),
            readiness_text=readiness_text,
            readiness_tone=readiness_tone,
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        if hasattr(self, "_precision_bar"):
            self._precision_bar.refresh()

        if hasattr(self, "_object_browser"):
            self._object_browser.set_objects(
                self._canvas.get_polylines_state(),
                self._canvas.get_selection_indices(),
            )

        if hasattr(self, "_layers_tree"):
            rows = [("trace_preview", self._canvas.poly_count, False, True)]
            self._layers_tree.set_layers(rows)

    # ── Export ────────────────────────────────────────────────────────────────

    def _get_save_path(self, title: str) -> str | None:
        stem = Path(self._img_path).stem if self._img_path else "outline"
        path, _ = QFileDialog.getSaveFileName(
            self,
            title,
            f"{stem}_outline.dxf",
            "DXF files (*.dxf);;All files (*)",
        )
        return path or None

    def _export_all(self) -> None:
        polys = self._canvas.get_polylines_state()
        if not polys:
            QMessageBox.critical(self, "Export", "No polylines to export.")
            return
        out = self._get_save_path("Export all outlines as DXF")
        if not out:
            return
        try:
            write_polylines_dxf(polys, out, close=True)
            self._last_out = out
            self._reveal_btn.setEnabled(True)
            self._set_status(
                f"Exported {len(polys)} polylines → {Path(out).name}", "#3fb950"
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _export_selected(self) -> None:
        polys = self._canvas.get_selected()
        if not polys:
            QMessageBox.information(self, "Export Selected", "Nothing is selected.")
            return
        out = self._get_save_path("Export selected outlines as DXF")
        if not out:
            return
        try:
            write_polylines_dxf(polys, out, close=True)
            self._last_out = out
            self._reveal_btn.setEnabled(True)
            self._set_status(
                f"Exported {len(polys)} selected polylines → {Path(out).name}",
                "#3fb950",
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

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
            LOGGER.debug(
                "Failed to restore background image from path '%s': %s", path, exc
            )
            self._canvas.clear_background_image()

    def get_workspace_state(self) -> dict:
        doc_graph = graph_from_polylines(
            self._canvas.get_polylines_state(),
            layer="trace_preview",
            as_segments=False,
        )
        return {
            "image_path": self._img_path or self._img_edit.text(),
            "blur": self._blur.text(),
            "threshold": self._thresh_entry.text(),
            "auto_threshold": self._auto_thresh_cb.isChecked(),
            "invert": self._invert_cb.isChecked(),
            "edge_mode": self._edge_mode_cb.isChecked(),
            "canny_low": self._canny_low.text(),
            "canny_high": self._canny_high.text(),
            "outer_only": self._outer_only_cb.isChecked(),
            "simplify": self._simplify.text(),
            "min_area": self._min_area.text(),
            "max_area": self._max_area.text(),
            "close_r": self._close_r.text(),
            "width_mm": self._width_mm.text(),
            "height_mm": self._height_mm.text(),
            "max_res": self._max_res.text(),
            "aspect_locked": self._lock_cb.isChecked(),
            "bg_visible": self._bg_visible_cb.isChecked(),
            "img_w_px": self._img_w_px,
            "img_h_px": self._img_h_px,
            "img_aspect": self._img_aspect,
            "last_width_mm": self._last_width_mm,
            "last_height_mm": self._last_height_mm,
            "canvas_polys": self._canvas.get_polylines_state(),
            "canvas_view": self._canvas.get_view_state(),
            "document_graph": doc_graph.snapshot(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        self._suspend_state_changes = True
        if not isinstance(state, dict):
            state = {}
        self._reset_trace_runtime_state()
        image_path = str(state.get("image_path", "")).strip()
        self._img_path = image_path or None
        self._img_edit.setText(str(state.get("image_path", "")))
        self._blur.setText(str(state.get("blur", "1.5")))
        self._thresh_entry.setText(str(state.get("threshold", "128")))
        self._auto_thresh_cb.setChecked(bool(state.get("auto_threshold", True)))
        self._update_thresh_controls()
        self._invert_cb.setChecked(bool(state.get("invert", False)))
        self._edge_mode_cb.setChecked(bool(state.get("edge_mode", False)))
        self._canny_low.setText(str(state.get("canny_low", "50")))
        self._canny_high.setText(str(state.get("canny_high", "150")))
        self._outer_only_cb.setChecked(bool(state.get("outer_only", True)))
        self._simplify.setText(str(state.get("simplify", "2.0")))
        self._min_area.setText(str(state.get("min_area", "100")))
        self._max_area.setText(str(state.get("max_area", "")))
        self._close_r.setText(str(state.get("close_r", "1")))
        self._width_mm.setText(str(state.get("width_mm", "50.0")))
        self._height_mm.setText(str(state.get("height_mm", "---")))
        self._max_res.setText(str(state.get("max_res", "1200")))
        self._lock_cb.setChecked(bool(state.get("aspect_locked", True)))
        self._bg_visible_cb.setChecked(bool(state.get("bg_visible", True)))
        try:
            self._img_w_px = int(state.get("img_w_px", 0))
        except (TypeError, ValueError):
            self._img_w_px = 0
        try:
            self._img_h_px = int(state.get("img_h_px", 0))
        except (TypeError, ValueError):
            self._img_h_px = 0
        try:
            self._img_aspect = float(state.get("img_aspect", 1.0))
        except (TypeError, ValueError):
            self._img_aspect = 1.0
        try:
            self._last_width_mm = float(state.get("last_width_mm", 0.0))
        except (TypeError, ValueError):
            self._last_width_mm = 0.0
        try:
            self._last_height_mm = float(state.get("last_height_mm", 0.0))
        except (TypeError, ValueError):
            self._last_height_mm = 0.0
        if image_path and Path(image_path).exists():
            self._load_thumbnail(image_path)
        else:
            self._img_info_lbl.setText("")
        polys: list[list[tuple[float, float]]]
        graph_state = state.get("document_graph")
        if isinstance(graph_state, dict):
            doc_graph = DocumentGraph()
            doc_graph.restore(graph_state)
            polys = polylines_from_graph(doc_graph, layer="trace_preview")
            if not polys:
                polys = polylines_from_graph(doc_graph, layer="geometry")
        else:
            polys = [list(poly) for poly in state.get("canvas_polys", [])]
        self._canvas.set_polylines_state(polys, fit=bool(polys))
        if self._last_width_mm > 0 and self._last_height_mm > 0:
            self._canvas.set_image_bounds(self._last_width_mm, self._last_height_mm)
        if polys and state.get("canvas_view"):
            self._canvas.set_view_state(state["canvas_view"])
        if image_path and self._bg_visible_cb.isChecked() and self._last_width_mm > 0:
            self._restore_background_from_path(image_path)
        elif not self._bg_visible_cb.isChecked():
            self._canvas.clear_background_image()
        self._export_all_btn.setEnabled(bool(polys))
        self._export_sel_btn.setEnabled(False)
        self._suspend_state_changes = False
        self._update_trace_action_states()
        self._refresh_canvas_panels()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._reset_trace_runtime_state()
        self._img_path = None
        self._img_edit.setText("")
        self._canvas.clear_background_image()
        self._canvas.set_image_bounds(0.0, 0.0)
        self._set_status("No image loaded.")
        self._update_trace_action_states()
        self._refresh_canvas_panels()
