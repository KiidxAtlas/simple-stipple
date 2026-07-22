from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from src.backend.model.document import WORKSPACE_FILE_SUFFIX
from src.ui.widgets.dialogs.workspace_library import WorkspaceLibraryDialog


def test_workspace_library_lists_and_opens_saved_files(qapp, tmp_path):
    saved = tmp_path / f"drawing{WORKSPACE_FILE_SUFFIX}"
    saved.write_text("{}", encoding="utf-8")
    dialog = WorkspaceLibraryDialog(tmp_path)

    dialog._list.setCurrentRow(0)
    dialog._open_selected()

    assert dialog.selected_path == saved


def test_workspace_library_can_duplicate_and_delete(qapp, tmp_path, monkeypatch):
    saved = tmp_path / f"drawing{WORKSPACE_FILE_SUFFIX}"
    saved.write_text('{"version": 1}', encoding="utf-8")
    dialog = WorkspaceLibraryDialog(tmp_path)
    dialog._list.setCurrentRow(0)

    dialog._duplicate_selected()

    duplicate = tmp_path / f"drawing copy{WORKSPACE_FILE_SUFFIX}"
    assert duplicate.read_text(encoding="utf-8") == saved.read_text(encoding="utf-8")
    dialog.refresh(select=duplicate)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog._delete_selected()

    assert not duplicate.exists()
    assert saved.exists()


def test_workspace_library_can_rename_inline(qapp, tmp_path):
    saved = tmp_path / f"drawing{WORKSPACE_FILE_SUFFIX}"
    saved.write_text("{}", encoding="utf-8")
    dialog = WorkspaceLibraryDialog(tmp_path)
    dialog._list.setCurrentRow(0)
    dialog._rename_selected()
    assert not dialog._rename_edit.isHidden()
    dialog._rename_edit.setText("renamed")
    dialog._commit_rename()

    renamed = tmp_path / f"renamed{WORKSPACE_FILE_SUFFIX}"
    assert renamed.exists()
    assert dialog.renamed_paths == {saved: renamed}


def test_workspace_library_exposes_recent_and_recovery_sections(qapp, tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    recent = tmp_path / f"recent{WORKSPACE_FILE_SUFFIX}"
    recent.write_text("{}", encoding="utf-8")
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    recovery = recovery_dir / "window-1.workspace.json"
    recovery.write_text(
        '{"recovery":{"workspace_path":"drawing.stipple.workspace.json"},'
        '"document":{"schema_version":1}}',
        encoding="utf-8",
    )
    dialog = WorkspaceLibraryDialog(
        saves,
        recent_paths=[recent],
        recovery_dir=recovery_dir,
    )

    dialog._category.setCurrentIndex(dialog._category.findData("recent"))
    assert dialog._current_path() == recent

    dialog._category.setCurrentIndex(dialog._category.findData("recovery"))
    dialog._list.setCurrentRow(0)
    text = dialog._list.currentItem().text()
    assert "drawing.stipple.workspace.json" in text
    assert "Snapshot" in text
    dialog._open_selected()
    assert dialog.selected_path == recovery
    assert dialog.selected_source == "recovery"
    assert dialog.selected_document == {"schema_version": 1}


def test_workspace_library_deletes_multiple_recovery_snapshots(qapp, tmp_path, monkeypatch):
    saves = tmp_path / "saves"
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    snapshots = [
        recovery_dir / f"window-{index}.workspace.json" for index in range(3)
    ]
    for snapshot in snapshots:
        snapshot.write_text('{"document":{"schema_version":1}}', encoding="utf-8")
    dialog = WorkspaceLibraryDialog(saves, recovery_dir=recovery_dir, initial_source="recovery")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    dialog._list.item(0).setSelected(True)
    dialog._list.item(1).setSelected(True)

    dialog._delete_selected()

    assert len(list(recovery_dir.glob("*.workspace.json"))) == 1
    assert len(dialog.deleted_paths) == 2


def test_workspace_library_deletes_all_recovery_snapshots(qapp, tmp_path, monkeypatch):
    saves = tmp_path / "saves"
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir()
    for index in range(2):
        (recovery_dir / f"window-{index}.workspace.json").write_text(
            '{"document":{"schema_version":1}}', encoding="utf-8"
        )
    dialog = WorkspaceLibraryDialog(saves, recovery_dir=recovery_dir, initial_source="recovery")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    dialog._delete_all_recovery()

    assert not list(recovery_dir.glob("*.workspace.json"))
    assert len(dialog.deleted_paths) == 2
