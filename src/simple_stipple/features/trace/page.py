"""Image to Outline page."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, NoReturn

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QSplitter,
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
from simple_stipple.canvas.widgets.image_placement_panel import ImagePlacementPanel
from simple_stipple.canvas.widgets.toolbar import CanvasStatusStrip
from simple_stipple.core.cad.preflight import analyze_geometry
from simple_stipple.core.cad.production import machine_profile_from_settings
from simple_stipple.core.imaging import RasterEngravingSpec, export_raster_job, image_to_outlines
from simple_stipple.features.base import BasePage
from simple_stipple.features.canvas_runtime import TraceCanvasPageRuntime
from simple_stipple.features.trace.form import (
    TRACE_FIELD_LIMITS,
    PathField,
    SliderField,
    TextField,
    TraceFieldBindings,
    build_lazy_section,
    build_trace_kwargs,
    trace_default,
)
from simple_stipple.features.trace.model import TraceModel
from simple_stipple.features.trace.placement import ImagePlacement
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
from simple_stipple.ui.components.feedback import clear_line_edit_error, reject_input, show_error
from simple_stipple.ui.components.inputs import make_resettable_line_edit
from simple_stipple.ui.components.layout import (
    CollapsibleSection,
    content_splitter,
    sidebar_panel,
    surface_frame,
)
from simple_stipple.ui.components.recent import KIND_IMAGE, RecentFilesButton, record_recent
from simple_stipple.ui.components.units import parse_numeric_expression, to_display, unit_suffix
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.dialogs.files import (
    pick_open_file,
    pick_save_file,
    reveal_label,
    reveal_path,
)
from simple_stipple.ui.style import STATUS_ERR, STATUS_NEUTRAL, STATUS_OK, STATUS_WARN

TRACE_BG_COLOR = (0x16, 0x21, 0x3E)
TRACE_BG_BLEND_ALPHA = 0.7

# ── Page default settings ────────────────────────────────────────────────
# Detection defaults (blur, threshold, simplify, …) live in
# ``simple_stipple.features.trace.form.TRACE_DEFAULTS`` and are user-editable in
# Settings — only the widgets' mechanical config is defined here.
TRACE_DEBOUNCE_MS = 220  # retrace delay after a control changes
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0
_EXPRESSION_HINT = "enter a number or expression, e.g. 25/2"

#: Next-step destinations: (primary button label, what it does).
_NEXT_ACTIONS: dict[str, tuple[str, str]] = {
    "draft": (
        "Next — Edit in Draft",
        "Send the traced outlines (or the current selection) to Draft for editing",
    ),
    "pattern": (
        "Next — Use in Pattern",
        "Send the closed traced outlines (or the current selection) to Pattern; "
        "open contours are reported and can go to Draft instead",
    ),
    "export": ("Next — Export DXF…", "Export all traced outlines as a DXF file"),
}


def _is_closed(poly: list[tuple[float, float]]) -> bool:
    return len(poly) >= 4 and poly[0] == poly[-1]


LOGGER = logging.getLogger(__name__)


class TracePage(BasePage):
    """Image → outline tracing page."""

    _trace_done = Signal(object)  # (display_img, polys, img_w_px, img_h_px, width_mm)
    _trace_error = Signal(object)
    _trace_progress = Signal(int, int, str)  # (revision, percent, label)
    _trace_cancelled = Signal(int)  # (trace_token)
    sendSelectedToDraftRequested = Signal(object)
    sendSelectedToPatternRequested = Signal(object)
    customTileRequested = Signal(object)

    _MODEL_STATE_FIELDS = {
        "_img_path": "image_path",
        "_running": "running",
        "_trace_pending": "trace_pending",
        "_active_trace_token": "active_trace_token",
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
        "_image_x_mm": "image_x_mm",
        "_image_y_mm": "image_y_mm",
        "_image_rotation_deg": "image_rotation_deg",
        "_trace_revision": "trace_revision",
        "_needs_view_fit": "needs_view_fit",
        "_trace_result_stale": "trace_result_stale",
    }

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent, settings)
        self._model = TraceModel()
        self._img_path: str | None = None
        self._running: bool = False
        self._trace_pending: bool = False
        self._active_trace_token: int | None = None
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
        # Contour count of the last completed trace of the current image, so
        # a retrace can report what a settings change did.
        self._last_contour_count: int | None = None
        # Unit the output Width/Height fields are shown and typed in; follows
        # the canvas display unit (see _sync_size_unit).
        self._size_unit = "mm"
        # While the picture is being dragged: its placement when the drag
        # began and the outlines expressed in that placement's frame.
        self._image_edit_base: tuple[ImagePlacement, list[list[tuple[float, float]]]] | None = None

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
        self._splitter.set_responsive_secondary(0, "Settings")
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

    def showEvent(self, event) -> None:
        # The canvas display unit can change in Settings while this page is
        # hidden; bring the output-size fields along before they are seen.
        self._sync_size_unit()
        super().showEvent(event)

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
            empty_message="No recent files",
        )
        self._recent_btn.setToolTip("Recent files")
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
        self._export_all_btn = QPushButton("Export DXF…")
        self._export_all_btn.setProperty("role", "primary")
        self._export_all_btn.setToolTip("Save all traced outlines as a DXF file")
        self._export_all_btn.setEnabled(False)
        self._export_all_btn.clicked.connect(self._export_all)
        self._export_overflow_btn = QToolButton()
        self._export_overflow_btn.setText("Format")
        self._export_overflow_btn.setProperty("role", "overflow")
        self._export_overflow_btn.setMinimumWidth(72)
        self._export_overflow_btn.setToolTip("Choose export format")
        self._export_overflow_btn.setAccessibleName("Choose export format")
        self._export_overflow_btn.setEnabled(False)
        self._export_overflow_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        _overflow_menu = QMenu(self._export_overflow_btn)
        self._export_sel_action = _overflow_menu.addAction(
            "Export Selected as DXF…", self._export_selected
        )
        self._export_sel_action.setEnabled(False)
        self._export_raster_action = _overflow_menu.addAction(
            "Export Raster Engraving…", self._export_raster_engraving
        )
        self._export_raster_action.setEnabled(False)
        _overflow_menu.addSeparator()
        self._reveal_action = _overflow_menu.addAction(reveal_label(), self._reveal_in_finder)
        self._reveal_action.setEnabled(False)
        self._export_overflow_btn.setMenu(_overflow_menu)
        export_row = QHBoxLayout()
        export_row.setContentsMargins(0, 0, 0, 0)
        export_row.setSpacing(4)
        export_row.addWidget(self._export_all_btn, stretch=1)
        export_row.addWidget(self._export_overflow_btn)
        export_layout.addLayout(export_row)
        self._next_btn = QPushButton()
        self._next_btn.setProperty("role", "primary")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._run_remembered_next)
        self._next_more = QToolButton()
        self._next_more.setText("Next step")
        self._next_more.setProperty("role", "overflow")
        self._next_more.setToolTip("Choose where the Next button sends the traced outlines")
        self._next_more.setAccessibleName("Choose trace next action")
        self._next_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        next_menu = QMenu(self._next_more)
        next_group = QActionGroup(next_menu)
        next_group.setExclusive(True)
        self._next_actions: dict[str, QAction] = {}
        for key, label in (
            ("draft", "Edit in Draft"),
            ("pattern", "Use in Pattern"),
            ("export", "Export DXF…"),
        ):
            action = next_menu.addAction(label)
            action.setCheckable(True)
            next_group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, choice=key: self._select_next_action(choice)
            )
            self._next_actions[key] = action
        self._next_more.setMenu(next_menu)
        next_row = QHBoxLayout()
        next_row.setSpacing(4)
        next_row.addWidget(self._next_btn, 1)
        next_row.addWidget(self._next_more)
        export_layout.addLayout(next_row)
        self._show_next_action(str(self._settings.get("trace_next_action", "draft")))
        # _build_right reparents this into the bottom of the right inspector.
        self._export_footer = export_content

        layout.addStretch()

    def _init_trace_form_fields(self) -> None:
        self._blur = self._mk_entry(
            "blur", "Gaussian blur applied before thresholding / edge detection", unit="px"
        )
        self._thresh_entry = self._mk_entry(
            "threshold", "Brightness cutoff: pixels darker than this become outlines"
        )
        self._canny_low = self._mk_entry(
            "canny_low",
            "Lower hysteresis threshold for Canny edge detection.\n"
            "Edges below this value are discarded.",
        )
        self._canny_high = self._mk_entry(
            "canny_high",
            "Upper hysteresis threshold for Canny edge detection.\n"
            "Edges above this value are always kept.",
        )
        self._simplify = self._mk_entry(
            "simplify", "Polygon simplification tolerance (higher = fewer points)", unit="px"
        )
        self._min_area = self._mk_entry(
            "min_area", "Discard contours smaller than this area", unit="px²"
        )
        self._max_area = self._mk_entry(
            "max_area",
            "Discard contours larger than this area",
            unit="px²",
            placeholder="no limit",
        )
        self._close_r = self._mk_entry(
            "close_r", "Morphological closing radius that fills small gaps in edges", unit="px"
        )
        self._max_res = self._mk_entry(
            "max_res",
            "Maximum pixel dimension when loading the image.\n"
            "Higher values give finer detail but are slower.",
            unit="px",
        )
        self._width_mm = self._mk_size_entry(
            trace_default(self._settings, "width_mm"),
            "Target output width",
            self._commit_width,
            placeholder="greater than 0",
        )
        self._height_mm = self._mk_size_entry(
            "",
            "Target output height; with Lock aspect ratio on, the width follows",
            self._commit_height,
            placeholder="auto",
        )
        try:
            self._set_size_mm(self._width_mm, float(self._width_mm.text() or 50.0))
        except ValueError:
            pass

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

        self._invert_cb = QCheckBox("Invert (dark → light)")
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

    def _mk_entry(
        self,
        key: str,
        description: str,
        *,
        unit: str = "",
        placeholder: str | None = None,
    ) -> QLineEdit:
        """A detection field validated by ``TRACE_FIELD_LIMITS[key]``."""
        limits = TRACE_FIELD_LIMITS[key]
        valid = (
            f"{limits.minimum:g}–{limits.maximum:g}"
            if limits.maximum is not None
            else f"{limits.minimum:g} or more"
        )
        if unit:
            valid += f" {unit}"
        empty_note = "; leave empty for no limit" if limits.allow_empty else ""
        default = trace_default(self._settings, key)
        entry = QLineEdit(default)
        make_resettable_line_edit(entry, default)
        entry.setToolTip(f"{description}\nValid: {valid}{empty_note}")
        entry.setPlaceholderText(valid if placeholder is None else placeholder)
        self._bind_commit(entry, lambda: self._commit_field(entry, key))
        return entry

    def _mk_size_entry(
        self, default: str, description: str, commit, *, placeholder: str
    ) -> QLineEdit:
        """An output-size field in the canvas display unit."""
        entry = QLineEdit(default)
        make_resettable_line_edit(entry, default)
        entry.setToolTip(
            f"{description}. Must be greater than zero.\n"
            "Accepts arithmetic and units, e.g. 25/2 or 1in + 3mm."
        )
        entry.setPlaceholderText(placeholder)
        self._bind_commit(entry, commit)
        return entry

    @staticmethod
    def _bind_commit(entry: QLineEdit, commit) -> None:
        """Commit a typed value on Enter/focus-out, and after the reset button."""
        entry.editingFinished.connect(commit)
        reset = next(iter(entry.findChildren(QToolButton)), None)
        if reset is None:
            return

        def commit_reset() -> None:
            entry.setModified(True)
            commit()

        # The reset button restores the default on the next event-loop turn;
        # this timer is queued after it, so it commits the restored value.
        reset.clicked.connect(lambda: QTimer.singleShot(0, commit_reset))

    def _slider_field(
        self,
        label: str,
        entry: QLineEdit,
        key: str,
        *,
        maximum: float,
        step: float = 1.0,
        empty_at_minimum: bool = False,
    ) -> SliderField:
        """Entry + slider; the slider retraces live, the entry on commit."""
        field = SliderField(
            label,
            entry=entry,
            minimum=TRACE_FIELD_LIMITS[key].minimum,
            maximum=maximum,
            step=step,
            empty_at_minimum=empty_at_minimum,
            tooltip=entry.toolTip(),
        )
        field.slider.valueChanged.connect(lambda _value: self._on_slider_moved(entry))
        return field

    def _on_slider_moved(self, entry: QLineEdit) -> None:
        clear_line_edit_error(entry)
        self._schedule_trace()

    def _build_essential_fields(self, layout: QVBoxLayout) -> None:
        detection_label = QLabel("Detection")
        detection_label.setProperty("role", "section-label")
        layout.addWidget(detection_label)
        layout.addWidget(
            self._slider_field("Blur radius (px)", self._blur, "blur", maximum=5, step=0.1)
        )
        layout.addWidget(self._edge_mode_cb)

        self._thresh_widget = QWidget()
        tw_layout = QVBoxLayout(self._thresh_widget)
        tw_layout.setContentsMargins(0, 0, 0, 0)
        tw_layout.setSpacing(4)
        tw_layout.addWidget(self._auto_thresh_cb)
        thresh_field = self._slider_field(
            "Threshold (0–255)", self._thresh_entry, "threshold", maximum=255
        )
        self._thresh_slider = thresh_field.slider
        tw_layout.addWidget(thresh_field)
        tw_layout.addWidget(self._invert_cb)
        layout.addWidget(self._thresh_widget)

        self._canny_widget = QWidget()
        self._canny_widget.setVisible(False)
        cw_layout = QVBoxLayout(self._canny_widget)
        cw_layout.setContentsMargins(0, 0, 0, 0)
        cw_layout.setSpacing(4)
        cw_layout.addWidget(
            self._slider_field("Canny low (1–255)", self._canny_low, "canny_low", maximum=255)
        )
        cw_layout.addWidget(
            self._slider_field("Canny high (1–255)", self._canny_high, "canny_high", maximum=255)
        )
        layout.addWidget(self._canny_widget)

        size_label = QLabel("Output size")
        size_label.setProperty("role", "section-label")
        layout.addWidget(size_label)
        suffix = unit_suffix(self._size_unit)
        self._width_field = TextField(f"Width ({suffix})", entry=self._width_mm)
        self._height_field = TextField(f"Height ({suffix})", entry=self._height_mm, required=False)
        layout.addWidget(self._width_field)
        layout.addWidget(self._height_field)
        layout.addWidget(self._lock_cb)
        units_hint = QLabel(
            f"Image-cleanup controls use source-image px; output dimensions use {suffix}. "
            "With aspect lock on, Height is derived from Width (editing Height updates Width); "
            "the line below shows source pixels and current output size. Use ✕ on either "
            "field to restore its default."
        )
        units_hint.setProperty("role", "hint-sm")
        units_hint.setWordWrap(True)
        layout.addWidget(units_hint)
        self._size_info_lbl = QLabel("")
        self._size_info_lbl.setProperty("role", "hint-sm")
        self._size_info_lbl.setWordWrap(True)
        layout.addWidget(self._size_info_lbl)
        self._update_thresh_controls()

    def _build_advanced_fields(self, layout: QVBoxLayout) -> None:
        layout.addWidget(
            self._slider_field("Simplify (px)", self._simplify, "simplify", maximum=10, step=0.1)
        )
        layout.addWidget(
            self._slider_field("Min area (px²)", self._min_area, "min_area", maximum=1000)
        )
        layout.addWidget(
            self._slider_field(
                "Max area (px²)",
                self._max_area,
                "max_area",
                maximum=1_000_000,
                step=100,
                empty_at_minimum=True,
            )
        )
        layout.addWidget(
            self._slider_field("Closing radius (px)", self._close_r, "close_r", maximum=20)
        )
        layout.addWidget(self._outer_only_cb)
        layout.addWidget(
            self._slider_field(
                "Max resolution (px)", self._max_res, "max_res", maximum=8000, step=16
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
        profile = machine_profile_from_settings(self._settings)
        self._canvas.set_machine_bed(profile.bed_width_mm, profile.bed_height_mm)
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
        self._canvas.set_background_image_key_callback(self._on_image_key)
        self._canvas.backgroundEditFinished.connect(self._on_image_edit_finished)
        self._canvas.backgroundSelectionChanged.connect(self._on_image_selection_changed)

        # On the canvas toolbar, not the Source section: that section
        # collapses after every successful trace.
        self._adjust_image_btn = QPushButton("Adjust Image")
        self._adjust_image_btn.setCheckable(True)
        self._adjust_image_btn.setMinimumHeight(28)
        self._adjust_image_btn.setToolTip(
            "Click the image, then drag to move it, pull a corner to scale, or drag "
            "the top handle to rotate. The outlines follow the image."
        )
        self._adjust_image_btn.toggled.connect(self._on_adjust_image_toggled)
        self._remove_image_btn = QPushButton("Remove Image")
        self._remove_image_btn.setMinimumHeight(28)
        self._remove_image_btn.setToolTip(
            "Unload the source image; traced outlines stay on the canvas. "
            "Delete does the same while the image is selected."
        )
        self._remove_image_btn.clicked.connect(self._remove_image)
        self._toolbar_module = CanvasToolbarModule(
            canvas=self._canvas,
            on_mode=self._on_toolbar_mode,
            on_fit=self._canvas.fit,
            extra_widgets=(self._adjust_image_btn, self._remove_image_btn),
        )
        layout.addWidget(self._toolbar_module)

        self._grid_module = CanvasGridModule(
            canvas=self._canvas,
            on_changed=self._refresh_canvas_panels,
            compact=True,
        )
        self._precision_bar = self._grid_module
        self._toolbar_module.add_context_widget(self._grid_module)

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
        # Keep the rail wide enough that the export footer never clips; the
        # layer tree scrolls itself, so no extra scroll wrapper is needed.
        side_panel.setMinimumWidth(320)
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
        self._image_panel = ImagePlacementPanel(
            unit=lambda: getattr(self._canvas, "_unit_system", "mm")
        )
        self._image_panel.placementEdited.connect(self._apply_image_placement)
        self._image_panel.centerOnBedRequested.connect(lambda: self._place_image_on_bed(fit=False))
        self._image_panel.fitToBedRequested.connect(lambda: self._place_image_on_bed(fit=True))
        # Same inspector anatomy as Draft: numeric panel above, layers below.
        inspector_splitter = QSplitter(Qt.Orientation.Vertical)
        inspector_splitter.setChildrenCollapsible(False)
        inspector_splitter.addWidget(self._image_panel)
        inspector_splitter.addWidget(self._layer_module)
        inspector_splitter.setStretchFactor(0, 1)
        inspector_splitter.setStretchFactor(1, 2)
        inspector_splitter.setSizes([260, 400])
        side_layout.addWidget(inspector_splitter, stretch=1)
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

        splitter = content_splitter(canvas_shell, side_panel, sizes=(780, 340))
        splitter.set_responsive_secondary(1, "Inspector")
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
        self._active_trace_token = None
        self._cancel_event.set()
        self._cancel_event = threading.Event()
        self._last_out = None
        self._last_display_img = None
        self._last_width_mm = 0.0
        self._last_height_mm = 0.0
        self._img_w_px = 0
        self._img_h_px = 0
        self._img_aspect = 1.0
        self._last_contour_count = None
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
        self._sync_size_unit()
        has_image = bool(self._img_path or self._last_display_img is not None)
        has_polys = bool(self._canvas.poly_count) if hasattr(self, "_canvas") else False
        has_selection = bool(self._canvas.sel_count) if hasattr(self, "_canvas") else False
        self._bg_visible_cb.setEnabled(has_image)
        # Dragging the picture needs it visible; the numeric Image panel
        # stays usable while the background is hidden.
        shows_image = has_image and self._bg_visible_cb.isChecked()
        if not shows_image:
            self._adjust_image_btn.setChecked(False)
        self._adjust_image_btn.setEnabled(shows_image)
        self._remove_image_btn.setEnabled(bool(self._img_path))
        self._refresh_image_panel()
        self._export_all_btn.setEnabled(has_polys)
        self._export_raster_action.setEnabled(has_image)
        # The overflow holds the raster export, which needs only a loaded
        # image, so it stays available whenever any action inside it can run.
        self._export_overflow_btn.setEnabled(has_image or has_polys)
        self._export_sel_action.setEnabled(has_selection)
        self._reveal_action.setEnabled(bool(self._last_out))
        self._reload_btn.setEnabled(has_image)
        self._smooth_btn.setEnabled(has_polys)
        self._next_btn.setEnabled(has_polys)
        self._next_more.setEnabled(has_polys)

    # ── Field validation ──────────────────────────────────────────────────────

    def _reject(self, entry: QLineEdit, message: str) -> NoReturn:
        """Mark *entry* invalid, report why, and abort the caller's commit."""
        reject_input(entry, message)
        self._set_status(message, STATUS_ERR)
        raise ValueError(message)

    def _parse_field(self, entry: QLineEdit, key: str) -> float | None:
        """Validate a detection field against ``TRACE_FIELD_LIMITS[key]``."""
        limits = TRACE_FIELD_LIMITS[key]
        text = entry.text().strip()
        if not text:
            if limits.allow_empty:
                clear_line_edit_error(entry)
                return None
            self._reject(entry, f"{limits.label} is required")
        try:
            value = parse_numeric_expression(text, is_length=False)
        except ValueError:
            self._reject(entry, f"{limits.label}: {_EXPRESSION_HINT}")
        if value < limits.minimum:
            self._reject(entry, f"{limits.label} must be at least {limits.minimum:g}")
        if limits.maximum is not None and value > limits.maximum:
            self._reject(entry, f"{limits.label} must be at most {limits.maximum:g}")
        clear_line_edit_error(entry)
        return value

    def _commit_field(self, entry: QLineEdit, key: str) -> None:
        if not entry.isModified():
            return
        entry.setModified(False)
        try:
            self._parse_field(entry, key)
        except ValueError:
            return
        self._schedule_trace()

    # ── Output size (canvas display unit) ─────────────────────────────────────

    def _sync_size_unit(self) -> None:
        """Re-express Width/Height when the canvas display unit has changed."""
        if not hasattr(self, "_canvas"):
            return
        unit = str(getattr(self._canvas, "_unit_system", "mm"))
        if unit == self._size_unit:
            return
        old_unit, self._size_unit = self._size_unit, unit
        for entry in (self._width_mm, self._height_mm):
            try:
                value_mm = parse_numeric_expression(entry.text(), old_unit)
            except ValueError:
                continue  # blank or invalid: leave the text for the user
            self._set_size_mm(entry, value_mm)
        suffix = unit_suffix(unit)
        self._width_field.set_label(f"Width ({suffix})")
        self._height_field.set_label(f"Height ({suffix})")
        self._update_height_from_width()

    def _set_size_mm(self, entry: QLineEdit, value_mm: float) -> None:
        decimals = 4 if self._size_unit == "in" else 2
        entry.setText(f"{to_display(value_mm, self._size_unit):.{decimals}f}")
        clear_line_edit_error(entry)

    def _size_mm(self, entry: QLineEdit) -> float | None:
        """The field's length in mm, or None when blank/invalid (no feedback)."""
        self._sync_size_unit()
        try:
            value = parse_numeric_expression(entry.text(), self._size_unit)
        except ValueError:
            return None
        return value if value > 0 else None

    def _parse_size(self, entry: QLineEdit, label: str) -> float:
        """The field's length in mm; rejects the entry when invalid."""
        self._sync_size_unit()
        try:
            value = parse_numeric_expression(entry.text(), self._size_unit)
        except ValueError:
            self._reject(entry, f"{label}: {_EXPRESSION_HINT} or 1in + 3mm")
        if value <= 0:
            self._reject(entry, f"{label} must be greater than zero")
        clear_line_edit_error(entry)
        return value

    def _size_state(self, entry: QLineEdit) -> str:
        """Workspace form of an output-size field: mm, or the raw text if invalid."""
        value_mm = self._size_mm(entry)
        return entry.text() if value_mm is None else f"{value_mm:.2f}"

    def _restore_size(self, entry: QLineEdit, text_mm: str) -> None:
        try:
            self._set_size_mm(entry, float(text_mm))
        except ValueError:
            entry.setText(text_mm)

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
            # A newly chosen picture starts unmoved and unrotated, with no
            # earlier trace to compare against.
            self._image_x_mm = self._image_y_mm = self._image_rotation_deg = 0.0
            self._last_contour_count = None
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
            self._set_status("Could not load image", STATUS_ERR)
            self._update_trace_action_states()
            show_error(self, "Image load failed", exc, message="Could not load the selected image.")

    def _update_height_from_width(self) -> None:
        if self._img_aspect <= 0:
            return
        w = self._size_mm(self._width_mm)
        if w is None:
            return
        h = w / self._img_aspect
        self._set_size_mm(self._height_mm, h)
        if self._img_w_px and self._img_h_px:
            unit = self._size_unit
            self._size_info_lbl.setText(
                f"{self._img_w_px}×{self._img_h_px} px → "
                f"{to_display(w, unit):.2f}×{to_display(h, unit):.2f} {unit_suffix(unit)}"
            )

    def _on_aspect_lock_changed(self, state: int) -> None:
        self._aspect_locked = bool(state)
        if self._aspect_locked:
            self._update_height_from_width()

    def _commit_width(self) -> None:
        if not self._width_mm.isModified():
            return
        self._width_mm.setModified(False)
        try:
            width = self._parse_size(self._width_mm, "Width")
        except ValueError:
            return
        self._set_size_mm(self._width_mm, width)
        if self._aspect_locked:
            self._update_height_from_width()
        self._schedule_trace()

    def _commit_height(self) -> None:
        if not self._height_mm.isModified():
            return
        self._height_mm.setModified(False)
        try:
            height = self._parse_size(self._height_mm, "Height")
        except ValueError:
            return
        # The trace is sized by width; height drives it only through the
        # locked aspect ratio.
        if self._aspect_locked and self._img_aspect > 0:
            self._set_size_mm(self._width_mm, height * self._img_aspect)
            self._update_height_from_width()
            self._schedule_trace()
        else:
            self._set_size_mm(self._height_mm, height)

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
        """Start a fresh trace immediately, even if the current worker stalls."""
        if not self._img_path:
            self._set_status("No image loaded.", STATUS_ERR)
            return
        self._trace_revision += 1
        self._trace_pending = False
        self._trace_result_stale = bool(self._canvas.poly_count)
        self._preview_timer.stop()
        self._cancel_event.set()
        # The old worker keeps its own cancellation event reference. Mark this
        # run inactive and launch a fresh token now; late signals from the old
        # worker are ignored instead of changing the new run's UI state.
        self._running = False
        self._active_trace_token = None
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
        trace_token = self._trace_revision
        fields = TraceFieldBindings(
            blur=self._blur,
            simplify=self._simplify,
            min_area=self._min_area,
            max_area=self._max_area,
            close_r=self._close_r,
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
                width_mm=self._parse_size(self._width_mm, "Width"),
                parse_field=self._parse_field,
                on_progress=lambda pct, lbl: self._trace_progress.emit(trace_token, pct, lbl),
            )
        except ValueError:
            # The invalid field is already marked and the reason is in the
            # status line; keep the last preview instead of tracing.
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
        self._active_trace_token = trace_token
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # indeterminate
        self._set_status(
            "Preview out of date — tracing…" if self._trace_result_stale else "Tracing…",
            STATUS_WARN if self._trace_result_stale else STATUS_NEUTRAL,
        )
        self._reload_btn.setText("Restart Trace")
        self._reload_btn.setToolTip("Abandon the current trace and start a fresh one")
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
        self._active_trace_token = None
        self._reload_btn.setText("Refresh Preview")
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._canvas.set_image_bounds(width_mm_val, height_mm_val)
        self._last_display_img = _display_img
        self._last_width_mm = width_mm_val
        self._last_height_mm = height_mm_val
        unit = self._size_unit
        size_text = (
            f"{to_display(width_mm_val, unit):.2f}×{to_display(height_mm_val, unit):.2f} "
            f"{unit_suffix(unit)}"
        )
        if hasattr(self, "_trace_settings_section"):
            self._trace_settings_section.set_subtitle(f"{count} contour(s) · {size_text}")
        if _display_img is not None and self._bg_visible_cb.isChecked():
            self._show_background()
        if polys:
            placement = self._image_placement()
            polys = placement.place(polys, (width_mm_val, height_mm_val))
            self._canvas.set_polylines_state(polys, fit=self._needs_view_fit)
            self._trace_result_stale = False
            self._canvas.setToolTip("")
            self._needs_view_fit = False
            self._source_section.set_expanded(False)
            self._thumb_lbl.setMaximumHeight(64)
            previous, self._last_contour_count = self._last_contour_count, count
            if previous is None:
                change = ""
            elif previous == count:
                change = " (unchanged)"
            else:
                change = f" ({count - previous:+d} vs previous)"
            self._set_status(
                f"{count} contour(s){change} extracted  ·  "
                f"{img_w_px}×{img_h_px} px → {size_text} · "
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
        self._active_trace_token = None
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

    def _show_next_action(self, choice: str) -> None:
        """Show the active destination on the primary button and in the menu."""
        if choice not in _NEXT_ACTIONS:
            choice = "draft"
        label, description = _NEXT_ACTIONS[choice]
        self._next_btn.setText(label)
        self._next_btn.setToolTip(
            f"{description}.\nChange the destination with the Next step menu beside it."
        )
        self._next_actions[choice].setChecked(True)

    def _select_next_action(self, choice: str) -> None:
        """Remember the destination without unexpectedly leaving the page."""
        self._settings["trace_next_action"] = choice
        self._show_next_action(choice)
        self._set_status(
            f"Next step set: {self._next_btn.text()} — choose it when the trace is ready.",
            STATUS_OK,
        )

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
            self._send_to_pattern(polys)
        else:
            self.sendSelectedToDraftRequested.emit(polys)

    def _send_to_pattern(self, polys: list[list[tuple[float, float]]]) -> None:
        """Hand closed outlines to Pattern; explain and offer Draft for open ones."""
        closed = [poly for poly in polys if _is_closed(poly)]
        open_polys = [poly for poly in polys if not _is_closed(poly)]
        if not open_polys:
            self.sendSelectedToPatternRequested.emit(closed)
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Open contours")
        pattern_btn = None
        if closed:
            box.setText(
                f"{len(open_polys)} open contour(s) can't be used in Pattern and will be excluded."
            )
            box.setInformativeText(
                f"{len(closed)} closed outline(s) will be sent to Pattern. Send the open "
                "contours to Draft to close or repair them."
            )
            pattern_btn = box.addButton(
                f"Send {len(closed)} to Pattern", QMessageBox.ButtonRole.AcceptRole
            )
            draft_btn = box.addButton("Send Open to Draft", QMessageBox.ButtonRole.ActionRole)
        else:
            box.setText(
                f"None of the {len(open_polys)} traced contour(s) are closed, so Pattern "
                "can't use them."
            )
            box.setInformativeText(
                "Increase Closing radius or turn off Edge mode and retrace, or send the "
                "contours to Draft to close them."
            )
            draft_btn = box.addButton("Send to Draft", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if pattern_btn is not None and clicked is pattern_btn:
            self._set_status(
                f"Sent {len(closed)} closed outline(s) to Pattern; "
                f"{len(open_polys)} open contour(s) excluded.",
                STATUS_WARN,
            )
            self.sendSelectedToPatternRequested.emit(closed)
        elif clicked is draft_btn:
            self.sendSelectedToDraftRequested.emit(open_polys)
        else:
            self._set_status(
                f"Nothing sent — {len(open_polys)} open contour(s) need closing for Pattern.",
                STATUS_WARN,
            )

    def _handle_trace_cancelled(self, trace_token: int) -> None:
        """Reset the UI only for the currently active worker's cancellation."""
        if self._shutting_down:
            return
        if trace_token != self._active_trace_token:
            return
        self._active_trace_token = None
        self._running = False
        self._reload_btn.setText("Refresh Preview")
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

    def _on_trace_progress(self, trace_token: int, percent: int, label: str) -> None:
        if (
            self._shutting_down
            or not self._running
            or trace_token != self._trace_revision
            or trace_token != self._active_trace_token
        ):
            return
        self._progress.setRange(0, 100)
        self._progress.setValue(percent)
        if percent < 100:
            self._set_status(
                f"Preview out of date — {label}" if self._trace_result_stale else label,
                STATUS_WARN if self._trace_result_stale else STATUS_NEUTRAL,
            )

    def _on_bg_visible_changed(self, state: int) -> None:
        if state and self._last_display_img is not None:
            self._show_background()
        elif not state:
            self._adjust_image_btn.setChecked(False)
            self._canvas.clear_background_image()
        self._update_trace_action_states()

    def _image_placement(self) -> ImagePlacement:
        return ImagePlacement(
            self._image_x_mm,
            self._image_y_mm,
            self._last_width_mm,
            self._last_height_mm,
            self._image_rotation_deg,
        )

    def _show_background(self) -> None:
        """Draw the faded source image at its current placement."""
        if self._last_display_img is None:
            return
        try:
            bg_layer = Image.new("RGB", self._last_display_img.size, TRACE_BG_COLOR)
            faded = Image.blend(
                self._last_display_img.convert("RGB"), bg_layer, TRACE_BG_BLEND_ALPHA
            )
        except (OSError, ValueError) as exc:
            LOGGER.debug("Failed to prepare the background image: %s", exc)
            return
        placement = self._image_placement()
        self._canvas.set_background_image(
            faded,
            placement.width_mm,
            placement.height_mm,
            placement.x_mm,
            placement.y_mm,
            placement.rotation_deg,
        )

    def _on_adjust_image_toggled(self, enabled: bool) -> None:
        self._image_edit_base = None
        self._canvas.set_background_image_editable(
            enabled, self._on_image_transform, keep_aspect=True
        )
        if enabled:
            self._canvas.deselect_all()
            self._canvas.select_background_image(True)

    def _on_image_selection_changed(self, selected: bool) -> None:
        if selected:
            self._set_status(
                "Image selected — drag to move, pull a corner to scale, drag the top "
                "handle to rotate (Shift snaps). Delete removes it.",
                STATUS_OK,
            )
        elif self._adjust_image_btn.isChecked():
            self._set_status("Click the image to move, scale, or rotate it.")

    def _on_image_transform(
        self, x: float, y: float, w: float, h: float, rotation: float = 0.0
    ) -> None:
        """Keep the outlines glued to the picture while it is dragged."""
        if self._image_edit_base is None:
            start = self._image_placement()
            self._image_edit_base = (start, start.unplace(self._canvas.get_polylines_state()))
        start, local_polys = self._image_edit_base
        moved = ImagePlacement(x, y, w, h, rotation)
        self._image_x_mm, self._image_y_mm, self._image_rotation_deg = x, y, rotation
        self._last_width_mm, self._last_height_mm = w, h
        self._canvas.set_polylines_state(
            moved.place(local_polys, (start.width_mm, start.height_mm))
        )
        if abs(w - start.width_mm) > 1e-6:
            self._set_size_mm(self._width_mm, w)
            self._update_height_from_width()
        self._refresh_image_panel()

    def _apply_image_placement(
        self, x: float, y: float, w: float, h: float, rotation: float
    ) -> None:
        """A typed placement: one complete edit through the same path as a drag."""
        self._on_image_transform(x, y, w, h, rotation)
        if self._bg_visible_cb.isChecked():
            self._show_background()
        self._on_image_edit_finished()

    def _place_image_on_bed(self, *, fit: bool) -> None:
        """Centre (or fit, keeping proportions) the picture on the machine bed."""
        profile = machine_profile_from_settings(self._settings)
        if not profile.has_bed():
            self._set_status(
                "Set the machine bed size in Settings to centre or fit the image on it.",
                STATUS_WARN,
            )
            return
        assert profile.bed_width_mm is not None and profile.bed_height_mm is not None
        current = self._image_placement()
        target = (
            current.fitted_to(profile.bed_width_mm, profile.bed_height_mm)
            if fit
            else current.centered_on(profile.bed_width_mm, profile.bed_height_mm)
        )
        self._apply_image_placement(
            target.x_mm, target.y_mm, target.width_mm, target.height_mm, target.rotation_deg
        )
        unit = self._size_unit
        suffix = unit_suffix(unit)
        self._set_status(
            f"Image {'fitted to' if fit else 'centred on'} the bed — X "
            f"{to_display(target.x_mm, unit):.2f}, Y {to_display(target.y_mm, unit):.2f}, "
            f"{to_display(target.width_mm, unit):.2f}×{to_display(target.height_mm, unit):.2f} "
            f"{suffix}",
            STATUS_OK,
        )

    def _refresh_image_panel(self) -> None:
        # Numeric placement works whether or not the background is shown.
        loaded = bool(self._img_path or self._last_display_img is not None)
        placement = self._image_placement()
        self._image_panel.set_placement(
            (
                placement.x_mm,
                placement.y_mm,
                placement.width_mm,
                placement.height_mm,
                placement.rotation_deg,
            )
            if loaded and placement.width_mm > 0
            else None
        )

    def _on_image_edit_finished(self) -> None:
        if self._image_edit_base is None:
            return
        start, _local = self._image_edit_base
        self._image_edit_base = None
        if abs(self._last_width_mm - start.width_mm) > 1e-6:
            # Trace detail depends on the physical size: re-trace at the new
            # width. The scaled outlines stay visible until it finishes.
            self._schedule_trace()
        else:
            self._emit_state_changed()

    def _on_image_key(self, action: str, _reverse: bool = False) -> None:
        if action == "remove":
            self._remove_image()

    def _remove_image(self) -> None:
        """Unload the source picture; outlines already traced stay editable."""
        if not self._img_path and self._last_display_img is None:
            return
        self._preview_timer.stop()
        self._trace_revision += 1  # ignore any trace still finishing
        self._image_edit_base = None
        self._adjust_image_btn.setChecked(False)
        self._reset_trace_runtime_state()
        self._img_path = None
        self._img_edit.setText("")
        self._image_x_mm = self._image_y_mm = self._image_rotation_deg = 0.0
        self._needs_view_fit = True
        self._canvas.clear_background_image()
        self._reload_btn.setText("Refresh Preview")
        self._update_trace_action_states()
        self._set_status(
            "Image removed — traced outlines kept. Select them and press Delete to clear."
            if self._canvas.poly_count
            else "Image removed."
        )
        self._emit_state_changed()

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
            self._send_to_pattern(polys)

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
        dither = QComboBox()
        dither.addItem("Continuous tone", "continuous")
        dither.addItem("Floyd–Steinberg", "floyd_steinberg")
        dither.addItem("Ordered", "ordered")
        dither.addItem("Halftone", "halftone")
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
        form.addRow("Dither", dither)
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
                dither=str(dither.currentData()),
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
        if self._last_out and not reveal_path(self, self._last_out):
            QMessageBox.warning(
                self,
                "File Not Found",
                f"The file no longer exists:\n{self._last_out}",
            )

    def _restore_background_from_path(self, path: str) -> None:
        try:
            with Image.open(path) as src:
                self._last_display_img = src.convert("RGB")
        except (OSError, ValueError) as exc:
            LOGGER.debug("Failed to restore background image from path '%s': %s", path, exc)
            self._canvas.clear_background_image()
            return
        self._show_background()

    def shutdown(self) -> None:
        """Called by ``App.closeEvent`` before the window tears down.

        Signal the in-flight trace worker (if any) to stop and give it a
        short window to actually exit, instead of leaving it to run to
        completion (or crash) against a page that's already being destroyed.
        """
        self._preview_timer.stop()
        self._trace_revision += 1
        self._shutdown_thread(self._trace_thread, self._cancel_event.set)

    def get_workspace_state(self) -> dict:
        return get_trace_workspace_state(self)

    def apply_workspace_state(self, state: dict | None) -> None:
        apply_trace_workspace_state(self, state)

    def clear_workspace_state(self) -> None:
        clear_trace_workspace_state(self)

    # DXF export workflow callbacks, owned by ``trace.session``.
    _get_save_path = _get_save_path
    _export_all = _export_all
    _export_selected = _export_selected
