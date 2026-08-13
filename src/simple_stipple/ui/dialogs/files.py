"""File dialogs with persistence-backed directory memory."""

from __future__ import annotations

import platform as _platform
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog

from simple_stipple.platform.settings import save_settings
from simple_stipple.ui.components.recent import record_recent
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)
from simple_stipple.core.formats.service import DxfImportReport, summarize_dxf_import_report
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle
from simple_stipple.ui.components.layout import (
    section_label,
    surface_frame,
)
from simple_stipple.ui.style import SPACE_MD
from simple_stipple.ui.dialogs.base import BaseDialog

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


def _settings_key(slot: str) -> str:
    """Return the canonical settings key used to remember a dialog directory."""
    return f"dialog_dir.{slot}"


def reveal_label() -> str:
    """Platform-correct wording for "open this path in the OS file manager".

    Every call site uses ``QDesktopServices.openUrl`` (cross-platform), but
    several used to say "Show in Finder" unconditionally — correct on
    macOS, wrong on Windows/Linux.
    """
    system = _platform.system()
    if system == "Darwin":
        return "Show in Finder"
    if system == "Windows":
        return "Show in Explorer"
    return "Show in Files"


def remembered_dir(settings: dict, slot: str, *, fallback: str = "") -> str:
    """Return the last-remembered directory for *slot*, or *fallback*."""
    value = settings.get(_settings_key(slot))
    if isinstance(value, str) and value:
        return value
    if isinstance(fallback, str) and fallback:
        return fallback
    return ""


def remember_dir(settings: dict, slot: str, path: str) -> None:
    """Persist the parent directory of *path* under *slot*."""
    if not path:
        return
    parent = str(Path(path).expanduser().parent)
    if not parent:
        return
    if settings.get(_settings_key(slot)) == parent:
        return
    settings[_settings_key(slot)] = parent
    save_settings(settings)


def pick_open_file(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    file_filter: str,
    *,
    fallback_dir: str = "",
    recent_kind: str | None = None,
) -> str:
    """Show an *open file* dialog seeded with the remembered directory.

    When *recent_kind* is provided, a successful pick is also pushed onto the
    matching recent-files MRU (see the recent-files functions above).
    """
    start = remembered_dir(settings, slot, fallback=fallback_dir)
    path, _ = QFileDialog.getOpenFileName(parent, caption, start, file_filter)
    if path:
        remember_dir(settings, slot, path)
        if recent_kind:
            record_recent(settings, recent_kind, path)
    return path


def pick_save_file(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    default_name: str,
    file_filter: str,
    *,
    fallback_dir: str = "",
) -> str:
    """Show a *save file* dialog seeded with the remembered directory.

    *default_name* is appended to the directory to pre-fill the filename field.
    """
    start_dir = remembered_dir(settings, slot, fallback=fallback_dir)
    if start_dir:
        seed = str(Path(start_dir) / default_name)
    else:
        seed = default_name
    path, _ = QFileDialog.getSaveFileName(parent, caption, seed, file_filter)
    if path:
        remember_dir(settings, slot, path)
    return path


def pick_directory(
    parent: QWidget | None,
    settings: dict,
    slot: str,
    caption: str,
    *,
    fallback_dir: str = "",
) -> str:
    """Show a *choose folder* dialog seeded with the remembered directory."""
    start = remembered_dir(settings, slot, fallback=fallback_dir)
    path = QFileDialog.getExistingDirectory(parent, caption, start)
    if path:
        # Folder picks remember themselves, not the parent.
        if settings.get(_settings_key(slot)) != path:
            settings[_settings_key(slot)] = path
            save_settings(settings)
    return path


