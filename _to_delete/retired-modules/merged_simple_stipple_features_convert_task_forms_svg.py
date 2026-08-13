"""DXF/SVG conversion task forms."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QVBoxLayout, QWidget

from simple_stipple.engine.formats.service import DxfService
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_OK, STATUS_WARN

from .base import _append_ignored_entities_note, _ConversionSubTab

LOGGER = logging.getLogger(__name__)


class SvgSubTab(_ConversionSubTab):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._running = False
        self._thread: threading.Thread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        self._build_mode_row(layout, "Single file", "Folder (batch)")
        self._build_subfolders_checkbox(
            layout,
            "Find DXF files recursively and preserve their folder structure in the output",
        )
        self._src_edit, self._out_edit = self._build_input_output_rows(
            layout,
            src_heading="INPUT",
            src_placeholder="Select a .dxf file…",
            src_btn_tooltip="Browse for a DXF file to convert",
            src_tooltip="Path to the DXF file to convert to SVG",
            out_heading="OUTPUT",
            out_placeholder="Leave blank to auto-name…",
            out_btn_tooltip="Choose where to save the SVG file",
            out_tooltip="Destination SVG path (blank = same name as input with .svg extension)",
            on_browse_src=self._browse_src,
            on_browse_out=self._browse_out,
        )

        layout.addStretch()

        self._build_action_row(
            "Convert to SVG", "Convert the DXF polylines to an SVG vector graphic"
        )

        self._btn_state.connect(self._btn.setEnabled)
        # Note: _status_sig intentionally NOT connected to avoid sidebar popups
        # that appear after conversions complete.
        self._bind_readiness(self._src_edit)

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

    def _on_mode_switch(self, mode: str) -> None:
        batch = mode == "batch"
        self._src_edit.clear()
        self._out_edit.clear()
        self._src_edit.setPlaceholderText(
            "Select a folder containing DXF files…" if batch else "Select a .dxf file…"
        )
        self._out_edit.setPlaceholderText(
            "Optional output folder (blank = sibling SVG folder)…"
            if batch
            else "Leave blank to auto-name…"
        )

    def _is_batch(self) -> bool:
        return self._mode_batch.property("active") is True

    def _browse_src(self) -> None:
        if self._is_batch():
            path = QFileDialog.getExistingDirectory(self, "Select folder containing DXF files", "")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select DXF file",
                "",
                "DXF files (*.dxf *.DXF);;All files (*)",
            )
        if path:
            self._src_edit.setText(path)
            if not self._is_batch() and not self._out_edit.text().strip():
                self._out_edit.setText(str(Path(path).with_suffix(".svg")))

    def _browse_out(self) -> None:
        if self._is_batch():
            path = QFileDialog.getExistingDirectory(self, "Select output folder", "")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save SVG",
                "",
                "SVG files (*.svg);;All files (*)",
            )
        if path:
            self._out_edit.setText(path)

    def _run(self) -> None:
        if self._running:
            return
        src = self._src_edit.text().strip()
        if not src:
            self._status_sig.emit("Choose an input DXF file or folder first.", STATUS_WARN)
            return
        source_path = Path(src)
        expected = source_path.is_dir() if self._is_batch() else source_path.is_file()
        if not expected:
            QMessageBox.warning(
                self,
                "Source Not Found",
                f"The source {'folder' if self._is_batch() else 'file'} does not exist:\n{src}",
            )
            return
        if self._is_batch():
            out_dir = self._out_edit.text().strip()
            if not out_dir:
                QMessageBox.warning(
                    self,
                    "Output Folder Required",
                    "Please select an output folder for batch conversion.",
                )
                return
            include_subfolders = self._include_subfolders.isChecked()
            files = self._collect_files(src, ".dxf", include_subfolders)
            destinations = [
                Path(out_dir) / file.relative_to(source_path).with_suffix(".svg") for file in files
            ]
            if not self._confirm_replace(destinations):
                return
            cancel_event = self._start_job()
            self._thread = threading.Thread(
                target=self._convert_batch,
                args=(src, out_dir, cancel_event, include_subfolders),
                daemon=True,
            )
        else:
            out = self._out_edit.text().strip()
            if not out:
                out = str(Path(src).with_suffix(".svg"))
                self._out_edit.setText(out)
            if Path(src).resolve() == Path(out).resolve():
                QMessageBox.warning(
                    self, "Unsafe Output Path", "Output must not overwrite the input DXF."
                )
                return
            if not self._confirm_replace([Path(out)]):
                return
            cancel_event = self._start_job()
            self._thread = threading.Thread(
                target=self._convert, args=(src, out, cancel_event), daemon=True
            )
        self._btn.setEnabled(False)
        self._set_status("Converting…")
        self._thread.start()

    def _convert(self, src: str, out: str, cancel_event: threading.Event) -> None:
        try:
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            stats = DxfService.dxf_to_svg(src, out)
            msg = (
                f"Done — {stats['polylines']} polyline(s)"
                f"  · {stats['width_mm']:.1f} × {stats['height_mm']:.1f} mm"
            )
            msg = _append_ignored_entities_note(msg, stats)
            self.log_line.emit(msg)
            self._running = False
            self._refresh_readiness()
            self._reveal_state.emit(True)
            self._status_sig.emit("Done", STATUS_OK)
            self._last_out = out
            self.preview_path.emit(src)
        except Exception as exc:
            LOGGER.exception("Could not convert DXF to SVG: %s", src)
            self.log_line.emit(f"Error: {exc}")
            self._running = False
            self._refresh_readiness()
            self._status_sig.emit(f"Error: {exc}", STATUS_ERR)

    def _convert_batch(
        self,
        src: str,
        out_dir: str,
        cancel_event: threading.Event,
        include_subfolders: bool = True,
    ) -> None:
        files = self._collect_files(src, ".dxf", include_subfolders)
        if not files:
            self._running = False
            self._refresh_readiness()
            self._status_sig.emit("No DXF files found", STATUS_WARN)
            self.log_line.emit("No DXF files found in the selected folder.")
            return
        root = Path(src)
        self.log_line.emit(f"Found {len(files)} file(s)\n")
        self._begin_batch(len(files))
        ok = err = 0
        for index, dxf in enumerate(files, start=1):
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            relative = dxf.relative_to(root)
            svg = Path(out_dir) / relative.with_suffix(".svg")
            self._report_batch_progress(index, "Converting", dxf.name)
            try:
                svg.parent.mkdir(parents=True, exist_ok=True)
                stats = DxfService.dxf_to_svg(dxf, svg)
                msg = (
                    f"  ✓  {relative} → {svg.name}"
                    f"  ({stats['polylines']} polyline(s), "
                    f"{stats['width_mm']:.1f} × {stats['height_mm']:.1f} mm)"
                )
                self.log_line.emit(msg)
                ok += 1
            except Exception as exc:
                LOGGER.exception("Could not convert %s to SVG", dxf)
                self.log_line.emit(f"  ✗  {relative}: {exc}")
                err += 1
            finally:
                self._record_batch_item(index)
        self._running = False
        self._refresh_readiness()
        self._last_out = out_dir
        self._status_sig.emit(
            f"Done — {ok} converted" + (f", {err} error(s)" if err else ""),
            STATUS_WARN if err else STATUS_OK,
        )
        self.log_line.emit(f"\nDone — {ok} converted" + (f", {err} error(s)" if err else ""))


class SvgToDxfSubTab(_ConversionSubTab):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._running = False
        self._thread: threading.Thread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        self._build_mode_row(layout, "Single file", "Folder (batch)")
        self._build_subfolders_checkbox(
            layout,
            "Find SVG files recursively and preserve their folder structure in the output",
        )
        self._src_edit, self._out_edit = self._build_input_output_rows(
            layout,
            src_heading="INPUT",
            src_placeholder="Select a .svg file…",
            src_btn_tooltip="Browse for an SVG file to convert",
            src_tooltip="Path to the SVG file to convert",
            out_heading="OUTPUT",
            out_placeholder="Leave blank to auto-name…",
            out_btn_tooltip="Choose where to save the DXF file",
            out_tooltip="Destination DXF path (blank = same name as input with .dxf)",
            on_browse_src=self._browse_src,
            on_browse_out=self._browse_out,
        )

        layout.addStretch()

        self._build_action_row("Convert to DXF")

        self._btn_state.connect(self._btn.setEnabled)
        # Note: _status_sig intentionally NOT connected to avoid sidebar popups
        # that appear after conversions complete.
        self._bind_readiness(self._src_edit)

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

    def _on_mode_switch(self, mode: str) -> None:
        batch = mode == "batch"
        self._src_edit.clear()
        self._out_edit.clear()
        self._src_edit.setPlaceholderText(
            "Select a folder containing SVG files…" if batch else "Select a .svg file…"
        )
        self._out_edit.setPlaceholderText(
            "Optional output folder (blank = sibling DXF folder)…"
            if batch
            else "Leave blank to auto-name…"
        )

    def _is_batch(self) -> bool:
        return self._mode_batch.property("active") is True

    def _browse_src(self) -> None:
        if self._is_batch():
            path = QFileDialog.getExistingDirectory(self, "Select folder containing SVG files", "")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select SVG file",
                "",
                "SVG files (*.svg *.SVG);;All files (*)",
            )
        if path:
            self._src_edit.setText(path)
            if not self._is_batch() and not self._out_edit.text().strip():
                self._out_edit.setText(str(Path(path).with_suffix(".dxf")))

    def _browse_out(self) -> None:
        if self._is_batch():
            path = QFileDialog.getExistingDirectory(self, "Select output folder", "")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save DXF",
                "",
                "DXF files (*.dxf);;All files (*)",
            )
        if path:
            self._out_edit.setText(path)

    def _run(self) -> None:
        if self._running:
            return
        src = self._src_edit.text().strip()
        if not src:
            self._status_sig.emit("Choose an input SVG file or folder first.", STATUS_WARN)
            return
        source_path = Path(src)
        expected = source_path.is_dir() if self._is_batch() else source_path.is_file()
        if not expected:
            QMessageBox.warning(
                self,
                "Source Not Found",
                f"The source {'folder' if self._is_batch() else 'file'} does not exist:\n{src}",
            )
            return
        if self._is_batch():
            out_dir = self._out_edit.text().strip()
            if not out_dir:
                QMessageBox.warning(
                    self,
                    "Output Folder Required",
                    "Please select an output folder for batch conversion.",
                )
                return
            include_subfolders = self._include_subfolders.isChecked()
            files = self._collect_files(src, ".svg", include_subfolders)
            destinations = [
                Path(out_dir) / file.relative_to(source_path).with_suffix(".dxf") for file in files
            ]
            if not self._confirm_replace(destinations):
                return
            cancel_event = self._start_job()
            self._thread = threading.Thread(
                target=self._convert_batch,
                args=(src, out_dir, cancel_event, include_subfolders),
                daemon=True,
            )
        else:
            out = self._out_edit.text().strip()
            if not out:
                out = str(Path(src).with_suffix(".dxf"))
                self._out_edit.setText(out)
            if Path(src).resolve() == Path(out).resolve():
                QMessageBox.warning(
                    self, "Unsafe Output Path", "Output must not overwrite the input SVG."
                )
                return
            if not self._confirm_replace([Path(out)]):
                return
            cancel_event = self._start_job()
            self._thread = threading.Thread(
                target=self._convert, args=(src, out, cancel_event), daemon=True
            )
        self._btn.setEnabled(False)
        self._set_status("Converting…")
        self._thread.start()

    def _convert(self, src: str, out: str, cancel_event: threading.Event) -> None:
        try:
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            stats = DxfService.svg_to_dxf(src, out)
            msg = (
                f"Done — {stats['polylines']} polyline(s)"
                f"  · {stats['width_mm']:.1f} × {stats['height_mm']:.1f} mm"
            )
            unsupported_paths = int(stats.get("unsupported_paths", 0) or 0)
            if unsupported_paths:
                msg += f" · skipped {unsupported_paths} unsupported curved path(s)"
            unsupported_features = tuple(stats.get("unsupported_features", ()))
            if unsupported_features:
                msg += " · unsupported SVG features: " + ", ".join(unsupported_features)
            self.log_line.emit(msg)
            self._running = False
            self._refresh_readiness()
            self._reveal_state.emit(True)
            self._status_sig.emit(
                "Done"
                if not unsupported_paths and not unsupported_features
                else "Done with warnings",
                STATUS_OK if not unsupported_paths and not unsupported_features else STATUS_WARN,
            )
            self._last_out = out
            self.preview_path.emit(out)
        except Exception as exc:
            LOGGER.exception("Could not convert SVG to DXF: %s", src)
            self.log_line.emit(f"Error: {exc}")
            self._running = False
            self._refresh_readiness()
            self._status_sig.emit(f"Error: {exc}", STATUS_ERR)

    def _convert_batch(
        self,
        src: str,
        out_dir: str,
        cancel_event: threading.Event,
        include_subfolders: bool = True,
    ) -> None:
        files = self._collect_files(src, ".svg", include_subfolders)
        if not files:
            self._running = False
            self._refresh_readiness()
            self._status_sig.emit("No SVG files found", STATUS_WARN)
            self.log_line.emit("No SVG files found in the selected folder.")
            return
        root = Path(src)
        self.log_line.emit(f"Found {len(files)} file(s)\n")
        self._begin_batch(len(files))
        ok = err = 0
        for index, svg in enumerate(files, start=1):
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            relative = svg.relative_to(root)
            dxf = Path(out_dir) / relative.with_suffix(".dxf")
            self._report_batch_progress(index, "Converting", svg.name)
            try:
                dxf.parent.mkdir(parents=True, exist_ok=True)
                stats = DxfService.svg_to_dxf(svg, dxf)
                msg = (
                    f"  ✓  {relative} → {dxf.name}"
                    f"  ({stats['polylines']} polyline(s), "
                    f"{stats['width_mm']:.1f} × {stats['height_mm']:.1f} mm)"
                )
                unsupported_paths = int(stats.get("unsupported_paths", 0) or 0)
                if unsupported_paths:
                    msg += f" · skipped {unsupported_paths} curved path(s)"
                unsupported_features = tuple(stats.get("unsupported_features", ()))
                if unsupported_features:
                    msg += " · unsupported: " + ", ".join(unsupported_features)
                self.log_line.emit(msg)
                ok += 1
            except Exception as exc:
                LOGGER.exception("Could not convert %s to DXF", svg)
                self.log_line.emit(f"  ✗  {relative}: {exc}")
                err += 1
            finally:
                self._record_batch_item(index)
        self._running = False
        self._refresh_readiness()
        self._last_out = out_dir
        self._status_sig.emit(
            f"Done — {ok} converted" + (f", {err} error(s)" if err else ""),
            STATUS_WARN if err else STATUS_OK,
        )
        self.log_line.emit(f"\nDone — {ok} converted" + (f", {err} error(s)" if err else ""))


__all__ = [
    "SvgSubTab",
    "SvgToDxfSubTab",
]


# ════════════════════════════════════════════════════════════════════════════
# Page shell
# ════════════════════════════════════════════════════════════════════════════
