# pyright: reportAttributeAccessIssue=false
"""Convert feature page shell and shared preview."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.canvas.runtime import CanvasGridModule
from simple_stipple.canvas.widget import DxfCanvas
from simple_stipple.canvas.widgets.status_strip import CanvasStatusStrip
from simple_stipple.engine.formats.service import DxfService
from simple_stipple.features.base import BasePage
from simple_stipple.features.convert.tasks import (
    FixerSubTab,
    FviSubTab,
    SvgSubTab,
    SvgToDxfSubTab,
    _ConversionSubTab,
)
from simple_stipple.platform.config import save_settings
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.layout import (
    content_splitter,
    surface_frame,
)
from simple_stipple.ui.components.recent_files import RecentFilesButton
from simple_stipple.ui.components.workflow import set_status_label, workflow_strip
from simple_stipple.ui.files import reveal_label
from simple_stipple.ui.recent import KIND_VECTOR, record_recent
from simple_stipple.ui.style.theme import STATUS_NEUTRAL, STATUS_WARN

LOGGER = logging.getLogger(__name__)

# ── Page default settings ────────────────────────────────────────────────
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0
LOG_PANEL_MAX_HEIGHT = 260


# ══════════════════════════════════════════════════════════════════════════
# Tool sub-tabs
# ══════════════════════════════════════════════════════════════════════════


class ConvertPage(BasePage):
    """Convert page — conversion and repair helpers for vector workflows."""

    openInDraftRequested = Signal(object)
    openInPatternRequested = Signal(object)

    _TOOL_DESCS = (
        "Convert FVI vector files to DXF. Supports single file or folder batch mode.",
        "Clean up malformed DXF files — close open polylines, simplify, and remove degenerate geometry.",
        "Export DXF as an SVG vector graphic for web or print workflows.",
        "Import an SVG and convert its paths to DXF polylines.",
    )
    _BTN_LABELS = ("Convert", "Fix DXF", "Convert to SVG", "Convert to DXF")

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._initializing_task = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)
        self._workflow_strip = workflow_strip(
            ("Choose task", "Add input", "Run conversion", "Review result"),
            title="Vector tools",
            description="Repair or convert a file, then review the resulting geometry before continuing.",
        )
        root.addWidget(self._workflow_strip)

        # ── Left sidebar content ──────────────────────────────────────────────
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(12, 12, 12, 4)
        left.setSpacing(4)

        # Vertical tool selector. At the sidebar's compact edge the same
        # choices move into a combo, leaving enough horizontal room for forms.
        tool_label = QLabel("CHOOSE A TASK")
        tool_label.setProperty("role", "section-label")
        left.addWidget(tool_label)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        _tool_labels = [
            "FVI to DXF",
            "Repair DXF",
            "DXF to SVG",
            "SVG to DXF",
        ]
        _tool_tips = [
            "Convert FVI files to DXF format",
            "Repair and clean up DXF files",
            "Export DXF as SVG vector graphics",
            "Import SVG files as DXF outlines",
        ]
        self._task_buttons_widget = QWidget()
        task_buttons_layout = QVBoxLayout(self._task_buttons_widget)
        task_buttons_layout.setContentsMargins(0, 0, 0, 0)
        task_buttons_layout.setSpacing(4)
        for i, (lbl, tip) in enumerate(zip(_tool_labels, _tool_tips)):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setProperty("active", False)
            btn.setProperty("role", "tool-item")
            btn.setMinimumHeight(34)
            btn.setToolTip(tip)
            self._tool_group.addButton(btn, i)
            task_buttons_layout.addWidget(btn)
        left.addWidget(self._task_buttons_widget)
        self._task_combo = QComboBox()
        self._task_combo.setAccessibleName("Conversion task")
        self._task_combo.addItems(_tool_labels)
        self._task_combo.setVisible(False)
        left.addWidget(self._task_combo)

        left.addSpacing(4)

        self._subtab_desc = QLabel(self._TOOL_DESCS[0])
        self._subtab_desc.setProperty("role", "hint")
        self._subtab_desc.setWordWrap(True)
        left.addWidget(self._subtab_desc)

        left.addSpacing(4)

        self._tool_stack = QStackedWidget()
        # The Repair DXF form has a wider natural hint than the compact
        # sidebar. Allow the stack and its page to shrink to the viewport;
        # otherwise the horizontal scrollbar is disabled while controls are
        # laid out at ~340px, visibly clipping the sidebar at 280–320px.
        stack_policy = self._tool_stack.sizePolicy()
        stack_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        self._tool_stack.setSizePolicy(stack_policy)
        self._tool_stack.setMinimumWidth(0)
        self._fvi_subtab = FviSubTab(settings=self._settings)
        self._fix_subtab = FixerSubTab(settings=self._settings)
        self._svg_subtab = SvgSubTab(settings=self._settings)
        self._svg_dxf_subtab = SvgToDxfSubTab(settings=self._settings)
        self._tool_stack.addWidget(self._fvi_subtab)
        self._tool_stack.addWidget(self._fix_subtab)
        self._tool_stack.addWidget(self._svg_subtab)
        self._tool_stack.addWidget(self._svg_dxf_subtab)
        for subtab in (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        ):
            subtab.setMinimumWidth(0)
            subtab_policy = subtab.sizePolicy()
            subtab_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            subtab.setSizePolicy(subtab_policy)
        left.addWidget(self._tool_stack, stretch=1)

        # ── Manual sidebar: scroll area + sticky footer ───────────────────────
        sidebar_frame = surface_frame("sidebar")
        sidebar_frame.setMinimumWidth(280)
        sidebar_frame.setMaximumWidth(320)
        sidebar_outer = QVBoxLayout(sidebar_frame)
        sidebar_outer.setContentsMargins(0, 0, 0, 0)
        sidebar_outer.setSpacing(0)

        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(QFrame.Shape.NoFrame)
        _scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Convert uses a custom sidebar wrapper rather than sidebar_panel;
        # apply the same text reflow policy here so long task descriptions and
        # status messages never disappear behind the viewport edge.
        for label in left_w.findChildren(QLabel):
            if label.text().strip():
                label.setWordWrap(True)
                label_policy = label.sizePolicy()
                label_policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                label.setSizePolicy(label_policy)
        _scroll.setWidget(left_w)
        sidebar_outer.addWidget(_scroll, stretch=1)

        _sep = QFrame()
        _sep.setFrameShape(QFrame.Shape.HLine)
        _sep.setMaximumHeight(1)
        _sep.setProperty("role", "hsep")
        sidebar_outer.addWidget(_sep)

        # Sticky CTA footer
        footer_w = QWidget()
        footer_layout = QVBoxLayout(footer_w)
        footer_layout.setContentsMargins(12, 8, 12, 12)
        footer_layout.setSpacing(8)

        self._footer_btn = QPushButton(self._BTN_LABELS[0])
        self._footer_btn.setMinimumHeight(38)
        self._footer_btn.setProperty("role", "primary")
        self._footer_btn.clicked.connect(self._trigger_active_subtab)

        self._footer_overflow = QToolButton()
        self._footer_overflow.setText("Options")
        self._footer_overflow.setProperty("role", "overflow")
        self._footer_overflow.setFixedWidth(72)
        self._footer_overflow.setFixedHeight(38)
        self._footer_overflow.setToolTip("More actions")
        self._footer_overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._footer_overflow_menu = QMenu(self._footer_overflow)
        self._footer_overflow.setMenu(self._footer_overflow_menu)

        footer_cta = QHBoxLayout()
        footer_cta.setSpacing(4)
        footer_cta.addWidget(self._footer_btn, stretch=1)
        footer_cta.addWidget(self._footer_overflow)
        footer_layout.addLayout(footer_cta)

        self._footer_status = QLabel("")
        self._footer_status.setWordWrap(True)
        self._footer_status.setVisible(False)
        footer_layout.addWidget(self._footer_status)

        # Convert was the only page with a long-running job and no progress
        # affordance (Trace/Pattern both show one) — just static "Working…" text.
        self._footer_progress = QProgressBar()
        self._footer_progress.setRange(0, 0)  # indeterminate
        self._footer_progress.setTextVisible(False)
        self._footer_progress.setFixedHeight(4)
        self._footer_progress.setVisible(False)
        footer_layout.addWidget(self._footer_progress)

        self._footer_widget = footer_w
        self._left_panel = sidebar_frame

        # ── Right panel: empty state → canvas preview ─────────────────────────
        right_w = surface_frame("canvas")
        right_w.setObjectName("conversion-preview")
        right_w.setProperty("role", "preview-surface")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(8)

        self._right_stack = QStackedWidget()
        right.addWidget(self._right_stack, stretch=1)

        # Page 0 — empty state
        _empty_w = QWidget()
        _empty_w.setProperty("role", "empty-state")
        _ev = QVBoxLayout(_empty_w)
        _ev.setContentsMargins(24, 24, 24, 24)
        _ev_icon = QLabel("↗")
        _ev_icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _ev_icon.setProperty("role", "empty-icon")
        _ev_title = QLabel("No preview")
        _ev_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _ev_title.setProperty("role", "empty-title")
        _ev_hint = QLabel(
            "1  Choose a conversion task\n"
            "2  Select an input file\n"
            "3  Run it and review the result here"
        )
        _ev_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        _ev_hint.setWordWrap(True)
        _ev_hint.setProperty("role", "empty-hint")
        _ev.addStretch()
        _ev.addWidget(_ev_icon)
        _ev.addSpacing(8)
        _ev.addWidget(_ev_title)
        _ev.addSpacing(4)
        _ev.addWidget(_ev_hint)
        _ev_choose = QPushButton("Choose input file…")
        _ev_choose.setProperty("role", "primary")
        _ev_choose.setAccessibleDescription(
            "Choose the input for the currently selected conversion task"
        )
        _ev_choose.clicked.connect(self._browse_current_source)
        _ev.addSpacing(8)
        _ev.addWidget(_ev_choose, alignment=Qt.AlignmentFlag.AlignHCenter)
        _ev.addStretch()
        self._right_stack.addWidget(_empty_w)

        # Page 1 — canvas + log
        _canvas_w = QWidget()
        _cl = QVBoxLayout(_canvas_w)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cl.setSpacing(8)

        self._precision_bar = CanvasGridModule(canvas=None, on_changed=self._refresh_preview_ui)
        _cl.addWidget(self._precision_bar)

        self._canvas_status = CanvasStatusStrip()
        _cl.addWidget(self._canvas_status)

        self._preview_canvas = DxfCanvas(selectable=False)
        self._preview_canvas.set_empty_message(
            "No preview\nRun a conversion to see the result here"
        )
        self._preview_canvas.set_grid_visible(DEFAULT_GRID_VISIBLE)
        self._preview_canvas.set_grid_snap(False)
        self._preview_canvas.set_grid_spacing(DEFAULT_GRID_SPACING_MM)
        self._precision_bar.bind_canvas(self._preview_canvas)
        self._canvas_status.bind_canvas(self._preview_canvas)
        _cl.addWidget(self._preview_canvas, stretch=1)

        log_lbl = QLabel("LOG")
        log_lbl.setProperty("role", "section-label")
        _cl.addWidget(log_lbl)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(LOG_PANEL_MAX_HEIGHT)
        self._log.setProperty("role", "log")
        self._log.setPlaceholderText("Conversion output and repair details will appear here.")
        _cl.addWidget(self._log)

        self._right_stack.addWidget(_canvas_w)
        self._right_stack.setCurrentIndex(0)
        right.addWidget(self._footer_widget)

        result_actions = QHBoxLayout()
        self._open_draft_btn = QPushButton("Open in Draft")
        self._open_pattern_btn = QPushButton("Use in Pattern")
        self._open_draft_btn.setEnabled(False)
        self._open_pattern_btn.setEnabled(False)
        self._open_draft_btn.clicked.connect(self._open_preview_in_draft)
        self._open_pattern_btn.clicked.connect(self._open_preview_in_pattern)
        result_actions.addWidget(self._open_draft_btn)
        result_actions.addWidget(self._open_pattern_btn)
        right.addLayout(result_actions)

        # ── Splitter ──────────────────────────────────────────────────────────
        input_header = self._build_shared_input_header()
        input_header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(input_header)
        sidebar_width = max(280, min(320, int(self._settings.get("convert_sidebar_width", 320))))
        self._splitter = content_splitter(self._left_panel, right_w, sizes=(sidebar_width, 860))
        self._splitter.setCollapsible(0, True)
        self._splitter.set_responsive_secondary(0, "Task controls")
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.splitterMoved.connect(self._on_sidebar_resized)
        root.addWidget(self._splitter, stretch=1)

        # ── Connect signals ───────────────────────────────────────────────────
        for tab in (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        ):
            tab.log_line.connect(self._append_log_line)
            tab.preview_path.connect(self._load_preview)

        # Secondary overflow action enabled state (guarded by current tab)
        self._fvi_subtab._out_dir_sig.connect(lambda _: self._update_sec_action_if_active(0, True))
        self._fix_subtab._reveal_state.connect(lambda b: self._update_sec_action_if_active(1, b))
        self._svg_subtab._reveal_state.connect(lambda b: self._update_sec_action_if_active(2, b))
        self._svg_dxf_subtab._reveal_state.connect(
            lambda b: self._update_sec_action_if_active(3, b)
        )

        self._active_tab_idx: int | None = None
        self._tool_group.idClicked.connect(self._on_tool_changed)
        self._tool_group.idClicked.connect(lambda _index: self._emit_state_changed())
        self._task_combo.currentIndexChanged.connect(self._select_task_from_combo)
        # Every persisted Convert control participates in workspace dirty
        # tracking. Previously all of these values round-tripped through JSON
        # while producing zero stateChanged signals, so close/autosave lost
        # them silently.
        for subtab in (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        ):
            for edit in subtab.findChildren(QLineEdit):
                edit.textChanged.connect(lambda _value: self._emit_state_changed())
            for check in subtab.findChildren(QCheckBox):
                check.toggled.connect(lambda _checked: self._emit_state_changed())
            for combo in subtab.findChildren(QComboBox):
                combo.currentIndexChanged.connect(lambda _index: self._emit_state_changed())
            subtab._src_edit.textChanged.connect(self._sync_shared_input_from_task)
            subtab._src_edit.textChanged.connect(lambda _text: self._update_workflow())
        self.setAcceptDrops(True)
        selected_task = max(0, min(3, int(self._settings.get("convert_selected_task", 0))))
        selected_button = self._tool_group.button(selected_task)
        if selected_button is not None:
            selected_button.setChecked(True)
        self._task_combo.setCurrentIndex(selected_task)
        self._on_tool_changed(selected_task)
        self._initializing_task = False
        self._update_task_selector_mode(sidebar_width)
        self._refresh_preview_ui()
        self._update_workflow()

    def sizeHint(self) -> QSize:
        """Prefer a width that fits the smallest supported application window."""
        hint = super().sizeHint()
        return QSize(min(900, hint.width()), hint.height())

    def _on_sidebar_resized(self, position: int, _index: int) -> None:
        if position <= 0:
            return
        width = max(280, min(320, position))
        self._update_task_selector_mode(width)
        if self._settings.get("convert_sidebar_width") != width:
            self._settings["convert_sidebar_width"] = width
            save_settings(self._settings)

    def _update_task_selector_mode(self, width: int) -> None:
        compact = width < 340
        self._task_buttons_widget.setVisible(not compact)
        self._task_combo.setVisible(compact)

    def _select_task_from_combo(self, index: int) -> None:
        button = self._tool_group.button(index)
        if button is not None:
            button.setChecked(True)
        self._on_tool_changed(index)
        self._emit_state_changed()

    def _build_shared_input_header(self) -> QWidget:
        header = surface_frame("panel")
        header.setProperty("role", "input-header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        label = QLabel("INPUT")
        label.setProperty("role", "section-label")
        layout.addWidget(label)
        self._shared_input_edit = QLineEdit()
        self._shared_input_edit.setPlaceholderText("Drop or choose the current conversion input…")
        self._shared_input_edit.setAccessibleName("Current conversion input")
        self._shared_input_edit.editingFinished.connect(self._commit_shared_input)
        layout.addWidget(self._shared_input_edit, 1)
        recent = RecentFilesButton(
            self._settings, KIND_VECTOR, empty_message="No recent conversion inputs."
        )
        recent.fileSelected.connect(self._set_shared_source)
        layout.addWidget(recent)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_current_source)
        layout.addWidget(browse)
        self._shared_input_hint = QLabel("FVI file or folder")
        self._shared_input_hint.setProperty("role", "hint-sm")
        layout.addWidget(self._shared_input_hint)
        return header

    def _active_conversion_tab(self) -> _ConversionSubTab:
        return (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        )[self._tool_stack.currentIndex()]

    def _sync_shared_input_from_task(self, _text: str = "") -> None:
        if not hasattr(self, "_shared_input_edit"):
            return
        source = self._active_conversion_tab()._src_edit.text()
        self._shared_input_edit.blockSignals(True)
        self._shared_input_edit.setText(source)
        self._shared_input_edit.blockSignals(False)

    def _set_shared_source(self, path: str) -> None:
        self._active_conversion_tab()._src_edit.setText(path)
        self._sync_shared_input_from_task()
        if Path(path).is_file():
            record_recent(self._settings, KIND_VECTOR, path)

    def open_repair_input(self, path: str) -> None:
        """Select the appropriate repair/conversion task for an external asset."""
        suffix = Path(path).suffix.casefold()
        index = {".fvi": 0, ".dxf": 1, ".svg": 3}.get(suffix, 1)
        button = self._tool_group.button(index)
        if button is not None:
            button.setChecked(True)
        self._on_tool_changed(index)
        self._set_shared_source(path)

    def _commit_shared_input(self) -> None:
        value = self._shared_input_edit.text().strip()
        if value:
            self._set_shared_source(value)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and any(
            Path(url.toLocalFile()).is_dir()
            or Path(url.toLocalFile()).suffix.casefold() in {".fvi", ".dxf", ".svg"}
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        if not paths:
            event.ignore()
            return
        path = paths[0]
        # Indexed by tool_stack position (Convert/Fix DXF/Convert to SVG/
        # Convert to DXF) — .get() so a future added task degrades to "no
        # extension check" instead of an IndexError inside a drop handler.
        expected_by_index = {0: ".fvi", 1: ".dxf", 2: ".dxf", 3: ".svg"}
        expected = expected_by_index.get(self._tool_stack.currentIndex())
        if expected and Path(path).is_file() and Path(path).suffix.casefold() != expected:
            self._set_footer_status(
                f"This task expects {expected.upper()} input; choose another task or file.",
                STATUS_WARN,
            )
            event.ignore()
            return
        self._set_shared_source(path)
        event.acceptProposedAction()

    def _browse_current_source(self) -> None:
        current = self._tool_stack.currentWidget()
        if isinstance(current, _ConversionSubTab):
            current._browse_src()

    def _on_tool_changed(self, idx: int) -> None:
        self._task_combo.blockSignals(True)
        self._task_combo.setCurrentIndex(idx)
        self._task_combo.blockSignals(False)
        if not self._initializing_task and self._settings.get("convert_selected_task") != idx:
            self._settings["convert_selected_task"] = idx
            save_settings(self._settings)
        self._tool_stack.setCurrentIndex(idx)
        self._subtab_desc.setText(self._TOOL_DESCS[idx])
        for btn in self._tool_group.buttons():
            active = self._tool_group.id(btn) == idx
            btn.setProperty("active", active)
            refresh_style(btn)
        self._footer_btn.setText(self._BTN_LABELS[idx])
        if hasattr(self, "_shared_input_hint"):
            self._shared_input_hint.setText(
                (
                    "FVI file or folder",
                    "DXF file or folder",
                    "DXF file or folder",
                    "SVG file or folder",
                )[idx]
            )
            self._sync_shared_input_from_task()

        _all = (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        )
        if self._active_tab_idx is not None:
            prev = _all[self._active_tab_idx]
            prev._btn_state.disconnect(self._on_subtab_ready)
            prev._status_sig.disconnect(self._set_footer_status)
        self._active_tab_idx = idx
        subtab = _all[idx]
        subtab._btn_state.connect(self._on_subtab_ready)
        subtab._status_sig.connect(self._set_footer_status)
        # Reflect the INCOMING tab's actual state — both whether its own
        # conversion is still in flight from before the user switched away
        # (each subtab guards its own re-entrancy, but the footer CTA should
        # match reality too) and whether it has the input it needs. A blind
        # "not running" left the footer button enabled on a tab with no
        # source path chosen yet — a dead-end click.
        still_running = bool(getattr(subtab, "_running", False))
        self._footer_btn.setEnabled(subtab.is_ready())
        self._footer_progress.setVisible(still_running)
        if still_running:
            self._set_footer_status("Working…", STATUS_NEUTRAL)
        else:
            self._footer_status.setVisible(False)

        self._footer_overflow_menu.clear()
        if idx == 0:
            sec = self._footer_overflow_menu.addAction(
                "Open Output Folder", self._fvi_subtab._open_output_folder
            )
            sec.setEnabled(bool(self._fvi_subtab._last_out_dir))
        else:
            sec = self._footer_overflow_menu.addAction(
                reveal_label(),
                subtab._reveal,  # type: ignore[union-attr]
            )
            sec.setEnabled(bool(subtab._last_out))  # type: ignore[union-attr]
        self._footer_overflow_menu.addSeparator()
        cancel_action = self._footer_overflow_menu.addAction("Cancel Active Job", subtab.cancel)
        cancel_action.setEnabled(still_running)
        self._update_workflow()

    def _trigger_active_subtab(self) -> None:
        """Disable footer CTA, show working status, then invoke the active subtab."""
        _all = (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        )
        subtab = _all[self._tool_stack.currentIndex()]
        if bool(getattr(subtab, "_running", False)):
            return
        self._log.clear()
        self._footer_btn.setEnabled(False)
        self._set_footer_status("Working…", STATUS_NEUTRAL)
        self._footer_progress.setVisible(True)
        subtab.run()
        actions = self._footer_overflow_menu.actions()
        if actions:
            actions[-1].setEnabled(bool(getattr(subtab, "_running", False)))

    def _on_subtab_ready(self, enabled: bool) -> None:
        """Re-enable the footer CTA and, if the job is done, hide progress.

        ``_btn_state`` fires with the subtab's readiness both mid-session
        (e.g. a source path was chosen) and at job completion; only the
        latter case also clears ``_running``, so that's what gates the bar.
        """
        self._footer_btn.setEnabled(enabled)
        subtab = self._tool_stack.currentWidget()
        if not bool(getattr(subtab, "_running", False)):
            self._footer_progress.setVisible(False)

    def _update_sec_action_if_active(self, tab_idx: int, enabled: bool) -> None:
        """Update the secondary overflow action when its tab is active."""
        if self._tool_stack.currentIndex() == tab_idx:
            actions = self._footer_overflow_menu.actions()
            if actions:
                actions[0].setEnabled(enabled)

    def _set_footer_status(self, text: str, color: str = STATUS_NEUTRAL) -> None:
        set_status_label(self._footer_status, text, color)

    def _append_log_line(self, text: str) -> None:
        """Reveal conversion results even when a batch has no single preview file."""
        self._right_stack.setCurrentIndex(1)
        self._log.appendPlainText(text)
        scrollbar = self._log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self._update_workflow()

    def _refresh_preview_ui(self) -> None:
        if not hasattr(self, "_preview_canvas"):
            return
        summary = self._preview_canvas.get_status_summary()
        zoom = self._preview_canvas.get_zoom_percent()
        cursor = self._preview_canvas.get_cursor_world_pos()
        topo = self._preview_canvas.get_topology_summary()

        self._canvas_status.set_snapshot(
            mode=str(summary["mode"]),
            selected_count=self._preview_canvas.sel_count,
            object_count=self._preview_canvas.poly_count,
            precision_text=str(summary["precision"]),
            topology_text=f"{topo['closed']} closed · {topo['open']} open",
            readiness_text=("Preview ready" if self._preview_canvas.poly_count else "No preview"),
            readiness_tone=("success" if self._preview_canvas.poly_count else "warn"),
            zoom_percent=zoom,
            cursor_pos=cursor,
        )
        self._precision_bar.refresh()
        has_preview = bool(self._preview_canvas.poly_count)
        self._open_draft_btn.setEnabled(has_preview)
        self._open_pattern_btn.setEnabled(
            has_preview
            and any(
                len(poly) >= 4 and poly[0] == poly[-1]
                for poly in self._preview_canvas.get_polylines_state()
            )
        )
        self._update_workflow()

    def _update_workflow(self) -> None:
        """Expose the next meaningful Convert action at all times."""
        if not hasattr(self, "_tool_stack"):
            return
        source = self._active_conversion_tab()._src_edit.text().strip()
        has_result = hasattr(self, "_preview_canvas") and bool(self._preview_canvas.poly_count)
        running = bool(getattr(self._active_conversion_tab(), "_running", False))
        if has_result:
            states = ("complete", "complete", "complete", "current")
        elif source or running:
            states = ("complete", "complete", "current", "pending")
        else:
            states = ("complete", "current", "pending", "pending")
        self._workflow_strip.set_step_states(states)

    def _open_preview_in_draft(self) -> None:
        polys = self._preview_canvas.get_polylines_state()
        if polys:
            self.openInDraftRequested.emit(polys)

    def _open_preview_in_pattern(self) -> None:
        closed = [
            poly
            for poly in self._preview_canvas.get_polylines_state()
            if len(poly) >= 4 and poly[0] == poly[-1]
        ]
        if closed:
            self.openInPatternRequested.emit(closed)

    def shutdown(self) -> None:
        """Called by ``App.closeEvent`` before the window tears down."""
        for subtab in (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        ):
            subtab.shutdown()

    def get_workspace_state(self) -> dict:
        return {
            "active_sub_tab": self._tool_stack.currentIndex(),
            "fvi_src": self._fvi_subtab._src_edit.text(),
            "fvi_out": self._fvi_subtab._out_edit.text(),
            "fvi_batch": self._fvi_subtab._is_batch(),
            "fvi_include_subfolders": self._fvi_subtab._include_subfolders.isChecked(),
            "fix_src": self._fix_subtab._src_edit.text(),
            "fix_out": self._fix_subtab._out_edit.text(),
            "fix_batch": self._fix_subtab._is_batch(),
            "fix_include_subfolders": self._fix_subtab._include_subfolders.isChecked(),
            "fix_mode": str(self._fix_subtab._repair_mode.currentData()),
            "svg_src": self._svg_subtab._src_edit.text(),
            "svg_out": self._svg_subtab._out_edit.text(),
            "svg_dxf_src": self._svg_dxf_subtab._src_edit.text(),
            "svg_dxf_out": self._svg_dxf_subtab._out_edit.text(),
            "preview_polys": self._preview_canvas.get_polylines_state(),
            "preview_view": self._preview_canvas.get_view_state(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        self._suspend_state = True
        if not isinstance(state, dict):
            state = {}
        try:
            index = int(state.get("active_sub_tab", 0))
        except (TypeError, ValueError):
            index = 0
        btn = self._tool_group.button(index)
        if btn is not None:
            btn.setChecked(True)
        self._on_tool_changed(index)
        self._fvi_subtab._set_mode("batch" if bool(state.get("fvi_batch")) else "single")
        self._fvi_subtab._src_edit.setText(str(state.get("fvi_src", "")))
        self._fvi_subtab._out_edit.setText(str(state.get("fvi_out", "")))
        self._fvi_subtab._include_subfolders.setChecked(
            bool(state.get("fvi_include_subfolders", True))
        )
        self._fix_subtab._set_mode("batch" if bool(state.get("fix_batch")) else "single")
        self._fix_subtab._src_edit.setText(str(state.get("fix_src", "")))
        self._fix_subtab._out_edit.setText(str(state.get("fix_out", "")))
        self._fix_subtab._include_subfolders.setChecked(
            bool(state.get("fix_include_subfolders", True))
        )
        fix_mode_index = self._fix_subtab._repair_mode.findData(str(state.get("fix_mode", "safe")))
        self._fix_subtab._repair_mode.setCurrentIndex(max(0, fix_mode_index))
        self._svg_subtab._src_edit.setText(str(state.get("svg_src", "")))
        self._svg_subtab._out_edit.setText(str(state.get("svg_out", "")))
        self._svg_dxf_subtab._src_edit.setText(str(state.get("svg_dxf_src", "")))
        self._svg_dxf_subtab._out_edit.setText(str(state.get("svg_dxf_out", "")))
        preview_polys = [list(poly) for poly in state.get("preview_polys", [])]
        self._preview_canvas.set_polylines_state(preview_polys, fit=bool(preview_polys))
        if preview_polys:
            self._right_stack.setCurrentIndex(1)
            if state.get("preview_view"):
                self._preview_canvas.set_view_state(state["preview_view"])
        self._suspend_state = False
        self._refresh_preview_ui()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._log.clear()
        self._right_stack.setCurrentIndex(0)
        self._footer_status.setVisible(False)

    def has_workspace_content(self) -> bool:
        state = self.get_workspace_state()
        return bool(
            state["preview_polys"]
            or any(
                str(state[key]).strip()
                for key in (
                    "fvi_src",
                    "fvi_out",
                    "fix_src",
                    "fix_out",
                    "svg_src",
                    "svg_out",
                    "svg_dxf_src",
                    "svg_dxf_out",
                )
            )
        )

    def _load_preview(self, dxf_path: str) -> None:
        try:
            polys = DxfService.load_dxf_polylines(dxf_path)
            if polys:
                self._right_stack.setCurrentIndex(1)
                self._preview_canvas.load(polys)
                self._refresh_preview_ui()
            else:
                self._set_footer_status(
                    f"Converted, but {Path(dxf_path).name} has no preview geometry", STATUS_WARN
                )
        except (OSError, ValueError) as exc:
            LOGGER.debug("Preview load failed for '%s': %s", dxf_path, exc)
            self._set_footer_status(
                f"Converted, but the preview could not be loaded: {exc}", STATUS_WARN
            )
