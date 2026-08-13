"""FVI-to-DXF task form."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox, QVBoxLayout, QWidget

from simple_stipple.engine.formats.service import DxfService, FviNoGeometryError
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_OK, STATUS_WARN

from .base import _ConversionSubTab

LOGGER = logging.getLogger(__name__)


class FviSubTab(_ConversionSubTab):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _out_dir_sig = Signal(str)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out_dir: str | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        self._build(layout)

    def _build(self, layout: QVBoxLayout) -> None:
        self._build_mode_row(layout, "Single file", "Folder (batch)")
        self._build_subfolders_checkbox(
            layout,
            "Find FVI files recursively and preserve their folder structure in the output",
        )
        self._src_edit, self._out_edit = self._build_input_output_rows(
            layout,
            src_heading="INPUT",
            src_placeholder="Select a .fvi file or folder…",
            src_btn_tooltip="Browse for an FVI source file or folder",
            out_heading="OUTPUT",
            out_placeholder="Optional (blank = same as source)…",
            out_btn_tooltip="Choose an output folder for converted files",
            on_browse_src=self._browse_src,
            on_browse_out=self._browse_out,
        )

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._build_action_row("Convert")

        self._btn_state.connect(self._btn.setEnabled)
        self._out_dir_sig.connect(self._set_output_dir)
        # Note: _status_sig intentionally NOT connected to avoid sidebar popups
        # that appear after conversions complete.
        self._bind_readiness(self._src_edit)

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

    def _on_mode_switch(self, mode: str) -> None:
        pass

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
        if self._running:
            # Guards against re-entrancy independent of button/footer state —
            # switching tools re-enables the page's footer CTA even while
            # this subtab's own background thread is still converting.
            return
        src = self._src_edit.text().strip()
        if not src:
            self._status_sig.emit("Choose an FVI source file or folder first.", STATUS_WARN)
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
        out_dir = self._out_edit.text().strip() or None
        source_files = self._collect_files(src, ".fvi", self._include_subfolders.isChecked())
        destinations = []
        for source in source_files:
            if out_dir:
                relative = source.relative_to(src_path) if src_path.is_dir() else Path(source.name)
                destinations.append(Path(out_dir) / relative.with_suffix(".dxf"))
            else:
                destinations.append(source.with_suffix(".dxf"))
        if not self._confirm_replace(destinations):
            return
        cancel_event = self._start_job()
        self._btn.setEnabled(False)
        self._thread = threading.Thread(
            target=self._convert,
            args=(src, out_dir, cancel_event, self._include_subfolders.isChecked()),
            daemon=True,
        )
        self._thread.start()

    def _convert(
        self,
        src: str,
        out_dir: str | None,
        cancel_event: threading.Event,
        include_subfolders: bool = True,
    ) -> None:
        files = self._collect_files(src, ".fvi", include_subfolders)

        if not files:
            self.log_line.emit("No .fvi files found.")
            self._running = False
            self._refresh_readiness()
            self._status_sig.emit("No FVI files found", STATUS_WARN)
            return

        self.log_line.emit(f"Found {len(files)} file(s)\n")
        self._begin_batch(len(files))
        ok = err = warned = skipped = 0
        last_dxf: str | None = None
        for index, fvi in enumerate(files, start=1):
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            self._report_batch_progress(index, "Converting", fvi.name)
            if out_dir:
                src_path = Path(src)
                relative = fvi.relative_to(src_path) if src_path.is_dir() else Path(fvi.name)
                dest = Path(out_dir) / relative.with_suffix(".dxf")
            else:
                dest = fvi.with_suffix(".dxf")
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                report = DxfService.convert_fvi_to_dxf(fvi, dest)
                src_path = Path(src)
                display_source = str(fvi.relative_to(src_path)) if src_path.is_dir() else fvi.name
                display_dest = str(dest.relative_to(Path(out_dir))) if out_dir else dest.name
                warning = DxfService.summarize_fvi_import(report)
                marker = "⚠" if warning else "✓"
                self.log_line.emit(f"  {marker}  {display_source}  →  {display_dest}")
                if warning:
                    warned += 1
                    self.log_line.emit(f"     Warning: {warning}")
                ok += 1
                last_dxf = str(dest)
            except FviNoGeometryError as exc:
                src_path = Path(src)
                display_source = str(fvi.relative_to(src_path)) if src_path.is_dir() else fvi.name
                self.log_line.emit(f"  ⚠  {display_source}: skipped — {exc}")
                skipped += 1
            except Exception as exc:
                LOGGER.exception("Could not convert FVI %s", fvi)
                self.log_line.emit(f"  ✗  {fvi.name}: {exc}")
                err += 1
            finally:
                self._record_batch_item(index)

        self.log_line.emit(
            f"\nDone — {ok} converted, {warned} with warning(s), {skipped} skipped, {err} error(s)."
        )
        self._running = False
        self._refresh_readiness()
        if files:
            final_dir = out_dir or str(files[0].parent)
            self._out_dir_sig.emit(final_dir)
        if err == 0 and ok > 0:
            self._status_sig.emit(
                f"Done — {ok} converted"
                + (f", {warned} with warnings" if warned else "")
                + (f", {skipped} skipped" if skipped else ""),
                STATUS_WARN if warned or skipped else STATUS_OK,
            )
        elif err > 0:
            self._status_sig.emit(f"{err} error(s)", STATUS_ERR)
        elif skipped:
            self._status_sig.emit(
                f"Done — {skipped} empty/unsupported file(s) skipped", STATUS_WARN
            )
        if last_dxf:
            self.preview_path.emit(last_dxf)
