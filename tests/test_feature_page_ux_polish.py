"""UX polish on the Draft/Pattern/Trace/Convert/Repository pages."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent
from PySide6.QtWidgets import QMessageBox

import simple_stipple.features.repository.page as repo_module
from simple_stipple.features.convert.page import ConvertPage
from simple_stipple.features.draft.page import DraftPage
from simple_stipple.features.pattern.page import PatternPage
from simple_stipple.features.repository.page import RepoPage
from simple_stipple.features.trace.page import TracePage

OUTER = [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0), (0.0, 0.0)]
OTHER = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
TREATMENT = {"kind": "pattern", "pattern": "Honeycomb", "params": {}}


def _drag_enter(page, path: str) -> QDragEnterEvent:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path)])
    event = QDragEnterEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    event.setAccepted(False)
    page.dragEnterEvent(event)
    return event


# ── Drop rejection messages name StarFX support ───────────────────────────────


def test_draft_rejection_names_supported_starfx_and_accepts_fmb(qtbot, monkeypatch) -> None:
    page = DraftPage(settings={})
    qtbot.addWidget(page)
    flashes: list[str] = []
    monkeypatch.setattr(page._canvas, "_show_flash", lambda text, *_a: flashes.append(text))

    assert _drag_enter(page, "/tmp/job.FMB").isAccepted()
    assert not _drag_enter(page, "/tmp/notes.txt").isAccepted()
    assert "StarFX" in flashes[-1] and "FMB" in flashes[-1]


def test_pattern_rejects_starfx_job_but_points_to_draft(qtbot) -> None:
    page = PatternPage(settings={})
    qtbot.addWidget(page)

    assert not _drag_enter(page, "/tmp/job.fmb").isAccepted()
    assert "Draft" in page._status.text()
    assert _drag_enter(page, "/tmp/outline.dxf").isAccepted()

    assert not _drag_enter(page, "/tmp/notes.txt").isAccepted()
    assert "Draft" not in page._status.text()


def test_convert_rejection_explains_and_starfx_points_to_draft(qtbot) -> None:
    page = ConvertPage(settings={})
    qtbot.addWidget(page)

    assert not _drag_enter(page, "/tmp/job.Fmb").isAccepted()
    assert "Draft" in page._footer_status.text()

    assert not _drag_enter(page, "/tmp/notes.txt").isAccepted()
    assert "Draft" not in page._footer_status.text()
    assert "DXF, FVI or SVG" in page._footer_status.text()

    assert _drag_enter(page, "/tmp/outline.svg").isAccepted()


# ── Convert: the run button lives with the settings it acts on ────────────────


@pytest.mark.parametrize(("width", "compact"), [(1400, False), (900, True)])
def test_convert_run_footer_is_inside_the_settings_sidebar(qtbot, width, compact) -> None:
    page = ConvertPage(settings={})
    qtbot.addWidget(page)
    page.resize(width, 700)
    page.show()
    qtbot.waitExposed(page)
    if compact:
        page._splitter._set_drawer_open(True)
    qtbot.wait(50)

    panel = page._left_panel
    assert panel.isAncestorOf(page._footer_btn)
    assert panel.isAncestorOf(page._footer_status)
    assert not page._right_stack.isAncestorOf(page._footer_btn)
    assert panel.width() > 0
    # Not clipped: the run button sits fully inside the sidebar, above its bottom edge.
    button_rect = page._footer_btn.rect()
    top_left = page._footer_btn.mapTo(panel, button_rect.topLeft())
    bottom_right = page._footer_btn.mapTo(panel, button_rect.bottomRight())
    assert panel.rect().contains(top_left) and panel.rect().contains(bottom_right)
    assert page._footer_btn.isVisibleTo(page)


# ── Trace: typed invalid image path uses the shared field feedback ────────────


def test_trace_invalid_typed_path_marks_field_until_edited(qtbot, tmp_path: Path) -> None:
    page = TracePage(settings={})
    qtbot.addWidget(page)
    missing = str(tmp_path / "missing.png")

    page._img_edit.setText(missing)
    page._commit_typed_image_path()

    assert page._img_edit.property("error") is True
    assert "supported image" in page._img_edit.toolTip()
    assert page._img_edit.text() == missing  # typed text kept for correction

    qtbot.keyClicks(page._img_edit, "x")  # a real edit clears the mark
    assert not page._img_edit.property("error")


# ── Pattern: replacing an outline that carries treatments asks first ──────────


def _write_square_dxf(path: Path) -> None:
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    doc.saveas(path)


def test_pattern_reload_with_treatments_requires_confirmation(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    page = PatternPage(settings={})
    qtbot.addWidget(page)
    page.load_outline_polys([OUTER])
    page._treatments = {page._outline_ids[0]: dict(TREATMENT)}
    target = tmp_path / "square.dxf"
    _write_square_dxf(target)

    answers = [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes]
    asked: list[str] = []

    def fake_question(_parent, title, text, *_a):
        asked.append(text)
        return answers.pop(0)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))

    page._load_outline_file(str(target))
    assert len(asked) == 1 and "square.dxf" in asked[0]
    assert page._treatments  # declined: nothing was discarded
    assert page._edit_polys == [OUTER]

    page._load_outline_file(str(target))
    assert not page._treatments
    assert page._edit_polys != [OUTER]


def test_pattern_confirmed_drop_does_not_ask_twice(qtbot, monkeypatch, tmp_path: Path) -> None:
    page = PatternPage(settings={})
    qtbot.addWidget(page)
    page.load_outline_polys([OUTER])
    page._treatments = {page._outline_ids[0]: dict(TREATMENT)}
    target = tmp_path / "square.dxf"
    _write_square_dxf(target)
    asked: list[str] = []

    def fake_question(_parent, _title, text, *_a):
        asked.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    page._load_outline_file(str(target), confirmed=True)

    assert asked == []
    assert not page._treatments


def test_pattern_focus_out_of_unchanged_path_field_keeps_treatments(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    page = PatternPage(settings={})
    qtbot.addWidget(page)
    target = tmp_path / "square.dxf"
    _write_square_dxf(target)
    page._load_outline_file(str(target))
    page._treatments = {page._outline_ids[0]: dict(TREATMENT)}
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *_a: pytest.fail("focus loss must not reload")),
    )

    page._dxf_edit.editingFinished.emit()

    assert page._treatments


# ── Pattern: "Undo transfer" is withdrawn once later work would be lost ───────


def test_undo_transfer_survives_view_changes_but_not_content_edits(qtbot) -> None:
    page = PatternPage(settings={})
    qtbot.addWidget(page)
    page.show()
    page.load_outline_polys([OTHER])

    page.load_outline_polys([OUTER], source_label="Draft selection", offer_undo=True)
    assert page._undo_transfer_btn.isVisibleTo(page)

    # Loading/preview settling and view-only changes keep the offer alive.
    page._canvas.fit()
    page._emit_state_changed()
    assert page._undo_transfer_btn.isVisibleTo(page)

    # A real content edit makes the snapshot stale: restoring it would erase it.
    page._treatments = {page._outline_ids[0]: dict(TREATMENT)}
    page._emit_state_changed()
    assert not page._undo_transfer_btn.isVisibleTo(page)

    page._undo_outline_transfer()
    assert page._edit_polys == [OUTER]  # later work was not overwritten
    assert page._treatments


def test_undo_transfer_still_restores_untouched_previous_outline(qtbot) -> None:
    page = PatternPage(settings={})
    qtbot.addWidget(page)
    page.show()
    page.load_outline_polys([OTHER])
    page.load_outline_polys([OUTER], offer_undo=True)

    page._undo_outline_transfer()

    assert page._edit_polys == [OTHER]
    assert not page._undo_transfer_btn.isVisibleTo(page)


# ── Repository: in-flight feedback for the operation and each step ────────────


class _GatedProcess:
    """Stand-in for a git subprocess that finishes only when released."""

    gates: list[threading.Event] = []
    launched: list[list[str]] = []

    def __init__(self, command, **_kwargs) -> None:
        self.returncode = 0
        self._gate = self.gates[len(self.launched)]
        self.launched.append(list(command))

    def communicate(self, timeout=None):
        assert self._gate.wait(10), "test never released the fake git step"
        return "done", ""

    def poll(self):
        return None if not self._gate.is_set() else 0

    def terminate(self) -> None:
        self._gate.set()

    def kill(self) -> None:
        self._gate.set()


def test_git_operation_reports_operation_and_steps_while_running(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    page = RepoPage(settings={"repo_dir": str(tmp_path)})
    qtbot.addWidget(page)
    monkeypatch.setattr(page, "_repo_dir", lambda *, show_dialogs=True: tmp_path)
    monkeypatch.setattr(page, "_refresh_branch_choices", lambda *_a, **_k: None)
    monkeypatch.setattr(repo_module, "find_git", lambda: "git")
    gates = [threading.Event(), threading.Event()]
    _GatedProcess.gates = gates
    _GatedProcess.launched = []
    monkeypatch.setattr(repo_module.subprocess, "Popen", _GatedProcess)
    finished: list[list] = []

    try:
        assert page._run_git_async([["fetch", "origin"], ["status"]], finished.append)

        # Feedback exists before any worker output: busy, cancellable, named.
        assert page._git_busy
        assert page._cancel_btn.isEnabled()
        assert page._repo_status.text() == "Running git fetch (step 1 of 2)…"

        # The command is echoed into the log while it is still running.
        qtbot.waitUntil(lambda: "$ git fetch origin" in page._log.toPlainText(), timeout=5000)
        assert page._git_busy and not finished

        gates[0].set()
        qtbot.waitUntil(lambda: "step 2 of 2" in page._repo_status.text(), timeout=5000)
        assert "$ git status" in page._log.toPlainText()
        assert page._git_busy and not finished

        gates[1].set()
        qtbot.waitUntil(lambda: not page._git_busy, timeout=5000)
        log = page._log.toPlainText()
        assert log.count("$ git fetch origin") == 1  # no duplicated header
        assert log.count("$ git status") == 1
        assert "done" in log
        assert finished
        assert page._repo_status.text().startswith("Ready")
    finally:
        for gate in gates:
            gate.set()
        page.shutdown()
