"""User-visible browser for durable workspace saves."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from simple_stipple.document.workspace import (
    WORKSPACE_FILE_SUFFIX,
    normalize_workspace_path,
)
from simple_stipple.platform.storage import (
    MAX_WORKSPACE_FILE_BYTES,
    read_json_file,
    write_json_file_atomic,
)
from simple_stipple.ui.components.focus import install_dialog_focus_lifecycle


class WorkspaceLibraryDialog(QDialog):
    """Browse and manage files in the dedicated saves directory."""

    def __init__(
        self,
        saves_dir: Path,
        parent=None,
        *,
        recent_paths: list[Path] | None = None,
        recovery_dir: Path | None = None,
        initial_source: str = "saved",
    ) -> None:
        super().__init__(parent)
        self.saves_dir = saves_dir
        self.selected_path: Path | None = None
        self.selected_document: dict | None = None
        self.selected_source = "saved"
        self.recent_paths = list(recent_paths or [])
        self.recovery_dir = recovery_dir
        self.renamed_paths: dict[Path, Path] = {}
        self.deleted_paths: set[Path] = set()
        self.setWindowTitle("Workspaces")
        self.resize(680, 440)

        layout = QVBoxLayout(self)
        heading = QLabel(f"Workspaces\n{self.saves_dir}")
        heading.setToolTip(str(self.saves_dir))
        layout.addWidget(heading)

        self._category = QComboBox()
        self._category.addItem("Saved workspaces", "saved")
        self._category.addItem("Recent workspaces", "recent")
        self._category.addItem("Recovery snapshots", "recovery")
        self._category.currentIndexChanged.connect(self.refresh)
        initial_index = self._category.findData(initial_source)
        if initial_index >= 0:
            self._category.blockSignals(True)
            self._category.setCurrentIndex(initial_index)
            self._category.blockSignals(False)
        layout.addWidget(self._category)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self._list.itemSelectionChanged.connect(self._sync_buttons)
        layout.addWidget(self._list, 1)

        self._rename_row = QHBoxLayout()
        self._rename_edit = QLineEdit()
        self._rename_edit.setPlaceholderText("Workspace name")
        self._rename_edit.setAccessibleName("New workspace name")
        self._rename_edit.returnPressed.connect(self._commit_rename)
        self._rename_apply_btn = QPushButton("Apply rename")
        self._rename_apply_btn.clicked.connect(self._commit_rename)
        self._rename_cancel_btn = QPushButton("Cancel")
        self._rename_cancel_btn.clicked.connect(self._cancel_rename)
        self._rename_row.addWidget(QLabel("Rename workspace"))
        self._rename_row.addWidget(self._rename_edit, stretch=1)
        self._rename_row.addWidget(self._rename_cancel_btn)
        self._rename_row.addWidget(self._rename_apply_btn)
        for index in range(self._rename_row.count()):
            item = self._rename_row.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
        layout.addLayout(self._rename_row)

        buttons = QHBoxLayout()
        self._open_btn = QPushButton("Open")
        self._open_btn.setProperty("role", "primary")
        self._open_btn.clicked.connect(self._open_selected)
        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._rename_selected)
        self._duplicate_btn = QPushButton("Duplicate")
        self._duplicate_btn.clicked.connect(self._duplicate_selected)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setProperty("role", "danger")
        self._delete_btn.clicked.connect(self._delete_selected)
        self._delete_all_btn = QPushButton("Delete All Snapshots")
        self._delete_all_btn.setProperty("role", "danger")
        self._delete_all_btn.clicked.connect(self._delete_all_recovery)
        folder_btn = QPushButton("Show Folder")
        folder_btn.clicked.connect(self._show_folder)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        for button in (
            self._rename_btn,
            self._duplicate_btn,
            self._delete_btn,
            self._delete_all_btn,
            folder_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        buttons.addWidget(self._open_btn)
        layout.addLayout(buttons)

        self.refresh()
        install_dialog_focus_lifecycle(self, self._category)

    def refresh(self, select: Path | None = None) -> None:
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self._list.clear()
        source = str(self._category.currentData() or "saved")
        if source == "recent":
            paths = list(dict.fromkeys(path for path in self.recent_paths if path.exists()))
        elif source == "recovery":
            paths = (
                sorted(
                    self.recovery_dir.glob("*.workspace.json"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if self.recovery_dir is not None and self.recovery_dir.exists()
                else []
            )
        else:
            paths = sorted(
                self.saves_dir.glob(f"*{WORKSPACE_FILE_SUFFIX}"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        for path in paths:
            modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%b %d, %Y  %I:%M %p")
            size_bytes = path.stat().st_size
            size = (
                f"{size_bytes / (1024 * 1024):.1f} MB"
                if size_bytes >= 1024 * 1024
                else f"{max(1, size_bytes // 1024)} KB"
            )
            detail = f"Modified {modified} · {size} · {path.parent}"
            valid = True
            if source == "recovery":
                try:
                    raw = read_json_file(
                        path,
                        default={},
                        max_bytes=MAX_WORKSPACE_FILE_BYTES,
                    )
                    valid = isinstance(raw, dict) and isinstance(raw.get("document", raw), dict)
                    metadata = raw.get("recovery", {}) if isinstance(raw, dict) else {}
                    original = Path(str(metadata.get("workspace_path", ""))).name
                    timestamp = str(metadata.get("timestamp", ""))
                    try:
                        captured = datetime.fromisoformat(timestamp).astimezone()
                        captured_text = captured.strftime("%b %d, %Y  %I:%M:%S %p")
                    except (TypeError, ValueError):
                        captured_text = modified
                    snapshot_id = path.name.removesuffix(WORKSPACE_FILE_SUFFIX)[-8:]
                    detail = (
                        f"{original or 'Unsaved workspace'} · {captured_text} · {size} · "
                        f"Snapshot {snapshot_id}"
                    )
                except (OSError, TypeError, ValueError):
                    valid = False
                    detail = f"Invalid recovery snapshot · {modified} · {size}"
            title = path.name if valid else f"Invalid · {path.name}"
            item = QListWidgetItem(f"{title}\n{detail}")
            item.setData(0x0100, str(path))
            item.setData(0x0101, source)
            item.setData(0x0102, valid)
            item.setToolTip(str(path))
            self._list.addItem(item)
            if select is not None and path == select:
                self._list.setCurrentItem(item)
        if not paths:
            messages = {
                "saved": "No saved workspaces yet.\nUse File → Save Workspace As to create one.",
                "recent": "No recent workspaces are currently available.",
                "recovery": "No recovery snapshots are currently available.",
            }
            empty = QListWidgetItem(messages[source])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._list.addItem(empty)
        elif self._list.currentItem() is None:
            self._list.setCurrentRow(0)
        self._sync_buttons()

    def _current_path(self) -> Path | None:
        item = self._list.currentItem()
        value = item.data(0x0100) if item is not None else None
        return Path(str(value)) if value else None

    def _sync_buttons(self) -> None:
        enabled = self._current_path() is not None
        item = self._list.currentItem()
        source = str(item.data(0x0101)) if item is not None else ""
        valid = bool(item.data(0x0102)) if item is not None else False
        self._open_btn.setEnabled(enabled and valid)
        self._open_btn.setText("Recover" if source == "recovery" else "Open")
        self._rename_btn.setEnabled(enabled and source == "saved")
        self._duplicate_btn.setEnabled(enabled)
        self._duplicate_btn.setText("Save a Copy" if source != "saved" else "Duplicate")
        self._delete_btn.setEnabled(enabled and source in {"saved", "recovery"})
        self._delete_all_btn.setVisible(source == "recovery")
        self._delete_all_btn.setEnabled(source == "recovery" and bool(self._recovery_paths()))

    def _selected_paths(self) -> list[Path]:
        return [
            Path(str(value)) for item in self._list.selectedItems() if (value := item.data(0x0100))
        ]

    def _recovery_paths(self) -> list[Path]:
        if self.recovery_dir is None or not self.recovery_dir.exists():
            return []
        return list(self.recovery_dir.glob("*.workspace.json"))

    def _open_selected(self) -> None:
        path = self._current_path()
        if path is not None:
            item = self._list.currentItem()
            self.selected_source = str(item.data(0x0101) or "saved") if item else "saved"
            if self.selected_source == "recovery":
                raw = read_json_file(
                    path,
                    default={},
                    max_bytes=MAX_WORKSPACE_FILE_BYTES,
                )
                if not isinstance(raw, dict):
                    QMessageBox.warning(self, "Recovery Failed", "This snapshot is not valid.")
                    return
                document = raw.get("document", raw)
                if not isinstance(document, dict):
                    QMessageBox.warning(
                        self, "Recovery Failed", "This snapshot has no workspace data."
                    )
                    return
                self.selected_document = document
            self.selected_path = path
            self.accept()

    def _rename_selected(self) -> None:
        path = self._current_path()
        if path is None:
            return
        base_name = path.name.removesuffix(WORKSPACE_FILE_SUFFIX)
        self._rename_edit.setText(base_name)
        for index in range(self._rename_row.count()):
            item = self._rename_row.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.show()
        self._rename_edit.selectAll()
        self._rename_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _cancel_rename(self) -> None:
        for index in range(self._rename_row.count()):
            item = self._rename_row.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()

    def _commit_rename(self) -> None:
        path = self._current_path()
        if path is None:
            self._cancel_rename()
            return
        name = self._rename_edit.text().strip()
        if not name:
            self._rename_edit.setProperty("invalid", True)
            self._rename_edit.setToolTip("Workspace name cannot be empty")
            return
        self._rename_edit.setProperty("invalid", False)
        candidate = normalize_workspace_path(self.saves_dir / name)
        if candidate.exists() and candidate != path:
            self._rename_edit.setProperty("invalid", True)
            self._rename_edit.setToolTip("A workspace with that name already exists")
            return
        try:
            path.rename(candidate)
        except OSError as exc:
            QMessageBox.warning(self, "Rename Workspace", f"Could not rename workspace:\n{exc}")
            return
        original = next((old for old, new in self.renamed_paths.items() if new == path), path)
        self.renamed_paths[original] = candidate
        self._cancel_rename()
        self.refresh(candidate)

    def _duplicate_selected(self) -> None:
        path = self._current_path()
        if path is None:
            return
        item = self._list.currentItem()
        source = str(item.data(0x0101) or "saved") if item else "saved"
        base = path.name.removesuffix(WORKSPACE_FILE_SUFFIX)
        if source == "recovery":
            base = f"Recovered {datetime.now().strftime('%Y-%m-%d %H%M')}"
        index = 2
        candidate = self.saves_dir / f"{base} copy{WORKSPACE_FILE_SUFFIX}"
        while candidate.exists():
            candidate = self.saves_dir / f"{base} copy {index}{WORKSPACE_FILE_SUFFIX}"
            index += 1
        try:
            if source == "recovery":
                raw = read_json_file(
                    path,
                    default={},
                    max_bytes=MAX_WORKSPACE_FILE_BYTES,
                )
                document = raw.get("document", raw) if isinstance(raw, dict) else None
                if not isinstance(document, dict):
                    raise ValueError("Recovery snapshot has no workspace document")
                write_json_file_atomic(candidate, document)
            else:
                shutil.copy2(path, candidate)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                self, "Duplicate Workspace", f"Could not duplicate workspace:\n{exc}"
            )
            return
        if source != "saved":
            self._category.setCurrentIndex(self._category.findData("saved"))
        self.refresh(candidate)

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        noun = "workspace" if len(paths) == 1 else "workspaces"
        names = paths[0].name if len(paths) == 1 else f"{len(paths)} selected {noun}"
        answer = QMessageBox.question(
            self,
            "Delete Workspace" if len(paths) == 1 else "Delete Workspaces",
            f"Permanently delete {names}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failures: list[str] = []
        for path in paths:
            try:
                path.unlink()
                self.deleted_paths.add(path)
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
        if failures:
            QMessageBox.warning(
                self,
                "Delete Incomplete",
                "Some files could not be deleted:\n" + "\n".join(failures),
            )
        self.refresh()

    def _delete_all_recovery(self) -> None:
        paths = self._recovery_paths()
        if not paths:
            return
        answer = QMessageBox.question(
            self,
            "Delete All Recovery Snapshots",
            f"Permanently delete all {len(paths)} recovery snapshots?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failures: list[str] = []
        for path in paths:
            try:
                path.unlink()
                self.deleted_paths.add(path)
            except OSError as exc:
                failures.append(f"{path.name}: {exc}")
        if failures:
            QMessageBox.warning(
                self,
                "Delete Incomplete",
                "Some snapshots could not be deleted:\n" + "\n".join(failures),
            )
        self.refresh()

    def _show_folder(self) -> None:
        path = self._current_path()
        folder = path.parent if path is not None else self.saves_dir
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


__all__ = ["WorkspaceLibraryDialog"]
