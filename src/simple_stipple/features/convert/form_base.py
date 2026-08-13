"""Conversion task forms and their background-operation behavior."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simple_stipple.ui.components.feedback import refresh_style
from simple_stipple.ui.components.inputs import browse_row
from simple_stipple.ui.components.workflow import set_status_label
from simple_stipple.ui.style import STATUS_NEUTRAL, STATUS_WARN

LOGGER = logging.getLogger(__name__)

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

    log_line = Signal(str)
    _btn_state = Signal(bool)
    _status_sig = Signal(str, str)
    _readiness_requested = Signal()
    _status: QLabel
    _thread: threading.Thread | None = None
    _mode_single: QPushButton
    _mode_batch: QPushButton
    _include_subfolders: QCheckBox
    _btn: QPushButton
    _last_out: str | None = None

    def _browse_src(self) -> None:
        raise NotImplementedError

    def _run(self) -> None:
        raise NotImplementedError

    def _bind_readiness(self, source_edit: QLineEdit) -> None:
        """Keep the Convert button disabled until a source path is entered.

        Without this the primary action sits fully enabled on an empty form,
        and clicking it does nothing but flash a status message — a dead end
        instead of a button that visibly isn't ready yet.
        """
        self._readiness_edit = source_edit
        if not getattr(self, "_readiness_signal_bound", False):
            self._readiness_requested.connect(
                self._refresh_readiness_on_gui,
                Qt.ConnectionType.QueuedConnection,
            )
            self._readiness_signal_bound = True
        source_edit.textChanged.connect(lambda _text: self._refresh_readiness())
        self._refresh_readiness()

    def is_ready(self) -> bool:
        if getattr(self, "_running", False):
            return False
        edit = getattr(self, "_readiness_edit", None)
        return bool(edit.text().strip()) if edit is not None else True

    @Slot()
    def _refresh_readiness(self) -> None:
        # Worker threads call this on job completion, but ``is_ready()`` reads
        # a QLineEdit — a GUI-thread-only object. Marshal back to the GUI
        # thread when invoked from a worker instead of touching the widget
        # off-thread.
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

    def _build_input_output_rows(
        self,
        layout: QVBoxLayout,
        *,
        src_heading: str,
        src_placeholder: str,
        src_btn_tooltip: str,
        out_heading: str,
        out_placeholder: str,
        out_btn_tooltip: str,
        on_browse_src,
        on_browse_out,
        src_tooltip: str = "",
        out_tooltip: str = "",
    ) -> tuple[QLineEdit, QLineEdit]:
        """Add INPUT + OUTPUT picker rows; return the two edits."""
        src_edit = self._add_picker_row(
            layout,
            src_heading,
            src_placeholder,
            tooltip=src_tooltip,
            btn_tooltip=src_btn_tooltip,
            on_browse=on_browse_src,
        )
        out_edit = self._add_picker_row(
            layout,
            out_heading,
            out_placeholder,
            tooltip=out_tooltip,
            btn_tooltip=out_btn_tooltip,
            on_browse=on_browse_out,
        )
        return src_edit, out_edit

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
