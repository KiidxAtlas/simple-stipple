from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from simple_stipple.ui.dialogs.export_preflight import export_preflight

OPEN_LINE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]


@dataclass
class _Answers:
    queued: list[str] = field(default_factory=list)
    shown: list[list[str]] = field(default_factory=list)


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> _Answers:
    """Answer each warning box with the next queued button label."""
    state = _Answers()

    def fake_exec(box: QMessageBox) -> int:
        labels = [button.text() for button in box.buttons()]
        state.shown.append(labels)
        box.setProperty("_test_choice", labels.index(state.queued.pop(0)))
        return 0

    def fake_clicked(box: QMessageBox):
        return box.buttons()[box.property("_test_choice")]

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", fake_clicked)
    return state


def test_accepted_warning_is_skipped_until_geometry_changes(app, answers) -> None:
    parent = QWidget()
    answers.queued.append("Export Anyway")
    proceed, _ = export_preflight(
        parent, [SQUARE, OPEN_LINE], action="Export", allow_open_paths=False
    )
    assert proceed
    assert len(answers.shown) == 1

    # Same geometry (float noise below the fingerprint rounding) and action: no prompt.
    noisy = [[(x + 1e-9, y) for x, y in SQUARE], OPEN_LINE]
    proceed, _ = export_preflight(parent, noisy, action="Export", allow_open_paths=False)
    assert proceed
    assert len(answers.shown) == 1

    # A different action is a different decision.
    answers.queued.append("Send Anyway")
    proceed, _ = export_preflight(
        parent, [SQUARE, OPEN_LINE], action="Send", allow_open_paths=False
    )
    assert proceed
    assert len(answers.shown) == 2

    # Edited geometry invalidates the acknowledgment.
    moved = [SQUARE, [(0.0, 0.0), (12.0, 0.0), (10.0, 10.0)]]
    answers.queued.append("Return to Drawing")
    proceed, _ = export_preflight(parent, moved, action="Export", allow_open_paths=False)
    assert not proceed
    assert len(answers.shown) == 3


def test_show_issues_reports_blocking_paths_and_cancels(app, answers) -> None:
    parent = QWidget()
    reported: list[list[int]] = []
    answers.queued.append("Show Issues")
    proceed, _ = export_preflight(
        parent,
        [SQUARE, OPEN_LINE, SQUARE],
        action="Export",
        allow_open_paths=False,
        on_show_issues=reported.append,
    )
    assert not proceed
    # Open path 1 blocks the export; path 2 duplicates path 0.
    assert reported == [[1, 2]]

    # Open paths are not reported when the export accepts them.
    answers.queued.append("Show Issues")
    export_preflight(
        parent,
        [SQUARE, OPEN_LINE, SQUARE],
        action="Export",
        allow_open_paths=True,
        on_show_issues=reported.append,
    )
    assert reported[-1] == [2]

    # Without the callback the button is not offered.
    answers.queued.append("Return to Drawing")
    export_preflight(parent, [SQUARE, OPEN_LINE], action="Export", allow_open_paths=False)
    assert "Show Issues" not in answers.shown[-1]
