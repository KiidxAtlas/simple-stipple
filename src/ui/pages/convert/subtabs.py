"""Subtab widgets for utilities/conversion workflows."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.backend.dxf.fix import fix_dxf
from src.backend.dxf.fvi import convert_fvi_to_dxf
from src.backend.dxf.svg import dxf_to_svg
from src.backend.io import svg_to_dxf


def _append_ignored_entities_note(msg: str, stats: dict) -> str:
    ignored = int(stats.get("ignored_entities", 0) or 0)
    if not ignored:
        return msg
    ignored_summary = stats.get("ignored_entity_summary")
    if ignored_summary:
        return f"{msg}  · ignored {ignored} ({ignored_summary})"
    return f"{msg}  · ignored {ignored} unsupported entity(s)"


class _StatusMixin:
    """Mixin that provides _set_status() for subtabs with a self._status QLabel."""

    _status: QLabel

    def _set_status(self, text: str, color: str = "#8b949e") -> None:
        if not text:
            self._status.setVisible(False)
            return
        self._status.setVisible(True)
        self._status.setText(text)
        if color == "#3fb950":
            role = "status-ok"
        elif color == "#f85149":
            role = "status-err"
        else:
            role = "status-neutral"
        self._status.setProperty("role", role)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)


class FviSubTab(_StatusMixin, QWidget):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _out_dir_sig = Signal(str)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out_dir: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
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

        _in_lbl = QLabel("INPUT")
        _in_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_in_lbl)
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

        _out_lbl = QLabel("OUTPUT")
        _out_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_out_lbl)
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Optional (blank = same as source)…")
        self._out_edit.setToolTip(
            "Destination folder for converted DXF files (blank = alongside source)"
        )
        out_btn = QPushButton("Browse")
        out_btn.setFixedWidth(70)
        out_btn.setToolTip("Choose an output folder for converted files")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        layout.addLayout(out_row)

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._btn = QPushButton("Convert")
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setVisible(False)

        self._btn_state.connect(self._btn.setEnabled)
        self._out_dir_sig.connect(self._set_output_dir)
        self._status_sig.connect(self._set_status)

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

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
            except (OSError, ValueError, RuntimeError) as exc:
                self.log_line.emit(f"  ✗  {fvi.name}: {exc}")
                err += 1

        self.log_line.emit(f"\nDone — {ok} converted, {err} error(s).")
        self._btn_state.emit(True)
        if files:
            final_dir = out_dir or str(files[0].parent)
            self._out_dir_sig.emit(final_dir)
        if err == 0 and ok > 0:
            self._status_sig.emit(f"Done — {ok} file(s) converted", "#3fb950")
        elif err > 0:
            self._status_sig.emit(f"{err} error(s)", "#f85149")
        if last_dxf:
            self.preview_path.emit(last_dxf)


class FixerSubTab(_StatusMixin, QWidget):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        _in_lbl = QLabel("INPUT")
        _in_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_in_lbl)
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

        _out_lbl = QLabel("OUTPUT")
        _out_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_out_lbl)
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Leave blank to overwrite input…")
        self._out_edit.setToolTip(
            "Save repaired DXF here (blank = overwrite the input file)"
        )
        out_btn = QPushButton("Browse")
        out_btn.setFixedWidth(70)
        out_btn.setToolTip("Choose where to save the repaired DXF file")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        layout.addLayout(out_row)

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._btn = QPushButton("Fix DXF")
        self._btn.setMinimumHeight(38)
        self._btn.setToolTip(
            "Close open polylines, simplify, and discard degenerate geometry"
        )
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setVisible(False)

        self._btn_state.connect(self._btn.setEnabled)
        self._status_sig.connect(self._set_status)

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

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
            msg = _append_ignored_entities_note(msg, stats)
            self.log_line.emit(msg)
            self._btn_state.emit(True)
            self._reveal_state.emit(True)
            self._status_sig.emit("Done", "#3fb950")
            self._last_out = out
            self.preview_path.emit(out)
        except (OSError, ValueError, RuntimeError) as exc:
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


class SvgSubTab(_StatusMixin, QWidget):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        _in_lbl = QLabel("INPUT")
        _in_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_in_lbl)
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

        _out_lbl = QLabel("OUTPUT")
        _out_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_out_lbl)
        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Leave blank to auto-name…")
        self._out_edit.setToolTip(
            "Destination SVG path (blank = same name as input with .svg extension)"
        )
        out_btn = QPushButton("Browse")
        out_btn.setFixedWidth(70)
        out_btn.setToolTip("Choose where to save the SVG file")
        out_btn.clicked.connect(self._browse_out)
        out_row.addWidget(self._out_edit, stretch=1)
        out_row.addWidget(out_btn)
        layout.addLayout(out_row)

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._btn = QPushButton("Convert to SVG")
        self._btn.setMinimumHeight(38)
        self._btn.setToolTip("Convert the DXF polylines to an SVG vector graphic")
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setVisible(False)

        self._btn_state.connect(self._btn.setEnabled)
        self._status_sig.connect(self._set_status)

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

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
            msg = _append_ignored_entities_note(msg, stats)
            self.log_line.emit(msg)
            self._btn_state.emit(True)
            self._reveal_state.emit(True)
            self._status_sig.emit("Done", "#3fb950")
            self._last_out = out
            self.preview_path.emit(src)
        except (OSError, ValueError, RuntimeError) as exc:
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


class SvgToDxfSubTab(_StatusMixin, QWidget):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        _in_lbl = QLabel("INPUT")
        _in_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_in_lbl)
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

        _out_lbl = QLabel("OUTPUT")
        _out_lbl.setProperty("role", "eyebrow")
        layout.addWidget(_out_lbl)
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

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._btn = QPushButton("Convert to DXF")
        self._btn.setMinimumHeight(38)
        self._btn.setProperty("role", "primary")
        self._btn.clicked.connect(self._run)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setVisible(False)

        self._btn_state.connect(self._btn.setEnabled)
        self._status_sig.connect(self._set_status)

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

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
        except (OSError, ValueError, RuntimeError) as exc:
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


__all__ = [
    "FixerSubTab",
    "FviSubTab",
    "SvgSubTab",
    "SvgToDxfSubTab",
]
