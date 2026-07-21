"""Update dialogs must not destroy network threads that are still running."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class _SlowCheckThread(QThread):
    checkComplete = Signal(object)

    def run(self) -> None:
        self.msleep(100)
        self.checkComplete.emit(None)


def test_closing_update_dialog_detaches_running_check(qapp, monkeypatch):
    from src.ui.widgets.dialogs import update_dialog

    monkeypatch.setattr(update_dialog, "UpdateCheckThread", _SlowCheckThread)
    dialog = update_dialog.UpdateDialog()
    thread = dialog._check_thread
    assert thread is not None
    assert thread.isRunning()

    dialog.close()
    qapp.processEvents()

    assert dialog._check_thread is None
    assert thread.parent() is None
    assert thread in update_dialog._DETACHED_THREADS
    assert thread.wait(1_000)
    qapp.processEvents()
    assert thread not in update_dialog._DETACHED_THREADS
