"""Repository tab — basic Git pull/commit/push workflow."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
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

from src.settings import save_settings
from src.ui.components.helpers import _surface_frame


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
        self._dir_edit.textChanged.connect(self._refresh_repo_state)
        dir_row.addWidget(self._dir_edit, stretch=1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_repo_dir)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        self._repo_status = QLabel("")
        self._repo_status.setStyleSheet("color: #8b949e; font-size: 11px;")
        self._repo_status.setWordWrap(True)
        layout.addWidget(self._repo_status)

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

        self._open_btn = QPushButton("Open Folder")
        self._open_btn.clicked.connect(self._open_repo_dir)
        actions.addWidget(self._open_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Git command output appears here.")
        layout.addWidget(self._log, stretch=1)

        root.addWidget(panel, stretch=1)
        self._refresh_repo_state()

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
        save_settings(self._settings)
        self._emit_state_changed()

    def _repo_dir(self, *, show_dialogs: bool = True) -> Path | None:
        text = self._dir_edit.text().strip()
        if not text:
            if show_dialogs:
                QMessageBox.information(
                    self, "Repository", "Select a repository directory first."
                )
            return None
        p = Path(text)
        if not p.exists() or not p.is_dir():
            if show_dialogs:
                QMessageBox.warning(self, "Repository", "Directory does not exist.")
            return None
        if not (p / ".git").exists():
            if show_dialogs:
                QMessageBox.warning(
                    self, "Repository", "Selected directory is not a git repository."
                )
            return None
        return p

    def _refresh_repo_state(self) -> None:
        repo = self._repo_dir(show_dialogs=False)
        ready = repo is not None
        if not self._dir_edit.text().strip():
            message = "Choose a repository folder to enable git actions."
            color = "#8b949e"
        elif ready:
            message = f"Ready — {repo.name} is a valid git repository."
            color = "#3fb950"
        else:
            message = "Selected folder is missing or is not a git repository."
            color = "#f85149"
        self._repo_status.setText(message)
        self._repo_status.setStyleSheet(f"color: {color}; font-size: 11px;")
        for button in (
            self._status_btn,
            self._pull_btn,
            self._commit_btn,
            self._push_btn,
            self._open_btn,
        ):
            button.setEnabled(ready)

    def _open_repo_dir(self) -> None:
        repo = self._repo_dir(show_dialogs=False)
        if repo is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(repo)))

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
        if not isinstance(state, dict):
            state = {}
        self._dir_edit.setText(str(state.get("repo_dir", "")))
        self._commit_msg.setText(str(state.get("commit_msg", "Update project files")))
        self._refresh_repo_state()

    def sync_from_settings(self) -> None:
        """Refresh the repo dir field from the current global settings dict."""
        self._dir_edit.setText(self._settings.get("repo_dir", ""))
        self._refresh_repo_state()

    def clear_workspace_state(self) -> None:
        self._dir_edit.setText(self._settings.get("repo_dir", ""))
        self._commit_msg.setText("Update project files")
        self._refresh_repo_state()

    def get_preset_state(self) -> dict[str, dict]:
        return {}

    def apply_preset_state(self, presets: dict[str, dict]) -> None:
        _ = presets

    def auto_fetch(self) -> bool:
        """Silently fetch from remote repository (for auto-fetch on startup).

        Returns True if successful, False otherwise.
        Does not show message boxes — only logs to the git output panel.
        """
        repo = self._repo_dir()
        if repo is None:
            return False
        try:
            proc = subprocess.run(
                ["git", "fetch", "--all", "--prune"],
                cwd=str(repo),
                text=True,
                capture_output=True,
                check=False,
                timeout=15,  # 15-second timeout for network operations
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        out = out.strip()
        if out:
            self._log.appendPlainText(f"$ git fetch --all --prune (auto)\n{out}\n")
        else:
            self._log.appendPlainText("$ git fetch --all --prune (auto)\n(done)\n")
        self._emit_state_changed()
        return proc.returncode == 0