class DxfImportPreviewDialog(BaseDialog):
    """Preview import scale/content and choose layers plus replace/append."""

    def __init__(
        self,
        path: str,
        by_layer: dict[str, list[list[tuple[float, float]]]],
        report: DxfImportReport,
        *,
        has_existing_geometry: bool,
        default_append: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        self._path = path
        self._by_layer = by_layer
        self._report = report
        self._has_existing_geometry = has_existing_geometry
        self._default_append = default_append
        super().__init__(parent, title="Import DXF")
        self.setMinimumSize(640, 520)
        self.resize(720, 600)

    def create_content(self, layout: QVBoxLayout) -> None:
        root = layout
        root.setSpacing(SPACE_MD)

        section_label(root, Path(self._path).name)
        points = [point for polys in self._by_layer.values() for poly in polys for point in poly]
        if points:
            xs, ys = zip(*points)
            bounds_text = f"{max(xs) - min(xs):.4g} × {max(ys) - min(ys):.4g} drawing units"
        else:
            bounds_text = "No usable bounds"
        summary = QLabel(
            f"Units: {self._report.units}   ·   Size: {bounds_text}\n"
            f"{self._report.supported_polylines:,} paths across {len(self._by_layer):,} layer(s)"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        issue_text = summarize_dxf_import_report(self._report) if self._report.has_issues else None
        if issue_text:
            notice = QLabel(f"Import notes: {issue_text}")
            notice.setProperty("role", "status-warn")
            notice.setWordWrap(True)
            root.addWidget(notice)

        layers_frame = surface_frame("panel")
        layers_layout = QVBoxLayout(layers_frame)
        layers_layout.addWidget(QLabel("Layers to import"))
        self._layers = QListWidget()
        for name, polys in self._by_layer.items():
            item = QListWidgetItem(f"{name}  ({len(polys):,} paths)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._layers.addItem(item)
        layers_layout.addWidget(self._layers)
        root.addWidget(layers_frame)

        mode_row = QHBoxLayout()
        self._replace = QRadioButton("Replace drawing")
        self._append = QRadioButton("Add to drawing")
        self._append.setEnabled(self._has_existing_geometry)
        # "Add" only makes sense when there's an existing drawing to add to —
        # otherwise there is nothing to append and the radio is disabled, so
        # it must not also be checked.
        choose_append = self._has_existing_geometry and self._default_append
        self._append.setChecked(choose_append)
        self._replace.setChecked(not choose_append)
        mode_row.addWidget(self._replace)
        mode_row.addWidget(self._append)
        mode_row.addStretch(1)
        root.addLayout(mode_row)
        if self._has_existing_geometry:
            safety = QLabel(
                "Add preserves the current drawing. Replace removes existing objects; "
                "you can undo the replacement immediately after import."
            )
            safety.setProperty("role", "hint")
            safety.setWordWrap(True)
            root.addWidget(safety)

        install_dialog_focus_lifecycle(self, self._layers)

    def validate(self) -> str | None:
        if not self.selected_layers():
            return "Select at least one layer to import."
        return None

    def selected_layers(self) -> list[str]:
        return [
            str(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self._layers.count())
            if (item := self._layers.item(row)).checkState() == Qt.CheckState.Checked
        ]

    def append_mode(self) -> bool:
        return self._append.isChecked()


class VectorImportModeDialog(BaseDialog):
    """Keep non-DXF imports honest about their effect on the current drawing.

    DXF needs a layer picker, while FVI and SVG do not.  All three formats
    still need the same destructive-state decision before import, so this
    compact dialog deliberately shares the wording and control order of the
    DXF review dialog.
    """

    def __init__(
        self,
        path: str,
        *,
        format_name: str,
        has_existing_geometry: bool,
        parent: QWidget | None = None,
    ) -> None:
        self._path = path
        self._format_name = format_name
        self._has_existing_geometry = has_existing_geometry
        super().__init__(parent, title=f"Import {format_name}")
        self.setMinimumWidth(460)

    def create_content(self, layout: QVBoxLayout) -> None:
        section_label(layout, Path(self._path).name)
        summary = QLabel(f"Import this {self._format_name} file into the current Draft canvas.")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self._replace = QRadioButton("Replace drawing")
        self._append = QRadioButton("Add to drawing")
        self._append.setEnabled(self._has_existing_geometry)
        self._replace.setChecked(True)
        layout.addWidget(self._replace)
        layout.addWidget(self._append)

        if self._has_existing_geometry:
            safety = QLabel(
                "Add preserves existing objects. Replace removes them; use Undo immediately "
                "after import if needed."
            )
            safety.setProperty("role", "hint")
            safety.setWordWrap(True)
            layout.addWidget(safety)

        install_dialog_focus_lifecycle(self, self._replace)

    def append_mode(self) -> bool:
        return self._append.isChecked()
