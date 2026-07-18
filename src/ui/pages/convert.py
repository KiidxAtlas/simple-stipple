"""Convert page — batch DXF/FVI/SVG conversion tools.

Two previously-separate modules merged here — ``subtabs.py`` (the 4 tool
sub-tabs: FVI->DXF, DXF fix, DXF->SVG, SVG->DXF) and ``tab.py`` (the page
shell that hosts them) are tightly coupled with no independent reason to
stay split.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.backend.dxf.fix import fix_dxf
from src.backend.dxf.fvi import FviNoGeometryError, convert_fvi_to_dxf, summarize_fvi_import
from src.backend.dxf.io import load_dxf_polylines
from src.backend.dxf.svg_dxf import dxf_to_svg, svg_to_dxf
from src.ui.canvas.canvas_runtime import CanvasGridModule
from src.ui.canvas.dxf_canvas import DxfCanvas
from src.ui.components import (
    browse_row,
    content_splitter,
    set_status_label,
    surface_frame,
)
from src.ui.pages.base import BasePage
from src.ui.style.theme import STATUS_ERR, STATUS_NEUTRAL, STATUS_OK, STATUS_WARN
from src.ui.widgets.canvas.status_strip import CanvasStatusStrip

LOGGER = logging.getLogger(__name__)

# ── Page default settings ────────────────────────────────────────────────
DEFAULT_GRID_VISIBLE = True
DEFAULT_GRID_SPACING_MM = 1.0
LOG_PANEL_MAX_HEIGHT = 140


# ══════════════════════════════════════════════════════════════════════════
# Tool sub-tabs
# ══════════════════════════════════════════════════════════════════════════


def _append_ignored_entities_note(msg: str, stats: dict) -> str:
    ignored = int(stats.get("ignored_entities", 0) or 0)
    if not ignored:
        return msg
    ignored_summary = stats.get("ignored_entity_summary")
    if ignored_summary:
        return f"{msg}  · ignored {ignored} ({ignored_summary})"
    return f"{msg}  · ignored {ignored} unsupported entity(s)"


class _ConversionSubTab(QWidget):
    """Shared cancellation, status, and file-picker behavior for conversion tools."""

    _status: QLabel
    _thread: threading.Thread | None

    def _start_job(self) -> threading.Event:
        self._cancel_event = threading.Event()
        self._running = True
        return self._cancel_event

    def cancel(self) -> None:
        event = getattr(self, "_cancel_event", None)
        if event is not None:
            event.set()
        if getattr(self, "_running", False):
            self._status_sig.emit("Cancelling…", STATUS_WARN)

    def _finish_cancelled(self) -> None:
        self._running = False
        self._btn_state.emit(True)
        self._status_sig.emit("Cancelled", STATUS_WARN)
        self.log_line.emit("Cancelled.")

    def shutdown(self) -> None:
        """Called by ``ConvertPage.shutdown()`` (in turn called by
        ``App.closeEvent``) before the window tears down. These conversions
        have no cooperative-cancellation support, so this is a best-effort
        wait for an in-flight one to finish rather than abandoning it
        outright — the thread is daemon=True either way, so a timeout here
        never blocks the app from actually closing."""
        self.cancel()
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _set_status(self, text: str, color: str = STATUS_NEUTRAL) -> None:
        set_status_label(self._status, text, color)

    def _add_picker_row(
        self,
        layout: QVBoxLayout,
        heading: str,
        placeholder: str,
        *,
        tooltip: str = "",
        btn_tooltip: str = "",
        on_browse,
    ) -> QLineEdit:
        """Add a section label + line-edit + Browse button row; return the edit."""
        return browse_row(
            layout,
            heading=heading,
            placeholder=placeholder,
            tooltip=tooltip,
            btn_tooltip=btn_tooltip,
            on_browse=on_browse,
        )


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

        self._include_subfolders = QCheckBox("Include subfolders")
        self._include_subfolders.setChecked(True)
        self._include_subfolders.setToolTip(
            "Find FVI files recursively and preserve their folder structure in the output"
        )
        self._include_subfolders.setVisible(False)
        layout.addWidget(self._include_subfolders)

        _in_lbl = QLabel("INPUT")
        _in_lbl.setProperty("role", "section-label")
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
        _out_lbl.setProperty("role", "section-label")
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
        self._include_subfolders.setVisible(mode == "batch")

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
        source_files = (
            [src_path]
            if src_path.is_file()
            else sorted(
                path
                for path in (
                    src_path.rglob("*")
                    if self._include_subfolders.isChecked()
                    else src_path.iterdir()
                )
                if path.is_file() and path.suffix.casefold() == ".fvi"
            )
        )
        destinations = []
        for source in source_files:
            if out_dir:
                relative = source.relative_to(src_path) if src_path.is_dir() else Path(source.name)
                destinations.append(Path(out_dir) / relative.with_suffix(".dxf"))
            else:
                destinations.append(source.with_suffix(".dxf"))
        existing = [path for path in destinations if path.exists()]
        if existing:
            answer = QMessageBox.question(
                self,
                "Replace Existing DXF Files?",
                f"{len(existing)} destination file(s) already exist and will be replaced.\n\n"
                + "\n".join(path.name for path in existing[:5])
                + ("\n…" if len(existing) > 5 else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
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
        p = Path(src)
        if p.is_file():
            files = [p]
        else:
            iterator = p.rglob("*") if include_subfolders else p.iterdir()
            files = sorted(
                (path for path in iterator if path.is_file() and path.suffix.casefold() == ".fvi"),
                key=lambda path: str(path.relative_to(p)).casefold(),
            )

        if not files:
            self.log_line.emit("No .fvi files found.")
            self._running = False
            self._btn_state.emit(True)
            self._status_sig.emit("No FVI files found", STATUS_WARN)
            return

        self.log_line.emit(f"Found {len(files)} file(s)\n")
        ok = err = warned = skipped = 0
        last_dxf: str | None = None
        for fvi in files:
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            if out_dir:
                relative = fvi.relative_to(p) if p.is_dir() else Path(fvi.name)
                dest = Path(out_dir) / relative.with_suffix(".dxf")
            else:
                dest = fvi.with_suffix(".dxf")
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                report = convert_fvi_to_dxf(fvi, dest)
                display_source = str(fvi.relative_to(p)) if p.is_dir() else fvi.name
                display_dest = str(dest.relative_to(Path(out_dir))) if out_dir else dest.name
                warning = summarize_fvi_import(report)
                marker = "⚠" if warning else "✓"
                self.log_line.emit(f"  {marker}  {display_source}  →  {display_dest}")
                if warning:
                    warned += 1
                    self.log_line.emit(f"     Warning: {warning}")
                ok += 1
                last_dxf = str(dest)
            except FviNoGeometryError as exc:
                display_source = str(fvi.relative_to(p)) if p.is_dir() else fvi.name
                self.log_line.emit(f"  ⚠  {display_source}: skipped — {exc}")
                skipped += 1
            except Exception as exc:
                LOGGER.exception("Could not convert FVI %s", fvi)
                self.log_line.emit(f"  ✗  {fvi.name}: {exc}")
                err += 1

        self.log_line.emit(
            f"\nDone — {ok} converted, {warned} with warning(s), {skipped} skipped, {err} error(s)."
        )
        self._running = False
        self._btn_state.emit(True)
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


class FixerSubTab(_ConversionSubTab):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        mode_row = QHBoxLayout()
        self._mode_single = QPushButton("Single file")
        self._mode_batch = QPushButton("Folder (batch)")
        self._mode_single.setProperty("active", True)
        self._mode_batch.setProperty("active", False)
        self._mode_single.clicked.connect(lambda: self._set_mode("single"))
        self._mode_batch.clicked.connect(lambda: self._set_mode("batch"))
        mode_row.addWidget(self._mode_single)
        mode_row.addWidget(self._mode_batch)
        layout.addLayout(mode_row)

        self._include_subfolders = QCheckBox("Include subfolders")
        self._include_subfolders.setChecked(True)
        self._include_subfolders.setToolTip(
            "Find DXF files recursively and preserve their folder structure in the output"
        )
        self._include_subfolders.setVisible(False)
        layout.addWidget(self._include_subfolders)

        self._repair_mode = QComboBox()
        self._repair_mode.addItem("Safe repair — preserve layers and native entities", "safe")
        self._repair_mode.addItem("Flatten for laser — layer 0 polylines", "flatten")
        self._repair_mode.setToolTip(
            "Safe repair changes only damaged polylines. Flatten for laser intentionally "
            "rebuilds supported geometry as normalized LWPOLYLINE entities."
        )
        layout.addWidget(self._repair_mode)

        self._src_edit = self._add_picker_row(
            layout,
            "INPUT",
            "Select a .dxf file or folder…",
            tooltip="Path to one DXF file or a folder containing DXF files",
            btn_tooltip="Browse for a DXF file or folder to repair",
            on_browse=self._browse_src,
        )
        self._out_edit = self._add_picker_row(
            layout,
            "OUTPUT",
            "Optional — defaults to a non-destructive fixed copy…",
            tooltip="Single: output file. Batch: output folder. Blank creates a sibling fixed copy.",
            btn_tooltip="Choose an output file or folder",
            on_browse=self._browse_out,
        )

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._btn = QPushButton("Fix DXF")
        self._btn.setMinimumHeight(38)
        self._btn.setToolTip(
            "Repair conservatively, or explicitly flatten supported geometry for laser workflows"
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

    def _set_mode(self, mode: str) -> None:
        batch = mode == "batch"
        for button, active in (
            (self._mode_single, not batch),
            (self._mode_batch, batch),
        ):
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self._include_subfolders.setVisible(batch)
        self._src_edit.clear()
        self._out_edit.clear()
        self._src_edit.setPlaceholderText(
            "Select a folder containing DXF files…" if batch else "Select a .dxf file…"
        )
        self._out_edit.setPlaceholderText(
            "Optional output folder (blank = sibling fixed folder)…"
            if batch
            else "Optional (blank = name-fixed.dxf)…"
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

    def _browse_out(self) -> None:
        if self._is_batch():
            path = QFileDialog.getExistingDirectory(self, "Select output folder", "")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save fixed DXF",
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
            self._status_sig.emit("Choose an input DXF file first.", STATUS_WARN)
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
        out = self._out_edit.text().strip()
        if not out:
            out = (
                str(source_path.with_name(f"{source_path.name}-fixed"))
                if self._is_batch()
                else str(source_path.with_name(f"{source_path.stem}-fixed{source_path.suffix}"))
            )
        elif self._is_batch():
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
                source_resolved = source_path.resolve()
                output_resolved = Path(out).resolve()
                if (
                    output_resolved != source_resolved
                    and source_resolved in output_resolved.parents
                ):
                    QMessageBox.warning(
                        self,
                        "Unsafe Output Folder",
                        "Choose an output folder outside the source tree to prevent generated "
                        "DXFs from being discovered as batch inputs.",
                    )
                    return
            except OSError as exc:
                QMessageBox.warning(self, "Output Folder Error", str(exc))
                return
        cancel_event = self._start_job()
        self._btn.setEnabled(False)
        self._set_status("Fixing…")
        if self._is_batch():
            self._thread = threading.Thread(
                target=self._fix_batch,
                args=(
                    src,
                    out,
                    cancel_event,
                    self._include_subfolders.isChecked(),
                    str(self._repair_mode.currentData()),
                ),
                daemon=True,
            )
        else:
            self._thread = threading.Thread(
                target=self._fix,
                args=(src, out, cancel_event, str(self._repair_mode.currentData())),
                daemon=True,
            )
        self._thread.start()

    @staticmethod
    def _folder_dxf_files(folder: str, *, recursive: bool = True) -> list[Path]:
        root = Path(folder)
        iterator = root.rglob("*") if recursive else root.iterdir()
        return sorted(
            (path for path in iterator if path.is_file() and path.suffix.lower() == ".dxf"),
            key=lambda path: str(path.relative_to(root)).casefold(),
        )

    def _fix_batch(
        self,
        src: str,
        out_dir: str,
        cancel_event: threading.Event,
        include_subfolders: bool = True,
        repair_mode: str = "safe",
    ) -> None:
        files = self._folder_dxf_files(src, recursive=include_subfolders)
        if not files:
            self._running = False
            self._btn_state.emit(True)
            self._status_sig.emit("No DXF files found", STATUS_WARN)
            self.log_line.emit("No DXF files found in the selected folder.")
            return
        succeeded = failed = changed_files = unchanged_files = 0
        totals = {
            "polylines_in": 0,
            "polylines_out": 0,
            "closed": 0,
            "simplified": 0,
            "discarded": 0,
            "flattened_entities": 0,
            "ignored_entities": 0,
            "protected_polylines": 0,
        }
        failed_files: list[str] = []
        output_root = Path(out_dir) if out_dir else None
        for index, source in enumerate(files, start=1):
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            relative = source.relative_to(Path(src))
            destination = output_root / relative if output_root else source
            self._status_sig.emit(f"Fixing {index} of {len(files)} — {source.name}", STATUS_NEUTRAL)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.resolve() == source.resolve():
                    shutil.copy2(source, source.with_suffix(source.suffix + ".bak"))
                stats = fix_dxf(source, destination, mode=repair_mode)  # type: ignore[arg-type]
                succeeded += 1
                for key in totals:
                    totals[key] += int(stats.get(key, 0) or 0)
                relative_name = str(relative)
                if bool(stats.get("changed")):
                    changed_files += 1
                    self.log_line.emit(
                        _append_ignored_entities_note(
                            f"{relative_name}: {stats['polylines_in']} in → "
                            f"{stats['polylines_out']} out"
                            f" · closed {stats['closed']} · simplified {stats['simplified']}"
                            f" · discarded {stats['discarded']}",
                            stats,
                        )
                    )
                else:
                    unchanged_files += 1
            except Exception as exc:
                # A corrupt file must not abort the rest of a user-selected batch.
                LOGGER.exception("Could not repair %s", source)
                failed += 1
                failed_files.append(str(relative))
                self.log_line.emit(f"{relative}: Error — {exc}")
        self._running = False
        self._btn_state.emit(True)
        self._reveal_state.emit(True)
        self._last_out = str(output_root or Path(src))
        tone = STATUS_OK if failed == 0 else STATUS_WARN
        changed = totals["closed"] + totals["simplified"] + totals["discarded"]
        status = (
            f"Done — {changed_files} with repairs · {unchanged_files} already clean · "
            f"{failed} failed · {changed} geometry repairs"
        )
        self._status_sig.emit(status, tone)
        summary_lines = [
            "",
            "BATCH SUMMARY",
            f"Files scanned: {len(files)}",
            f"Files written: {succeeded}",
            f"Files with geometry repairs: {changed_files}",
            f"Files already clean: {unchanged_files}",
            f"Files failed: {failed}",
            f"Polylines: {totals['polylines_in']} input → {totals['polylines_out']} output",
            f"Near-open paths closed: {totals['closed']}",
            f"Paths simplified: {totals['simplified']}",
            f"Degenerate paths discarded: {totals['discarded']}",
            f"Native entities flattened: {totals['flattened_entities']}",
            f"Unsupported entities ignored: {totals['ignored_entities']}",
            f"Curved/attributed polylines preserved: {totals['protected_polylines']}",
        ]
        if failed_files:
            summary_lines.append("Failed files: " + ", ".join(failed_files))
        summary_lines.append(f"Output: {self._last_out}")
        self.log_line.emit("\n".join(summary_lines))

    def _fix(
        self,
        src: str,
        out: str,
        cancel_event: threading.Event,
        repair_mode: str = "safe",
    ) -> None:
        try:
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            if Path(src).resolve() == Path(out).resolve():
                source = Path(src)
                shutil.copy2(source, source.with_suffix(source.suffix + ".bak"))
            stats = fix_dxf(src, out, mode=repair_mode)  # type: ignore[arg-type]
            msg = (
                f"Done — {stats['polylines_in']} in → {stats['polylines_out']} out"
                f"  · closed {stats['closed']}"
                f"  · simplified {stats['simplified']}"
                f"  · discarded {stats['discarded']}"
                f"  · protected {stats.get('protected_polylines', 0)}"
            )
            msg = _append_ignored_entities_note(msg, stats)
            self.log_line.emit(msg)
            self._running = False
            self._btn_state.emit(True)
            self._reveal_state.emit(True)
            self._status_sig.emit(msg, STATUS_OK)
            self._last_out = out
            self.preview_path.emit(out)
        except Exception as exc:
            LOGGER.exception("Could not repair DXF %s", src)
            self.log_line.emit(f"Error: {exc}")
            self._running = False
            self._btn_state.emit(True)
            self._status_sig.emit(f"Error: {exc}", STATUS_ERR)

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
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p if p.is_dir() else p.parent)))


class SvgSubTab(_ConversionSubTab):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        mode_row = QHBoxLayout()
        self._mode_single = QPushButton("Single file")
        self._mode_single.setToolTip("Convert one DXF file to SVG at a time")
        self._mode_batch = QPushButton("Folder (batch)")
        self._mode_batch.setToolTip("Convert all DXF files in a folder to SVG at once")
        self._mode_single.setProperty("active", True)
        self._mode_batch.setProperty("active", False)
        self._mode_single.clicked.connect(lambda: self._set_mode("single"))
        self._mode_batch.clicked.connect(lambda: self._set_mode("batch"))
        mode_row.addWidget(self._mode_single)
        mode_row.addWidget(self._mode_batch)
        layout.addLayout(mode_row)

        self._include_subfolders = QCheckBox("Include subfolders")
        self._include_subfolders.setChecked(True)
        self._include_subfolders.setToolTip(
            "Find DXF files recursively and preserve their folder structure in the output"
        )
        self._include_subfolders.setVisible(False)
        layout.addWidget(self._include_subfolders)

        self._src_edit = self._add_picker_row(
            layout,
            "INPUT",
            "Select a .dxf file…",
            tooltip="Path to the DXF file to convert to SVG",
            btn_tooltip="Browse for a DXF file to convert",
            on_browse=self._browse_src,
        )
        self._out_edit = self._add_picker_row(
            layout,
            "OUTPUT",
            "Leave blank to auto-name…",
            tooltip="Destination SVG path (blank = same name as input with .svg extension)",
            btn_tooltip="Choose where to save the SVG file",
            on_browse=self._browse_out,
        )

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

    def _set_mode(self, mode: str) -> None:
        batch = mode == "batch"
        for button, active in (
            (self._mode_single, not batch),
            (self._mode_batch, batch),
        ):
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self._include_subfolders.setVisible(batch)
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
        cancel_event = self._start_job()
        self._btn.setEnabled(False)
        self._set_status("Converting…")
        if self._is_batch():
            out_dir = self._out_edit.text().strip()
            if not out_dir:
                QMessageBox.warning(
                    self,
                    "Output Folder Required",
                    "Please select an output folder for batch conversion.",
                )
                self._btn.setEnabled(True)
                return
            self._thread = threading.Thread(
                target=self._convert_batch,
                args=(src, out_dir, cancel_event, self._include_subfolders.isChecked()),
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
                self._btn.setEnabled(True)
                return
            self._thread = threading.Thread(
                target=self._convert, args=(src, out, cancel_event), daemon=True
            )
        self._thread.start()

    def _convert(self, src: str, out: str, cancel_event: threading.Event) -> None:
        try:
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            stats = dxf_to_svg(src, out)
            msg = (
                f"Done — {stats['polylines']} polyline(s)"
                f"  · {stats['width_mm']:.1f} × {stats['height_mm']:.1f} mm"
            )
            msg = _append_ignored_entities_note(msg, stats)
            self.log_line.emit(msg)
            self._running = False
            self._btn_state.emit(True)
            self._reveal_state.emit(True)
            self._status_sig.emit("Done", STATUS_OK)
            self._last_out = out
            self.preview_path.emit(src)
        except Exception as exc:
            LOGGER.exception("Could not convert DXF to SVG: %s", src)
            self.log_line.emit(f"Error: {exc}")
            self._running = False
            self._btn_state.emit(True)
            self._status_sig.emit(f"Error: {exc}", STATUS_ERR)

    def _convert_batch(
        self,
        src: str,
        out_dir: str,
        cancel_event: threading.Event,
        include_subfolders: bool = True,
    ) -> None:
        root = Path(src)
        iterator = root.rglob("*") if include_subfolders else root.iterdir()
        files = sorted(
            (path for path in iterator if path.is_file() and path.suffix.lower() == ".dxf"),
            key=lambda path: str(path.relative_to(root)).casefold(),
        )
        if not files:
            self._running = False
            self._btn_state.emit(True)
            self._status_sig.emit("No DXF files found", STATUS_WARN)
            self.log_line.emit("No DXF files found in the selected folder.")
            return
        self.log_line.emit(f"Found {len(files)} file(s)\n")
        ok = err = 0
        for index, dxf in enumerate(files, start=1):
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            relative = dxf.relative_to(root)
            svg = Path(out_dir) / relative.with_suffix(".svg")
            self._status_sig.emit(
                f"Converting {index} of {len(files)} — {dxf.name}", STATUS_NEUTRAL
            )
            try:
                svg.parent.mkdir(parents=True, exist_ok=True)
                stats = dxf_to_svg(dxf, svg)
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
        self._running = False
        self._btn_state.emit(True)
        self._last_out = out_dir
        self._status_sig.emit(
            f"Done — {ok} converted" + (f", {err} error(s)" if err else ""),
            STATUS_WARN if err else STATUS_OK,
        )
        self.log_line.emit(f"\nDone — {ok} converted" + (f", {err} error(s)" if err else ""))

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


class SvgToDxfSubTab(_ConversionSubTab):
    log_line = Signal(str)
    preview_path = Signal(str)
    _btn_state = Signal(bool)
    _reveal_state = Signal(bool)
    _status_sig = Signal(str, str)

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}
        self._last_out: str | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self._build(root)

    def _build(self, layout: QVBoxLayout) -> None:
        mode_row = QHBoxLayout()
        self._mode_single = QPushButton("Single file")
        self._mode_single.setToolTip("Convert one SVG file to DXF at a time")
        self._mode_batch = QPushButton("Folder (batch)")
        self._mode_batch.setToolTip("Convert all SVG files in a folder to DXF at once")
        self._mode_single.setProperty("active", True)
        self._mode_batch.setProperty("active", False)
        self._mode_single.clicked.connect(lambda: self._set_mode("single"))
        self._mode_batch.clicked.connect(lambda: self._set_mode("batch"))
        mode_row.addWidget(self._mode_single)
        mode_row.addWidget(self._mode_batch)
        layout.addLayout(mode_row)

        self._include_subfolders = QCheckBox("Include subfolders")
        self._include_subfolders.setChecked(True)
        self._include_subfolders.setToolTip(
            "Find SVG files recursively and preserve their folder structure in the output"
        )
        self._include_subfolders.setVisible(False)
        layout.addWidget(self._include_subfolders)

        self._src_edit = self._add_picker_row(
            layout,
            "INPUT",
            "Select a .svg file…",
            tooltip="Path to the SVG file to convert",
            on_browse=self._browse_src,
        )
        self._out_edit = self._add_picker_row(
            layout,
            "OUTPUT",
            "Leave blank to auto-name…",
            tooltip="Destination DXF path (blank = same name as input with .dxf)",
            on_browse=self._browse_out,
        )

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

    def _set_mode(self, mode: str) -> None:
        batch = mode == "batch"
        for button, active in (
            (self._mode_single, not batch),
            (self._mode_batch, batch),
        ):
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)
        self._include_subfolders.setVisible(batch)
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
        cancel_event = self._start_job()
        self._btn.setEnabled(False)
        self._set_status("Converting…")
        if self._is_batch():
            out_dir = self._out_edit.text().strip()
            if not out_dir:
                QMessageBox.warning(
                    self,
                    "Output Folder Required",
                    "Please select an output folder for batch conversion.",
                )
                self._btn.setEnabled(True)
                return
            self._thread = threading.Thread(
                target=self._convert_batch,
                args=(src, out_dir, cancel_event, self._include_subfolders.isChecked()),
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
                self._btn.setEnabled(True)
                return
            self._thread = threading.Thread(
                target=self._convert, args=(src, out, cancel_event), daemon=True
            )
        self._thread.start()

    def _convert(self, src: str, out: str, cancel_event: threading.Event) -> None:
        try:
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            stats = svg_to_dxf(src, out)
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
            self._btn_state.emit(True)
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
            self._btn_state.emit(True)
            self._status_sig.emit(f"Error: {exc}", STATUS_ERR)

    def _convert_batch(
        self,
        src: str,
        out_dir: str,
        cancel_event: threading.Event,
        include_subfolders: bool = True,
    ) -> None:
        root = Path(src)
        iterator = root.rglob("*") if include_subfolders else root.iterdir()
        files = sorted(
            (path for path in iterator if path.is_file() and path.suffix.lower() == ".svg"),
            key=lambda path: str(path.relative_to(root)).casefold(),
        )
        if not files:
            self._running = False
            self._btn_state.emit(True)
            self._status_sig.emit("No SVG files found", STATUS_WARN)
            self.log_line.emit("No SVG files found in the selected folder.")
            return
        self.log_line.emit(f"Found {len(files)} file(s)\n")
        ok = err = 0
        for index, svg in enumerate(files, start=1):
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            relative = svg.relative_to(root)
            dxf = Path(out_dir) / relative.with_suffix(".dxf")
            self._status_sig.emit(
                f"Converting {index} of {len(files)} — {svg.name}", STATUS_NEUTRAL
            )
            try:
                dxf.parent.mkdir(parents=True, exist_ok=True)
                stats = svg_to_dxf(svg, dxf)
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
        self._running = False
        self._btn_state.emit(True)
        self._last_out = out_dir
        self._status_sig.emit(
            f"Done — {ok} converted" + (f", {err} error(s)" if err else ""),
            STATUS_WARN if err else STATUS_OK,
        )
        self.log_line.emit(f"\nDone — {ok} converted" + (f", {err} error(s)" if err else ""))

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


# ════════════════════════════════════════════════════════════════════════════
# Page shell
# ════════════════════════════════════════════════════════════════════════════


class ConvertPage(BasePage):
    """Convert page — conversion and repair helpers for vector workflows."""

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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        # ── Left sidebar content ──────────────────────────────────────────────
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(10, 10, 10, 4)
        left.setSpacing(4)

        # Vertical tool selector
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
        for i, (lbl, tip) in enumerate(zip(_tool_labels, _tool_tips)):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setProperty("active", i == 0)
            btn.setProperty("role", "tool-item")
            btn.setMinimumHeight(34)
            btn.setToolTip(tip)
            self._tool_group.addButton(btn, i)
            left.addWidget(btn)

        left.addSpacing(4)

        self._subtab_desc = QLabel(self._TOOL_DESCS[0])
        self._subtab_desc.setProperty("role", "hint")
        self._subtab_desc.setWordWrap(True)
        left.addWidget(self._subtab_desc)

        left.addSpacing(2)

        self._tool_stack = QStackedWidget()
        self._fvi_subtab = FviSubTab(settings=self._settings)
        self._fix_subtab = FixerSubTab(settings=self._settings)
        self._svg_subtab = SvgSubTab(settings=self._settings)
        self._svg_dxf_subtab = SvgToDxfSubTab(settings=self._settings)
        self._tool_stack.addWidget(self._fvi_subtab)
        self._tool_stack.addWidget(self._fix_subtab)
        self._tool_stack.addWidget(self._svg_subtab)
        self._tool_stack.addWidget(self._svg_dxf_subtab)
        left.addWidget(self._tool_stack, stretch=1)

        # ── Manual sidebar: scroll area + sticky footer ───────────────────────
        sidebar_frame = surface_frame("sidebar")
        sidebar_frame.setMinimumWidth(360)
        sidebar_frame.setMaximumWidth(440)
        sidebar_outer = QVBoxLayout(sidebar_frame)
        sidebar_outer.setContentsMargins(0, 0, 0, 0)
        sidebar_outer.setSpacing(0)

        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setFrameShape(QFrame.Shape.NoFrame)
        _scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        footer_layout.setContentsMargins(10, 8, 10, 10)
        footer_layout.setSpacing(6)

        self._footer_btn = QPushButton(self._BTN_LABELS[0])
        self._footer_btn.setMinimumHeight(38)
        self._footer_btn.setProperty("role", "primary")
        self._footer_btn.clicked.connect(self._trigger_active_subtab)

        self._footer_overflow = QToolButton()
        self._footer_overflow.setText("⋯")
        self._footer_overflow.setProperty("role", "overflow")
        self._footer_overflow.setFixedWidth(32)
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

        self._footer_widget = footer_w
        self._left_panel = sidebar_frame

        # ── Right panel: empty state → canvas preview ─────────────────────────
        right_w = surface_frame("canvas")
        right_w.setObjectName("conversion-preview")
        right_w.setProperty("role", "preview-surface")
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 8, 8, 8)
        right.setSpacing(6)

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
        _ev_hint = QLabel("Load a file and run a conversion\nto see the preview here.")
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
        _ev_choose.clicked.connect(lambda: self._tool_stack.currentWidget()._browse_src())
        _ev.addSpacing(8)
        _ev.addWidget(_ev_choose, alignment=Qt.AlignmentFlag.AlignHCenter)
        _ev.addStretch()
        self._right_stack.addWidget(_empty_w)

        # Page 1 — canvas + log
        _canvas_w = QWidget()
        _cl = QVBoxLayout(_canvas_w)
        _cl.setContentsMargins(0, 0, 0, 0)
        _cl.setSpacing(6)

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

        # ── Splitter ──────────────────────────────────────────────────────────
        self._splitter = content_splitter(self._left_panel, right_w, sizes=(380, 860))
        self._splitter.setCollapsible(0, False)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        root.addWidget(self._splitter)

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

        self._tool_group.idClicked.connect(self._on_tool_changed)
        self._on_tool_changed(0)
        self._refresh_preview_ui()

    def _on_tool_changed(self, idx: int) -> None:
        self._tool_stack.setCurrentIndex(idx)
        self._subtab_desc.setText(self._TOOL_DESCS[idx])
        for btn in self._tool_group.buttons():
            active = self._tool_group.id(btn) == idx
            btn.setProperty("active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._footer_btn.setText(self._BTN_LABELS[idx])

        _all = (
            self._fvi_subtab,
            self._fix_subtab,
            self._svg_subtab,
            self._svg_dxf_subtab,
        )
        if hasattr(self, "_active_tab_idx"):
            prev = _all[self._active_tab_idx]
            prev._btn_state.disconnect(self._footer_btn.setEnabled)
            prev._status_sig.disconnect(self._set_footer_status)
        self._active_tab_idx = idx
        subtab = _all[idx]
        subtab._btn_state.connect(self._footer_btn.setEnabled)
        subtab._status_sig.connect(self._set_footer_status)
        # Reflect the INCOMING tab's own running state, not a blind "ready" —
        # its conversion may still be in flight from before the user
        # switched away and back (each subtab now guards its own
        # re-entrancy, but the footer CTA should match reality too).
        still_running = bool(getattr(subtab, "_running", False))
        self._footer_btn.setEnabled(not still_running)
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
                "Show in Finder",
                subtab._reveal,  # type: ignore[union-attr]
            )
            sec.setEnabled(bool(subtab._last_out))  # type: ignore[union-attr]
        self._footer_overflow_menu.addSeparator()
        cancel_action = self._footer_overflow_menu.addAction("Cancel active job", subtab.cancel)
        cancel_action.setEnabled(still_running)

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
        subtab.run()
        actions = self._footer_overflow_menu.actions()
        if actions:
            actions[-1].setEnabled(bool(getattr(subtab, "_running", False)))

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
        self._refresh_preview_ui()

    def clear_workspace_state(self) -> None:
        self.apply_workspace_state({})
        self._log.clear()
        self._right_stack.setCurrentIndex(0)
        self._footer_status.setVisible(False)

    def _load_preview(self, dxf_path: str) -> None:
        try:
            polys = load_dxf_polylines(dxf_path)
            if polys:
                self._right_stack.setCurrentIndex(1)
                self._preview_canvas.load(polys)
                self._refresh_preview_ui()
        except (OSError, ValueError) as exc:
            LOGGER.debug("Preview load failed for '%s': %s", dxf_path, exc)
