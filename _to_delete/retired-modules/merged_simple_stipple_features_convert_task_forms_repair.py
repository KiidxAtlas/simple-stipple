"""DXF repair task form."""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QMessageBox, QVBoxLayout, QWidget

from simple_stipple.engine.formats.service import fix_dxf
from simple_stipple.ui.style.theme import STATUS_ERR, STATUS_OK, STATUS_WARN

from .base import _append_ignored_entities_note, _ConversionSubTab

LOGGER = logging.getLogger(__name__)


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

        self._src_edit, self._out_edit = self._build_input_output_rows(
            layout,
            src_heading="INPUT",
            src_placeholder="Select a .dxf file or folder…",
            src_btn_tooltip="Browse for a DXF file or folder to repair",
            out_heading="OUTPUT",
            out_placeholder="Optional — defaults to a non-destructive fixed copy…",
            out_btn_tooltip="Choose an output file or folder",
            on_browse_src=self._browse_src,
            on_browse_out=self._browse_out,
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
