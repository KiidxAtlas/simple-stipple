"""Repository tab — basic Git pull/commit/push workflow."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.helpers import _surface_frame


class RepoTab(QWidget):
    stateChanged = Signal()

    def __init__(self, parent: QWidget | None = None, settings: dict | None = None):
        super().__init__(parent)
        self._settings: dict = settings or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(0)

        panel = _surface_frame("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Repository directory"))
        self._dir_edit = QLineEdit(self._settings.get("repo_dir", ""))
        self._dir_edit.setPlaceholderText("Select a git repository folder")
        self._dir_edit.textChanged.connect(self._emit_state_changed)
        dir_row.addWidget(self._dir_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_repo_dir)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        msg_row = QHBoxLayout()
        msg_row.addWidget(QLabel("Commit message"))
        self._commit_msg = QLineEdit("Update project files")
        self._commit_msg.textChanged.connect(self._emit_state_changed)
        msg_row.addWidget(self._commit_msg, stretch=1)
        layout.addLayout(msg_row)

        actions = QHBoxLayout()
        self._status_btn = QPushButton("Status")
        self._status_btn.clicked.connect(self._git_status)
        actions.addWidget(self._status_btn)

        self._pull_btn = QPushButton("Pull")
        self._pull_btn.clicked.connect(self._git_pull)
        actions.addWidget(self._pull_btn)

        self._commit_btn = QPushButton("Commit")
        self._commit_btn.clicked.connect(self._git_commit)
        actions.addWidget(self._commit_btn)

        self._push_btn = QPushButton("Push")
        self._push_btn.clicked.connect(self._git_push)
        actions.addWidget(self._push_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Git command output appears here.")
        layout.addWidget(self._log, stretch=1)

        root.addWidget(panel, stretch=1)

    def _emit_state_changed(self) -> None:
        self.stateChanged.emit()

    def _browse_repo_dir(self) -> None:
        start = self._dir_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "Select repository directory", start
        )
        if not path:
            return
        self._dir_edit.setText(path)
        self._settings["repo_dir"] = path
        self._emit_state_changed()

    def _repo_dir(self) -> Path | None:
        text = self._dir_edit.text().strip()
        if not text:
            QMessageBox.information(
                self, "Repository", "Select a repository directory first."
            )
            return None
        p = Path(text)
        if not p.exists() or not p.is_dir():
            QMessageBox.warning(self, "Repository", "Directory does not exist.")
            return None
        if not (p / ".git").exists():
            QMessageBox.warning(
                self, "Repository", "Selected directory is not a git repository."
            )
            return None
        return p

    def _run_git(self, args: list[str]) -> tuple[bool, str]:
        repo = self._repo_dir()
        if repo is None:
            return False, ""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(repo),
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            QMessageBox.critical(self, "Git", str(exc))
            return False, ""

        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        out = out.strip()
        if out:
            self._log.appendPlainText(f"$ git {' '.join(args)}\n{out}\n")
        else:
            self._log.appendPlainText(f"$ git {' '.join(args)}\n(done)\n")
        self._emit_state_changed()
        return proc.returncode == 0, out

    def _git_status(self) -> None:
        self._run_git(["status", "--short", "--branch"])

    def _git_pull(self) -> None:
        self._run_git(["pull"])

    def _git_commit(self) -> None:
        ok, _ = self._run_git(["add", "-A"])
        if not ok:
            return
        msg = self._commit_msg.text().strip() or "Update project files"
        ok, out = self._run_git(["commit", "-m", msg])
        if not ok and "nothing to commit" in out.lower():
            QMessageBox.information(self, "Commit", "Nothing to commit.")

    def _git_push(self) -> None:
        self._run_git(["push"])

    def get_workspace_state(self) -> dict:
        return {
            "repo_dir": self._dir_edit.text(),
            "commit_msg": self._commit_msg.text(),
        }

    def apply_workspace_state(self, state: dict | None) -> None:
        state = state or {}
        self._dir_edit.setText(str(state.get("repo_dir", "")))
        self._commit_msg.setText(str(state.get("commit_msg", "Update project files")))

    def clear_workspace_state(self) -> None:
        self._dir_edit.setText("")
        self._commit_msg.setText("Update project files")

    def get_preset_state(self) -> dict[str, dict]:
        return {}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        _ = presets
