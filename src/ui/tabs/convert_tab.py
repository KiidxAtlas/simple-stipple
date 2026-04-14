"""Utilities tab — FVI → DXF | DXF Fixer | DXF → SVG | SVG → DXF."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.core.document_graph import DocumentGraph
from src.core.document_migration import graph_from_polylines, polylines_from_graph
from src.core.dxf_fix import fix_dxf
from src.core.dxf_io import load_dxf_polylines
from src.core.dxf_svg import dxf_to_svg
from src.core.fvi import convert_fvi_to_dxf
from src.core.svg_dxf import svg_to_dxf
from src.ui.action_maps import UTILITIES_ACTION_MAP
from src.ui.canvas import DxfCanvas
from src.ui.helpers import (
    _content_splitter,
    _section_label,
    _surface_frame,
)

ACTION_MAP = UTILITIES_ACTION_MAP


class UtilitiesTab(QWidget):
    """Utilities — conversion and repair helpers for vector workflows."""

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar (sub-tabs + small preview) ───────────────────────────
        left_w = _surface_frame("sidebar")
        left = QVBoxLayout(left_w)
        left.setContentsMargins(10, 10, 10, 10)
        left.setSpacing(6)

        tool_lbl = QLabel("Tool")
        tool_lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
        left.addWidget(tool_lbl)

        self._tool_combo = QComboBox()
        self._tool_combo.addItems([
            "FVI to DXF",
            "Repair DXF",
            "DXF to SVG",
            "SVG to DXF",
        ])
        self._tool_combo.setToolTip("Choose a conversion or repair utility")
        left.addWidget(self._tool_combo)

        self._tool_stack = QStackedWidget()

        self._fvi_subtab = _FviSubTab(settings=self._settings)
        self._fix_subtab = _FixerSubTab(settings=self._settings)
        self._svg_subtab = _SvgSubTab(settings=self._settings)
        self._svg_dxf_subtab = _SvgToDxfSubTab(settings=self._settings)
        self._tool_stack.addWidget(self._fvi_subtab)
        self._tool_stack.addWidget(self._fix_subtab)
        self._tool_stack.addWidget(self._svg_subtab)
        self._tool_stack.addWidget(self._svg_dxf_subtab)
        self._tool_combo.currentIndexChanged.connect(self._tool_stack.setCurrentIndex)
        left.addWidget(self._tool_stack, stretch=1)

        # ── Right: preview canvas + compact log ──────────────────────────────
        right_w = _surface_frame("canvas")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

        # Preview canvas — main content area
        self._preview_canvas = DxfCanvas(selectable=False)
        right.addWidget(self._preview_canvas, stretch=1)

        # Log — compact panel below preview
        log_lbl = QLabel("Log")
        log_lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
        right.addWidget(log_lbl)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        self._log.setStyleSheet("font-family: Menlo, Courier; font-size: 11px;")
        self._log.setPlaceholderText("Conversion output and repair details will appear here.")
        right.addWidget(self._log)

        left_w.setMinimumWidth(320)
        left_w.setMaximumWidth(400)
        self._left_panel = left_w
        self._splitter = _content_splitter(left_w, right_w, sizes=(320, 920))
        root.addWidget(self._splitter)

        # Connect sub-tab signals to shared log and preview
        for tab in (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        ):
            tab.log_line.connect(self._log.appendPlainText)
            tab.preview_path.connect(self._load_preview)

    def get_workspace_state(self) -> dict:
        doc_graph = graph_from_polylines(
            self._preview_canvas.get_polylines_state(),
            layer="convert_preview",
            as_segments=False,
        )
        return {
            "active_sub_tab": self._tool_stack.currentIndex(),
            "fvi_src": self._fvi_subtab._src_edit.text(),
            "fvi_out": self._fvi_subtab._out_edit.text(),
            "fvi_batch": self._fvi_subtab._is_batch(),
            "fix_src": self._fix_subtab._src_edit.text(),
            "fix_out": self._fix_subtab._out_edit.text(),
            "svg_src": self._svg_subtab._src_edit.text(),
            "svg_out": self._svg_subtab._out_edit.text(),
            "svg_dxf_src": self._svg_dxf_subtab._src_edit.text(),
            "svg_dxf_out": self._svg_dxf_subtab._out_edit.text(),
            "preview_polys": self._preview_canvas.get_polylines_state(),
            "preview_view": self._preview_canvas.get_view_state(),
            "document_graph": doc_graph.snapshot(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        state = state or {}
        index = int(state.get("active_sub_tab", 0))
        self._tool_combo.setCurrentIndex(index)
        self._tool_stack.setCurrentIndex(index)
        self._fvi_subtab._set_mode("batch" if state.get("fvi_batch") else "single")
        self._fvi_subtab._src_edit.setText(str(state.get("fvi_src", "")))
        self._fvi_subtab._out_edit.setText(str(state.get("fvi_out", "")))
        self._fix_subtab._src_edit.setText(str(state.get("fix_src", "")))
        self._fix_subtab._out_edit.setText(str(state.get("fix_out", "")))
        self._svg_subtab._src_edit.setText(str(state.get("svg_src", "")))
        self._svg_subtab._out_edit.setText(str(state.get("svg_out", "")))
        self._svg_dxf_subtab._src_edit.setText(str(state.get("svg_dxf_src", "")))
        self._svg_dxf_subtab._out_edit.setText(str(state.get("svg_dxf_out", "")))
        graph_state = state.get("document_graph")
        if isinstance(graph_state, dict):
            doc_graph = DocumentGraph()
            doc_graph.restore(graph_state)
            preview_polys = polylines_from_graph(doc_graph, layer="convert_preview")
            if not preview_polys:
                preview_polys = polylines_from_graph(doc_graph, layer="geometry")
        else:
            preview_polys = [list(poly) for poly in state.get("preview_polys", [])]
        self._preview_canvas.set_polylines_state(preview_polys, fit=bool(preview_polys))
        if preview_polys and state.get("preview_view"):
            self._preview_canvas.set_view_state(state["preview_view"])

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._log.clear()

    def _load_preview(self, dxf_path: str) -> None:
        try:
            polys = load_dxf_polylines(dxf_path)
            if polys:
                self._preview_canvas.load(polys)
        except Exception:
            pass


# ─── FVI → DXF sub-tab ───────────────────────────────────────────────────────


class _FviSubTab(QWidget):
    log_line = Signal(str)  # → parent shared log
    preview_path = Signal(str)  # → parent preview canvas
    _btn_state = Signal(bool)  # → self._btn.setEnabled
    _out_dir_sig = Signal(str)  # → self._set_output_dir

    def __init__(
        self,
        parent: QWidget | None = None,
        settings: dict | None = None,
    ):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out_dir: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Mode")
        mode_row = QHBoxLayout()
        self._mode_single = QPushButton("Single file")
        self._mode_single.setToolTip("Convert one FVI file at a time")
        self._mode_batch = QPushButton("Folder (batch)")
        self._mode_batch.setToolTip("Convert all FVI files in a folder at once")
        self._mode_single.setProperty("active", True)
        self._mode_batch.setProperty("active", False)
        self._mode_single.clicked.connect(lambda: self._set_mode("single"))
        self._mode_batch.clicked.connect(lambda: self._set_mode("batch"))
        mode_row.addWidget(self._mode_single)
        mode_row.addWidget(self._mode_batch)
        layout.addLayout(mode_row)

        _section_label(layout, "Source")
        src_row = QHBoxLayout()
        self._src_edit = QLineEdit()
        self._src_edit.setPlaceholderText("Select a .fvi file or folder…")
        self._src_edit.setToolTip("Path to an FVI file or a folder of FVI files")
        src_btn = QPushButton("Browse")
        src_btn.setFixedWidth(70)
        src_btn.setToolTip("Browse for an FVI source file or folder")
        src_btn.clicked.connect(self._browse_src)
        src_row.addWidget(self._src_edit, stretch=1)
        src_row.addWidget(src_btn)
        layout.addLayout(src_row)

        _section_label(layout, "Output folder")
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Optional (blank = same as source)…")
        self._out_edit.setToolTip("Destination folder for converted DXF files (blank = alongside source)")
        out_btn = QPushButton("Browse")
        out_btn.setFixedWidth(70)
        out_btn.setToolTip("Choose an output folder for converted files")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        layout.addLayout(out_row)

        self._btn = QPushButton("Convert")
        self._btn.setMinimumHeight(38)
        self._btn.setToolTip("Run the FVI to DXF conversion")
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)
        layout.addWidget(self._btn)

        self._open_folder_btn = QPushButton("Open Output Folder")
        self._open_folder_btn.setMinimumHeight(28)
        self._open_folder_btn.setToolTip("Open the output folder in Finder")
        self._open_folder_btn.setEnabled(False)
        self._open_folder_btn.clicked.connect(self._open_output_folder)
        layout.addWidget(self._open_folder_btn)

        layout.addStretch()

        # Wire internal signals (thread-safe)
        self._btn_state.connect(self._btn.setEnabled)
        self._out_dir_sig.connect(self._set_output_dir)

    def _set_mode(self, mode: str) -> None:
        for b, active in [
            (self._mode_single, mode == "single"),
            (self._mode_batch, mode == "batch"),
        ]:
            b.setProperty("active", active)
            b.style().unpolish(b)
            b.style().polish(b)

    def _is_batch(self) -> bool:
        return self._mode_batch.property("active") is True

    def _browse_src(self) -> None:
        idir = self._settings.get("fvi_source_dir", "")
        if not self._is_batch():
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select FVI file",
                idir,
                "FVI files (*.fvi *.Fvi *.FVI);;All files (*)",
            )
        else:
            path = QFileDialog.getExistingDirectory(
                self, "Select folder containing FVI files", idir
            )
        if path:
            self._src_edit.setText(path)

    def _browse_out(self) -> None:
        idir = self._settings.get("fvi_output_dir", "")
        path = QFileDialog.getExistingDirectory(self, "Select output folder", idir)
        if path:
            self._out_edit.setText(path)

    def _set_output_dir(self, d: str) -> None:
        self._last_out_dir = d
        self._open_folder_btn.setEnabled(True)

    def _open_output_folder(self) -> None:
        if self._last_out_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_out_dir)))

    def _run(self) -> None:
        src = self._src_edit.text().strip()
        if not src:
            QMessageBox.critical(
                self, "Error", "Please select a source file or folder."
            )
            return
        src_path = Path(src)
        if self._is_batch():
            if not src_path.is_dir():
                QMessageBox.warning(
                    self,
                    "Folder Not Found",
                    f"The source folder does not exist:\n{src}",
                )
                return
        else:
            if not src_path.is_file():
                QMessageBox.warning(
                    self,
                    "File Not Found",
                    f"The source file does not exist:\n{src}",
                )
                return
        self._btn.setEnabled(False)
        out_dir = self._out_edit.text().strip() or None
        threading.Thread(target=self._convert, args=(src, out_dir), daemon=True).start()

    def _convert(self, src: str, out_dir: str | None) -> None:
        p = Path(src)
        if p.is_file():
            files = [p]
        else:
            raw = (
                list(p.rglob("*.fvi")) + list(p.rglob("*.Fvi")) + list(p.rglob("*.FVI"))
            )
            seen: set[str] = set()
            files = []
            for f in raw:
                k = str(f).lower()
                if k not in seen:
                    seen.add(k)
                    files.append(f)

        if not files:
            self.log_line.emit("No .fvi files found.")
            self._btn_state.emit(True)
            return

        self.log_line.emit(f"Found {len(files)} file(s)\n")
        ok = err = 0
        last_dxf: str | None = None
        for fvi in files:
            dest_dir = Path(out_dir) if out_dir else fvi.parent
            dest = dest_dir / fvi.with_suffix(".dxf").name
            try:
                convert_fvi_to_dxf(fvi, dest)
                self.log_line.emit(f"  ✓  {fvi.name}  →  {dest.name}")
                ok += 1
                last_dxf = str(dest)
            except Exception as exc:
                self.log_line.emit(f"  ✗  {fvi.name}: {exc}")
                err += 1

        self.log_line.emit(f"\nDone — {ok} converted, {err} error(s).")
        self._btn_state.emit(True)
        if files:
            final_dir = out_dir or str(files[0].parent)
            self._out_dir_sig.emit(final_dir)
        if last_dxf:
            self.preview_path.emit(last_dxf)


# ─── DXF Fixer sub-tab ───────────────────────────────────────────────────────


class _FixerSubTab(QWidget):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)  # text, color

    def __init__(
        self,
        parent: QWidget | None = None,
        settings: dict | None = None,
    ):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Input DXF")
        src_row = QHBoxLayout()
        self._src_edit = QLineEdit()
        self._src_edit.setPlaceholderText("Select a .dxf file…")
        self._src_edit.setToolTip("Path to the DXF file that needs repair")
        src_btn = QPushButton("Browse")
        src_btn.setFixedWidth(70)
        src_btn.setToolTip("Browse for a DXF file to repair")
        src_btn.clicked.connect(self._browse_src)
        src_row.addWidget(self._src_edit, stretch=1)
        src_row.addWidget(src_btn)
        layout.addLayout(src_row)

        _section_label(layout, "Output DXF")
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Leave blank to overwrite input…")
        self._out_edit.setToolTip("Save repaired DXF here (blank = overwrite the input file)")
        out_btn = QPushButton("Browse")
        out_btn.setFixedWidth(70)
        out_btn.setToolTip("Choose where to save the repaired DXF file")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        layout.addLayout(out_row)

        self._btn = QPushButton("Fix DXF")
        self._btn.setMinimumHeight(38)
        self._btn.setToolTip("Close open polylines, simplify, and discard degenerate geometry")
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)
        layout.addWidget(self._btn)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setMinimumHeight(26)
        self._reveal_btn.setToolTip("Open the repaired file location in Finder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal)
        layout.addWidget(self._reveal_btn)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._btn_state.connect(self._btn.setEnabled)
        self._reveal_state.connect(self._reveal_btn.setEnabled)
        self._status_sig.connect(self._set_status)

    def _browse_src(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select DXF file",
            "",
            "DXF files (*.dxf *.DXF);;All files (*)",
        )
        if path:
            self._src_edit.setText(path)

    def _browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save fixed DXF",
            "",
            "DXF files (*.dxf);;All files (*)",
        )
        if path:
            self._out_edit.setText(path)

    def _set_status(self, text: str, color: str = "#8b949e") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _run(self) -> None:
        src = self._src_edit.text().strip()
        if not src:
            QMessageBox.critical(self, "Error", "Please select an input DXF file.")
            return
        if not Path(src).is_file():
            QMessageBox.warning(
                self,
                "File Not Found",
                f"The source file does not exist:\n{src}",
            )
            return
        out = self._out_edit.text().strip()
        if not out:
            answer = QMessageBox.question(
                self,
                "Overwrite Original?",
                "Output path is empty. This will overwrite the original file. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            out = src
        self._btn.setEnabled(False)
        self._set_status("Fixing…")
        threading.Thread(target=self._fix, args=(src, out), daemon=True).start()

    def _fix(self, src: str, out: str) -> None:
        try:
            stats = fix_dxf(src, out)
            msg = (
                f"Done — {stats['polylines_in']} in → {stats['polylines_out']} out"
                f"  · closed {stats['closed']}"
                f"  · simplified {stats['simplified']}"
                f"  · discarded {stats['discarded']}"
            )
            self.log_line.emit(msg)
            self._btn_state.emit(True)
            self._reveal_state.emit(True)
            self._status_sig.emit("Done", "#3fb950")
            self._last_out = out
            self.preview_path.emit(out)
        except Exception as exc:
            self.log_line.emit(f"Error: {exc}")
            self._btn_state.emit(True)
            self._status_sig.emit(f"Error: {exc}", "#f85149")

    def _reveal(self) -> None:
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


# ─── DXF → SVG sub-tab ───────────────────────────────────────────────────────


class _SvgSubTab(QWidget):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)  # text, color

    def __init__(
        self,
        parent: QWidget | None = None,
        settings: dict | None = None,
    ):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Input DXF")
        src_row = QHBoxLayout()
        self._src_edit = QLineEdit()
        self._src_edit.setPlaceholderText("Select a .dxf file…")
        self._src_edit.setToolTip("Path to the DXF file to convert to SVG")
        src_btn = QPushButton("Browse")
        src_btn.setFixedWidth(70)
        src_btn.setToolTip("Browse for a DXF file to convert")
        src_btn.clicked.connect(self._browse_src)
        src_row.addWidget(self._src_edit, stretch=1)
        src_row.addWidget(src_btn)
        layout.addLayout(src_row)

        _section_label(layout, "Output SVG")
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Leave blank to auto-name…")
        self._out_edit.setToolTip("Destination SVG path (blank = same name as input with .svg extension)")
        out_btn = QPushButton("Browse")
        out_btn.setFixedWidth(70)
        out_btn.setToolTip("Choose where to save the SVG file")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        layout.addLayout(out_row)

        self._btn = QPushButton("Convert to SVG")
        self._btn.setMinimumHeight(38)
        self._btn.setToolTip("Convert the DXF polylines to an SVG vector graphic")
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)
        layout.addWidget(self._btn)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setMinimumHeight(26)
        self._reveal_btn.setToolTip("Open the converted SVG file location in Finder")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal)
        layout.addWidget(self._reveal_btn)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        layout.addStretch()

        self._btn_state.connect(self._btn.setEnabled)
        self._reveal_state.connect(self._reveal_btn.setEnabled)
        self._status_sig.connect(self._set_status)

    def _browse_src(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select DXF file",
            "",
            "DXF files (*.dxf *.DXF);;All files (*)",
        )
        if path:
            self._src_edit.setText(path)
            if not self._out_edit.text().strip():
                self._out_edit.setText(str(Path(path).with_suffix(".svg")))

    def _browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save SVG",
            "",
            "SVG files (*.svg);;All files (*)",
        )
        if path:
            self._out_edit.setText(path)

    def _set_status(self, text: str, color: str = "#8b949e") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _run(self) -> None:
        src = self._src_edit.text().strip()
        if not src:
            QMessageBox.critical(self, "Error", "Please select an input DXF file.")
            return
        out = self._out_edit.text().strip()
        if not out:
            out = str(Path(src).with_suffix(".svg"))
            self._out_edit.setText(out)
        self._btn.setEnabled(False)
        self._set_status("Converting…")
        threading.Thread(target=self._convert, args=(src, out), daemon=True).start()

    def _convert(self, src: str, out: str) -> None:
        try:
            stats = dxf_to_svg(src, out)
            msg = (
                f"Done — {stats['polylines']} polyline(s)"
                f"  · {stats['width_mm']:.1f} × {stats['height_mm']:.1f} mm"
            )
            self.log_line.emit(msg)
            self._btn_state.emit(True)
            self._reveal_state.emit(True)
            self._status_sig.emit("Done", "#3fb950")
            self._last_out = out
            self.preview_path.emit(src)
        except Exception as exc:
            self.log_line.emit(f"Error: {exc}")
            self._btn_state.emit(True)
            self._status_sig.emit(f"Error: {exc}", "#f85149")

    def _reveal(self) -> None:
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


# ─── SVG → DXF sub-tab ─────────────────────────────────────────────────────


class _SvgToDxfSubTab(QWidget):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        settings: dict | None = None,
    ):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        _section_label(layout, "Input SVG")
        src_row = QHBoxLayout()
        self._src_edit = QLineEdit()
        self._src_edit.setPlaceholderText("Select a .svg file…")
        self._src_edit.setToolTip("Path to the SVG file to convert")
        src_btn = QPushButton("Browse")
        src_btn.setFixedWidth(70)
        src_btn.clicked.connect(self._browse_src)
        src_row.addWidget(self._src_edit, stretch=1)
        src_row.addWidget(src_btn)
        layout.addLayout(src_row)

        _section_label(layout, "Output DXF")
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Leave blank to auto-name…")
        self._out_edit.setToolTip(
            "Destination DXF path (blank = same name as input with .dxf)"
        )
        out_btn = QPushButton("Browse")
        out_btn.setFixedWidth(70)
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        layout.addLayout(out_row)

        self._btn = QPushButton("Convert to DXF")
        self._btn.setMinimumHeight(38)
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)
        layout.addWidget(self._btn)

        self._reveal_btn = QPushButton("Show in Finder")
        self._reveal_btn.setMinimumHeight(26)
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._reveal)
        layout.addWidget(self._reveal_btn)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        layout.addStretch()

        self._btn_state.connect(self._btn.setEnabled)
        self._reveal_state.connect(self._reveal_btn.setEnabled)
        self._status_sig.connect(self._set_status)

    def _browse_src(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SVG file",
            "",
            "SVG files (*.svg *.SVG);;All files (*)",
        )
        if path:
            self._src_edit.setText(path)
            if not self._out_edit.text().strip():
                self._out_edit.setText(str(Path(path).with_suffix(".dxf")))

    def _browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save DXF",
            "",
            "DXF files (*.dxf);;All files (*)",
        )
        if path:
            self._out_edit.setText(path)

    def _set_status(self, text: str, color: str = "#8b949e") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _run(self) -> None:
        src = self._src_edit.text().strip()
        if not src:
            QMessageBox.critical(self, "Error", "Please select an input SVG file.")
            return
        out = self._out_edit.text().strip()
        if not out:
            out = str(Path(src).with_suffix(".dxf"))
            self._out_edit.setText(out)
        self._btn.setEnabled(False)
        self._set_status("Converting…")
        threading.Thread(target=self._convert, args=(src, out), daemon=True).start()

    def _convert(self, src: str, out: str) -> None:
        try:
            stats = svg_to_dxf(src, out)
            msg = (
                f"Done — {stats['polylines']} polyline(s)"
                f"  · {stats['width_mm']:.1f} × {stats['height_mm']:.1f} mm"
            )
            self.log_line.emit(msg)
            self._btn_state.emit(True)
            self._reveal_state.emit(True)
            self._status_sig.emit("Done", "#3fb950")
            self._last_out = out
            self.preview_path.emit(out)
        except Exception as exc:
            self.log_line.emit(f"Error: {exc}")
            self._btn_state.emit(True)
            self._status_sig.emit(f"Error: {exc}", "#f85149")

    def _reveal(self) -> None:
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


# Keep old name as alias for backward compatibility
FviTab = UtilitiesTab
