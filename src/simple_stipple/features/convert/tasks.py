"""Conversion task forms and their background-operation behavior."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.core.formats.service import DxfService, FviNoGeometryError, fix_dxf
from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.inputs import browse_row
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.style import STATUS_ERR, STATUS_NEUTRAL, STATUS_OK, STATUS_WARN

__all__ = [
    "FixerSubTab",
    "FviSubTab",
    "SvgSubTab",
    "SvgToDxfSubTab",
    "_ConversionSubTab",
]

LOGGER = logging.getLogger(__name__)


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

    log_line = Signal(str)
    _btn_state = Signal(bool)
    _status_sig = Signal(str, str)
    _readiness_requested = Signal()
    _source_changed = Signal()
    _status: QLabel
    _thread: threading.Thread | None = None
    _mode_single: QPushButton
    _mode_batch: QPushButton
    _include_subfolders: QCheckBox
    _btn: QPushButton
    _last_out: str | None = None
    # Per-task source path. The sidebar has no INPUT field of its own — the
    # page's shared header is the single visible input — so the path lives
    # here as plain state, mutated only through _set_src_text.
    _src_text: str = ""

    def _browse_src(self) -> None:
        raise NotImplementedError

    def _run(self) -> None:
        raise NotImplementedError

    def _bind_readiness(self) -> None:
        """Keep the Convert button disabled until a source path is entered.

        Without this the primary action sits fully enabled on an empty form,
        and clicking it does nothing but flash a status message — a dead end
        instead of a button that visibly isn't ready yet.
        """
        if not getattr(self, "_readiness_signal_bound", False):
            self._readiness_requested.connect(
                self._refresh_readiness_on_gui,
                Qt.ConnectionType.QueuedConnection,
            )
            self._readiness_signal_bound = True
        self._refresh_readiness()

    def _set_src_text(self, text: str) -> None:
        """Update the source path, then re-evaluate readiness and notify the
        page so its shared header input and dirty tracking stay in step."""
        self._src_text = text
        self._refresh_readiness()
        self._source_changed.emit()

    def is_ready(self) -> bool:
        if getattr(self, "_running", False):
            return False
        return bool(self._src_text.strip())

    @Slot()
    def _refresh_readiness(self) -> None:
        # Worker threads call this on job completion; the slots downstream of
        # ``_btn_state`` touch GUI widgets. Marshal back to the GUI thread when
        # invoked from a worker instead of running those slots off-thread.
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            # PySide6 6.9 no longer accepts this string-based invokeMethod
            # form reliably, even when the target is decorated as a Slot.
            # A queued Qt signal is the supported cross-thread handoff.
            self._readiness_requested.emit()
            return
        self._refresh_readiness_on_gui()

    @Slot()
    def _refresh_readiness_on_gui(self) -> None:
        self._btn_state.emit(self.is_ready())

    def _start_job(self) -> threading.Event:
        self._shutting_down = False
        self.blockSignals(False)
        self._cancel_event = threading.Event()
        self._running = True
        self._job_started_at = time.monotonic()
        self._job_completed = 0
        self._job_total = 0
        return self._cancel_event

    def _begin_batch(self, total: int) -> None:
        self._job_total = total
        self._job_completed = 0

    def _elapsed_text(self) -> str:
        started_at = getattr(self, "_job_started_at", None)
        if started_at is None:
            started_at = time.monotonic()
        elapsed = max(0, int(time.monotonic() - started_at))
        minutes, seconds = divmod(elapsed, 60)
        return f"{minutes}:{seconds:02d}"

    def _report_batch_progress(self, index: int, phase: str, filename: str) -> None:
        self._status_sig.emit(
            f"{phase} {index}/{self._job_total} — {filename} · elapsed {self._elapsed_text()}",
            STATUS_NEUTRAL,
        )

    def _record_batch_item(self, completed: int) -> None:
        self._job_completed = completed

    def cancel(self) -> None:
        event = getattr(self, "_cancel_event", None)
        if event is not None:
            event.set()
        if getattr(self, "_running", False):
            self._status_sig.emit("Cancelling…", STATUS_WARN)

    def _finish_cancelled(self) -> None:
        self._running = False
        self._refresh_readiness()
        completed = getattr(self, "_job_completed", 0)
        total = getattr(self, "_job_total", 0)
        detail = f" — {completed}/{total} completed output(s) retained" if total else ""
        message = f"Cancelled{detail} · elapsed {self._elapsed_text()}"
        self._status_sig.emit(message, STATUS_WARN)
        self.log_line.emit(message + ".")

    def shutdown(self) -> None:
        """Called by ``ConvertPage.shutdown()`` (in turn called by
        ``App.closeEvent``) before the window tears down. These conversions
        have no cooperative-cancellation support, so this is a best-effort
        wait for an in-flight one to finish rather than abandoning it
        outright — the thread is daemon=True either way, so a timeout here
        never blocks the app from actually closing."""
        self._shutting_down = True
        self.cancel()
        self.blockSignals(True)
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _set_status(self, text: str, color: str = STATUS_NEUTRAL) -> None:
        set_status_label(self._status, text, color)

    def _confirm_replace(self, paths: list[Path]) -> bool:
        existing = [path for path in paths if path.exists()]
        if not existing:
            return True
        answer = QMessageBox.question(
            self,
            "Replace Existing Files?",
            f"{len(existing)} destination file(s) already exist and will be replaced.\n\n"
            + "\n".join(path.name for path in existing[:5])
            + ("\n…" if len(existing) > 5 else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _set_mode(self, mode: str) -> None:
        batch = mode == "batch"
        for b, active in [
            (self._mode_single, not batch),
            (self._mode_batch, batch),
        ]:
            b.setProperty("active", active)
            refresh_style(b)
        self._include_subfolders.setVisible(batch)
        self._on_mode_switch(mode)

    def _on_mode_switch(self, mode: str) -> None:
        """Override in subclasses to handle mode-specific UI changes."""

    def _build_output_row(
        self,
        layout: QVBoxLayout,
        *,
        heading: str,
        placeholder: str,
        btn_tooltip: str,
        on_browse,
        tooltip: str = "",
    ) -> QLineEdit:
        """Add the OUTPUT picker row; return its edit.

        There is no sidebar INPUT row: ConvertPage's shared header is the
        single visible input field; the per-task source path is held in
        ``_src_text`` instead.
        """
        return browse_row(
            layout,
            heading=heading,
            placeholder=placeholder,
            tooltip=tooltip,
            btn_tooltip=btn_tooltip,
            on_browse=on_browse,
        )

    def _build_mode_row(self, layout: QVBoxLayout, single_text: str, batch_text: str) -> None:
        """Add a single-file / batch mode toggle row."""
        mode_row = QHBoxLayout()
        self._mode_single = QPushButton(single_text)
        self._mode_single.setToolTip(f"Convert one {single_text.lower().split()[0]} file at a time")
        self._mode_batch = QPushButton(batch_text)
        self._mode_batch.setToolTip(
            f"Convert all {batch_text.lower().split()[0]} in a folder at once"
        )
        self._mode_single.setProperty("active", True)
        self._mode_batch.setProperty("active", False)
        self._mode_single.clicked.connect(lambda: self._set_mode("single"))
        self._mode_batch.clicked.connect(lambda: self._set_mode("batch"))
        mode_row.addWidget(self._mode_single)
        mode_row.addWidget(self._mode_batch)
        layout.addLayout(mode_row)

    def _build_subfolders_checkbox(self, layout: QVBoxLayout, tooltip: str) -> None:
        """Add the (initially hidden) subfolders recursion checkbox."""
        self._include_subfolders = QCheckBox("Include subfolders")
        self._include_subfolders.setChecked(True)
        self._include_subfolders.setToolTip(tooltip)
        self._include_subfolders.setVisible(False)
        layout.addWidget(self._include_subfolders)

    def _build_action_row(self, btn_text: str, tooltip: str = "") -> None:
        """Create the primary action button and status label.

        Signal wiring happens per-subtab because signal names differ.
        """
        self._btn = QPushButton(btn_text)
        if tooltip:
            self._btn.setToolTip(tooltip)
        self._btn.setProperty("role", "primary")
        self._btn.setMinimumHeight(38)
        self._btn.clicked.connect(self._run)

        # Status is presented in ConvertPage's sticky footer. Keep this
        # compatibility label parented to its sub-tab so status signals can
        # never turn it into an accidental top-level native window.
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setVisible(False)

    @staticmethod
    def _collect_files(src: str, ext: str, include_subfolders: bool = True) -> list[Path]:
        """Collect source files from *src*, filtered by *ext* (e.g. ``".fvi"``).

        Returns a single-item list when *src* points at a file, or a sorted
        list of directory contents otherwise.
        """
        p = Path(src)
        if p.is_file():
            return [p]
        iterator = p.rglob("*") if include_subfolders else p.iterdir()
        return sorted(
            (path for path in iterator if path.is_file() and path.suffix.casefold() == ext),
            key=lambda path: str(path.relative_to(p)).casefold(),
        )

    # ── _reveal / _last_out  (lifted into base class) ──────────────────

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
        self._out_edit = self._build_output_row(
            layout,
            heading="OUTPUT",
            placeholder="Optional (blank = same as source)…",
            btn_tooltip="Choose an output folder for converted files",
            on_browse=self._browse_out,
        )

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._build_action_row("Convert")

        self._btn_state.connect(self._btn.setEnabled)
        self._out_dir_sig.connect(self._set_output_dir)
        # Note: _status_sig intentionally NOT connected to avoid sidebar popups
        # that appear after conversions complete.
        self._bind_readiness()

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
            self._set_src_text(path)

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
        src = self._src_text.strip()
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


class FixerSubTab(_ConversionSubTab):
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

        self._repair_mode = QComboBox()
        self._repair_mode.addItem("Safe repair — preserve layers and native entities", "safe")
        self._repair_mode.addItem("Flatten for laser — layer 0 polylines", "flatten")
        self._repair_mode.setToolTip(
            "Safe repair changes only damaged polylines. Flatten for laser intentionally "
            "rebuilds supported geometry as normalized LWPOLYLINE entities."
        )
        layout.addWidget(self._repair_mode)

        self._out_edit = self._build_output_row(
            layout,
            heading="OUTPUT",
            placeholder="Optional — defaults to a non-destructive fixed copy…",
            btn_tooltip="Choose an output file or folder",
            on_browse=self._browse_out,
        )

        layout.addStretch()

        # CTA and status live in the page-level sticky footer (ConvertPage)
        self._build_action_row(
            "Fix DXF",
            "Repair conservatively, or explicitly flatten supported geometry for laser workflows",
        )

        self._btn_state.connect(self._btn.setEnabled)
        # Note: _status_sig intentionally NOT connected to avoid sidebar popups
        # that appear after repairs complete.
        self._bind_readiness()

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

    def _on_mode_switch(self, mode: str) -> None:
        batch = mode == "batch"
        self._set_src_text("")
        self._out_edit.clear()
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
            self._set_src_text(path)

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
        src = self._src_text.strip()
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
        if self._is_batch():
            files = self._collect_files(src, ".dxf", self._include_subfolders.isChecked())
            root = source_path
            destinations = [Path(out) / file.relative_to(root) for file in files]
        else:
            destinations = [Path(out)]
        if not self._confirm_replace(destinations):
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

    def _fix_batch(
        self,
        src: str,
        out_dir: str,
        cancel_event: threading.Event,
        include_subfolders: bool = True,
        repair_mode: str = "safe",
    ) -> None:
        files = self._collect_files(src, ".dxf", include_subfolders)
        if not files:
            self._running = False
            self._refresh_readiness()
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
        self._begin_batch(len(files))
        for index, source in enumerate(files, start=1):
            if cancel_event.is_set():
                self._finish_cancelled()
                return
            relative = source.relative_to(Path(src))
            destination = output_root / relative if output_root else source
            self._report_batch_progress(index, "Fixing", source.name)
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
            finally:
                self._record_batch_item(index)
        self._running = False
        self._refresh_readiness()
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
            self._refresh_readiness()
            self._reveal_state.emit(True)
            self._status_sig.emit(msg, STATUS_OK)
            self._last_out = out
            self.preview_path.emit(out)
        except Exception as exc:
            LOGGER.exception("Could not repair DXF %s", src)
            self.log_line.emit(f"Error: {exc}")
            self._running = False
            self._refresh_readiness()
            self._status_sig.emit(f"Error: {exc}", STATUS_ERR)


# ════════════════════════════════════════════════════════════════════════════
# Page shell
# ════════════════════════════════════════════════════════════════════════════


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
        self._out_edit = self._build_output_row(
            layout,
            heading="OUTPUT",
            placeholder="Leave blank to auto-name…",
            btn_tooltip="Choose where to save the SVG file",
            tooltip="Destination SVG path (blank = same name as input with .svg extension)",
            on_browse=self._browse_out,
        )

        layout.addStretch()

        self._build_action_row(
            "Convert to SVG", "Convert the DXF polylines to an SVG vector graphic"
        )

        self._btn_state.connect(self._btn.setEnabled)
        # Note: _status_sig intentionally NOT connected to avoid sidebar popups
        # that appear after conversions complete.
        self._bind_readiness()

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

    def _on_mode_switch(self, mode: str) -> None:
        batch = mode == "batch"
        self._set_src_text("")
        self._out_edit.clear()
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
            self._set_src_text(path)
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
        src = self._src_text.strip()
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
        self._out_edit = self._build_output_row(
            layout,
            heading="OUTPUT",
            placeholder="Leave blank to auto-name…",
            btn_tooltip="Choose where to save the DXF file",
            tooltip="Destination DXF path (blank = same name as input with .dxf)",
            on_browse=self._browse_out,
        )

        layout.addStretch()

        self._build_action_row("Convert to DXF")

        self._btn_state.connect(self._btn.setEnabled)
        # Note: _status_sig intentionally NOT connected to avoid sidebar popups
        # that appear after conversions complete.
        self._bind_readiness()

    def run(self) -> None:
        """Public entry point called by the page-level footer CTA."""
        self._run()

    def _on_mode_switch(self, mode: str) -> None:
        batch = mode == "batch"
        self._set_src_text("")
        self._out_edit.clear()
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
            self._set_src_text(path)
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
        src = self._src_text.strip()
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
