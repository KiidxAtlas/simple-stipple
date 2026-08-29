"""Bounded smoke tests for startup, shutdown, and release metadata."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SUBPROCESS_ENV = {
    **os.environ,
    "PYTHONPATH": str(ROOT / "src"),
    "QT_QPA_PLATFORM": "offscreen",
}


def _run_python(source: str) -> subprocess.CompletedProcess[str]:
    """Run a GUI lifecycle probe with a hard bound so CI cannot hang."""
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=SUBPROCESS_ENV,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize(
    "source",
    [
        """
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from simple_stipple.app.window import App

app = QApplication([])
window = App()
window.show()
QTimer.singleShot(25, window.close)
QTimer.singleShot(75, app.quit)
raise SystemExit(app.exec())
""",
        """
from argparse import Namespace
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from simple_stipple.app.launcher import run_app
from simple_stipple.app.window import App

original_show = App.show
def show_then_stop(window):
    original_show(window)
    QTimer.singleShot(25, window.close)
    QTimer.singleShot(75, QApplication.instance().quit)

App.show = show_then_stop
raise SystemExit(run_app(Namespace(allow_multi_instance=True)))
""",
    ],
    ids=("application", "launcher"),
)
def test_startup_and_shutdown_complete(source: str) -> None:
    result = _run_python(source)
    assert result.returncode == 0, result.stderr


def test_release_metadata_check_is_non_mutating() -> None:
    result = subprocess.run(
        ["bash", "scripts/release.sh", "--check", "v0.3.7"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Release metadata is consistent for v0.3.7." in result.stdout
